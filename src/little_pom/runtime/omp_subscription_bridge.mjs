// A provider call, deliberately NOT an OMP/Pi agent or session.
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

export const PROTOCOL = 'little-pom-pi-subscription/1';
export const ROLE_SCHEMA_POLICY = JSON.parse(
  await readFile(new URL('./subscription_policy.json', import.meta.url), 'utf8'));
const VERSION = '1.5.0';
const ENDPOINT = 'https://chatgpt.com/backend-api/codex/responses';
const sensitive = new Set(['authorization', 'api_key', 'apikey', 'access', 'access_token',
  'refresh', 'refresh_token', 'password', 'secret', 'credentials', 'cookie', 'set-cookie']);

export function redactor(secrets) {
  return function clean(value) {
    if (typeof value === 'string') {
      for (const secret of secrets) if (secret) value = value.replaceAll(secret, '[REDACTED]');
      return value;
    }
    if (Array.isArray(value)) return value.map(clean);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(
      ([key, item]) => [clean(key), sensitive.has(key.toLowerCase()) ? '[REDACTED]' : clean(item)]));
    return value;
  };
}

function metadata(extra = {}) {
  return { bridge_protocol: PROTOCOL, sdk_package: '@earendil-works/pi-ai', sdk_version: VERSION,
    node_version: process.version, raw_observation: 'parsed_provider_sse_events_and_pi_messages_not_wire_bytes',
    max_retries: 0, transport: 'sse', ...extra };
}

function validate(payload) {
  const request = payload.request;
  const policy = Object.hasOwn(ROLE_SCHEMA_POLICY, request?.stage ?? '') ? ROLE_SCHEMA_POLICY[request.stage] : null;
  if (payload.protocol !== PROTOCOL || !policy || payload.schema_id !== policy.schema ||
      !policy.models.includes(request?.model) ||
      !request.config || Object.keys(request.config).length !== 1 || request.config.reasoning_effort !== 'medium' ||
      !Number.isSafeInteger(request.output_budget) || request.output_budget < 1 ||
      request.output_budget > payload.model?.maxTokens ||
      `openai-codex/${payload.model?.id}` !== request.model || payload.model.provider !== 'openai-codex' ||
      payload.model.api !== 'openai-codex-responses' ||
      payload.model.thinkingLevelMap?.medium !== 'medium' ||
      payload.model.baseUrl !== 'https://chatgpt.com/backend-api' ||
      typeof request.instructions !== 'string' || !request.instructions.trim() ||
      !request.packet || typeof request.packet !== 'object' || !Array.isArray(request.history)) {
    throw new Error('invalid_request');
  }
  upstreamContext(request, payload.model);
}

function upstreamContext(request, model) {
  if (request.stage !== 'scientific-judge') {
    if (Object.hasOwn(request, 'upstream_messages')) throw new Error('upstream_messages_role_mismatch');
    return null;
  }
  if (Object.keys(request.packet).length || request.history.length ||
      !Array.isArray(request.upstream_messages) || request.upstream_messages.length !== 1) {
    throw new Error('invalid_upstream_messages');
  }
  const message = request.upstream_messages[0];
  if (!message || Object.keys(message).sort().join(',') !== 'content,role' || message.role !== 'user' ||
      !Array.isArray(message.content) || !message.content.length) throw new Error('invalid_upstream_message');
  const content = message.content.map(block => {
    if (block?.type === 'text' && Object.keys(block).sort().join(',') === 'text,type' &&
        typeof block.text === 'string') return { type: 'text', text: block.text };
    const source = block?.source;
    if (block?.type !== 'image' || Object.keys(block).sort().join(',') !== 'source,type' ||
        !source || Object.keys(source).sort().join(',') !== 'data,media_type,type' ||
        source.type !== 'base64' || !['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(source.media_type) ||
        typeof source.data !== 'string' || !source.data ||
        Buffer.from(source.data, 'base64').toString('base64') !== source.data || !model.input?.includes('image')) {
      throw new Error('invalid_upstream_image');
    }
    return { type: 'image', data: source.data, mimeType: source.media_type };
  });
  const marker = '## Rubric criteria\n';
  const first = content[0];
  if (first.type !== 'text' || request.instructions.includes(marker) ||
      first.text.split(marker).length !== 2 || !first.text.startsWith(request.instructions + marker)) {
    throw new Error('invalid_upstream_guidance_split');
  }
  // Required nonempty Codex instructions are exact trusted upstream guidance.
  // Removing that prefix here keeps every original text byte exactly once.
  first.text = first.text.slice(request.instructions.length);
  return { messages: [{ role: 'user', content, timestamp: 0 }] };
}

function safeRejection(status, text) {
  if (status === 400) {
    if (/instructions? (?:are |is )?required|instructions?.{0,30}(?:empty|missing)/i.test(text)) {
      return 'subscription_instructions_required';
    }
    if (/unsupported|not supported|unknown|unrecognized/i.test(text)) {
      for (const parameter of ['max_output_tokens', 'truncation']) {
        if (text.includes(parameter)) return `unsupported_${parameter}`;
      }
    }
    if (/context_length_exceeded|maximum context length|context window/i.test(text)) {
      return 'subscription_context_rejected';
    }
    if (/invalid_image|image.{0,30}(?:too large|unsupported|invalid)/i.test(text)) {
      return 'subscription_image_rejected';
    }
  }
  if (status === 401 || status === 403) return 'subscription_auth_rejected';
  if (status === 429) return 'subscription_rate_limited';
  return 'subscription_http_rejected';
}

// Exported for offline, injected-provider regression coverage. Production imports
// only Pi's low-level stream implementation; there is no tool executor here.
export async function runTurn(payload, { stream, apiKey, fetchImpl = globalThis.fetch, clean = value => value }) {
  validate(payload);
  const request = payload.request;
  const providerEvents = [];
  const messageEvents = [];
  const responses = [];
  let message = null;
  let sentPayload = null;
  let dispatches = 0;
  let rejection = null;
  let terminal = null;
  const controller = new AbortController();
  const timer = payload.timeout_ms === null ? null : setTimeout(() => controller.abort(), payload.timeout_ms);
  const upstream = upstreamContext(request, payload.model);
  const context = upstream ?? { messages: [
    { role: 'system', content: request.instructions, timestamp: 0 },
    { role: 'user', content: [{ type: 'text', text: JSON.stringify({ packet: request.packet, history: request.history }) }], timestamp: 0 },
  ] };
  try {
    const events = stream(payload.model, context, {
      apiKey, transport: 'sse', maxRetries: 0, reasoningEffort: 'medium',
      maxTokens: request.output_budget, cacheRetention: 'none',
      toolChoice: 'none', signal: controller.signal,
      ...(payload.timeout_ms === null ? {} : { timeoutMs: payload.timeout_ms }),
      onPayload(body) {
        // Pi 1.5.0 omits maxTokens for Codex. Never silently discard the cap.
        // If the subscription endpoint rejects this field, record that rejection.
        const result = { ...body, max_output_tokens: request.output_budget,
          tools: [], tool_choice: 'none', parallel_tool_calls: false };
        delete result.prompt_cache_key;
        if (upstream) {
          // No generic provider prompt: promote only the exact upstream guidance
          // prefix; scientific artifacts remain user data with real image bytes.
          result.instructions = request.instructions;
          result.input = upstream.messages.map(message => ({ role: 'user',
            content: message.content.map(block => block.type === 'text'
              ? { type: 'input_text', text: block.text }
              : { type: 'input_image', detail: 'auto',
                image_url: `data:${block.mimeType};base64,${block.data}` }) }));
          // Codex does not implement the public Responses truncation parameter.
          // We never truncate client input; server acceptance remains observable.
          delete result.truncation;
        }
        if (result.model !== payload.model.id || result.store !== false || result.stream !== true ||
            result.reasoning?.effort !== 'medium' || result.previous_response_id !== undefined) {
          throw new Error('provider_payload_contract_changed');
        }
        sentPayload = clean(structuredClone(result));
        return result;
      },
      async fetch(url, init) {
        if (String(url) !== ENDPOINT || init?.method !== 'POST' || dispatches !== 0) {
          throw new Error('generation_dispatch_contract_violation');
        }
        dispatches++;
        // No automatic redirect can turn a single allowed POST into more sends.
        const response = await fetchImpl(url, { ...init, redirect: 'error' });
        responses.push({ status: response.status });
        if (!response.ok) {
          // Keep status + allowlisted diagnosis, not an HTTP error body/header.
          rejection = safeRejection(response.status, await response.clone().text());
        }
        return response;
      },
      onProviderStreamEvent(event) {
        providerEvents.push(clean(structuredClone(event)));
        if (['response.completed', 'response.done', 'response.incomplete', 'response.failed'].includes(event.type)) {
          terminal = clean(structuredClone(event));
        }
      },
    });
    for await (const event of events) {
      const snapshot = structuredClone(event);
      if (rejection && snapshot.error) snapshot.error.errorMessage = rejection;
      messageEvents.push(clean(snapshot));
      if (event.type === 'done') message = clean(structuredClone(event.message));
      if (event.type === 'error') {
        message = clean(structuredClone(event.error));
        if (rejection && message) message.errorMessage = rejection;
      }
    }
  } catch {
    // Partial observations survive stream errors; never dispatch another sample.
    rejection ??= controller.signal.aborted ? 'subscription_timeout' : 'subscription_stream_failed';
  } finally {
    clearTimeout(timer);
  }
  const response = terminal?.response ?? null;
  const complete = dispatches === 1 && !rejection && message?.stopReason === 'stop' &&
    ['response.completed', 'response.done'].includes(terminal?.type) &&
    response?.status === 'completed' && !response.error && !response.incomplete_details;
  const outputMessages = (response?.output ?? []).filter(item => item.type === 'message' && item.role === 'assistant');
  const finalMessages = outputMessages.filter(item => item.phase === 'final_answer');
  const answerMessages = finalMessages.length ? finalMessages : outputMessages.filter(item => !item.phase);
  const text = outputMessages.length
    ? answerMessages.flatMap(item => item.content ?? []).filter(item => item.type === 'output_text')
      .map(item => item.text ?? '').join('')
    : (message?.content ?? []).filter(item => item.type === 'text').map(item => item.text).join('');
  return clean({
    id: response?.id ?? null, model: response?.model ?? request.model,
    status: complete ? 'completed' : 'incomplete', output_text: text,
    usage: response?.usage ?? null,
    incomplete_details: complete ? null : { reason: rejection ??
      response?.incomplete_details?.reason ?? (terminal?.type === 'response.incomplete' ? 'provider_incomplete' :
        message?.stopReason === 'length' ? 'max_output_tokens' : 'subscription_stream_incomplete') },
    error: rejection ? { code: rejection, http_status: responses.at(-1)?.status ?? null } : response?.error ?? null,
    provider_response: response, provider_events: providerEvents,
    pi_message: message, pi_message_events: messageEvents,
    _transport_metadata: metadata({ generation_dispatches: dispatches, http_responses: responses,
      sent_payload: sentPayload, observation_complete: terminal !== null }),
  });
}

async function main() {
  const input = [];
  for await (const chunk of process.stdin) input.push(chunk);
  const payload = JSON.parse(Buffer.concat(input).toString('utf8'));
  validate(payload);
  const nodeVersion = process.versions.node.split('.').map(Number);
  if (nodeVersion[0] < 22 || (nodeVersion[0] === 22 && nodeVersion[1] < 19)) throw new Error('node_version_unsupported');
  const load = path => import(pathToFileURL(path).href);
  for (const root of [payload.pi_ai_dir, payload.pi_agent_dir]) {
    if (JSON.parse(await readFile(resolve(root, 'package.json'), 'utf8')).version !== VERSION) throw new Error('pi_version_changed');
  }
  const catalog = JSON.parse(await readFile(resolve(payload.pi_ai_dir, 'dist/providers/data/openai-codex.json'), 'utf8'));
  // Compare values, not object insertion order (Python serializes canonical JSON).
  const expectedModel = catalog['openai-codex-responses'][`chat:${payload.model.id}`];
  const canonical = value => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') return Object.fromEntries(
      Object.keys(value).sort().map(key => [key, canonical(value[key])]));
    return value;
  };
  if (!expectedModel || JSON.stringify(canonical(expectedModel)) !== JSON.stringify(canonical(payload.model))) {
    throw new Error('catalog_changed');
  }
  const stored = JSON.parse(await readFile(payload.auth_file, 'utf8'))['openai-codex'];
  if (stored?.type !== 'oauth' || !stored.access || !stored.refresh || !Number.isFinite(stored.expires)) {
    throw new Error('connected_pi_oauth_required');
  }
  const secrets = [stored.access, stored.refresh];
  const clean = redactor(secrets);
  const { AuthStorage } = await load(resolve(payload.pi_agent_dir, 'dist/core/auth-storage.js'));
  const { openaiCodexOAuth } = await load(resolve(payload.pi_ai_dir, 'dist/auth/oauth/openai-codex.js'));
  const { refreshStoredOAuthCredential } = await load(resolve(payload.pi_ai_dir, 'dist/auth/resolve.js'));
  const store = AuthStorage.create(payload.auth_file);
  let credential = stored;
  if (Date.now() + 300_000 >= stored.expires) {
    // Official lock/recheck/refresh/persist path protects concurrent workers.
    // This is auth maintenance, not model generation, and has no API-key fallback.
    credential = await refreshStoredOAuthCredential(store, 'openai-codex', openaiCodexOAuth,
      current => Date.now() + 300_000 >= current.expires, new AbortController().signal);
  }
  if (credential?.type !== 'oauth' || !credential.access) throw new Error('connected_pi_oauth_required');
  secrets.push(credential.access, credential.refresh);
  const { stream } = await load(resolve(payload.pi_ai_dir, 'dist/api/openai-codex-responses.js'));
  const result = await runTurn(payload, { stream, apiKey: credential.access, clean });
  process.stdout.write(JSON.stringify(result));
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch(() => {
    // Never print exception stacks, auth contents, or stderr from dependency code.
    process.stdout.write(JSON.stringify({ status: 'incomplete', model: null, output_text: '', usage: null,
      error: { code: 'subscription_setup_failed' }, incomplete_details: { reason: 'subscription_setup_failed' },
      _transport_metadata: metadata({ generation_dispatches: 0 }) }));
    process.exitCode = 0;
  });
}
