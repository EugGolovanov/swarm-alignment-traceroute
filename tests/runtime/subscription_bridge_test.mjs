import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { zstdDecompressSync } from 'node:zlib';
import { PROTOCOL, ROLE_SCHEMA_POLICY, runTurn, redactor } from '../../src/little_pom/runtime/omp_subscription_bridge.mjs';

const endpoint = 'https://chatgpt.com/backend-api/codex/responses';
function payload() {
  return { protocol: PROTOCOL, schema_id: 'WorkerTurn', execution_id: 'test', timeout_ms: 1000,
    model: { id: 'gpt-6-luna', provider: 'openai-codex', api: 'openai-codex-responses',
      baseUrl: 'https://chatgpt.com/backend-api', maxTokens: 128000, reasoning: true,
      input: ['text'], output: ['text'],
      thinkingLevelMap: { medium: 'medium' }, compat: { supportsMidConvoSystemMessages: true },
      cost: { input: 0.1, output: 0.5, cacheRead: 0.01, cacheWrite: 0.125 } },
    request: { model: 'openai-codex/gpt-6-luna', stage: 'workers', config: { reasoning_effort: 'medium' },
      instructions: 'Only the supplied instructions.', packet: { id: 'packet' },
      history: [{ content: 'local previous turn, not a server session' }], output_budget: 256 },
  };
}
function body() {
  return { model: 'gpt-6-luna', stream: true, store: false, reasoning: { effort: 'medium' } };
}
function message(stopReason = 'stop') {
  return { role: 'assistant', stopReason, content: [{ type: 'text', text: '{"tool_requests":[]}' }] };
}
function terminal(status = 'completed') {
  return { type: `response.${status}`, response: { id: 'provider-id', model: 'gpt-6-luna', status,
    usage: { input_tokens: 7, output_tokens: 5 },
    ...(status === 'incomplete' ? { incomplete_details: { reason: 'max_output_tokens' } } : {}) } };
}

// The fake provider exercises the same options boundary as the real adapter.
test('one isolated medium-effort request with explicit cap and complete event snapshots', async () => {
  let calls = 0;
  const stream = async function* (model, context, options) {
    assert.equal(model.id, 'gpt-6-luna');
    assert.equal(options.maxRetries, 0);
    assert.equal(options.transport, 'sse');
    assert.equal(options.reasoningEffort, 'medium');
    assert.equal(options.maxTokens, 256);
    assert.equal(options.cacheRetention, 'none');
    assert.equal(options.sessionId, undefined);
    assert.equal(options.toolChoice, 'none');
    assert.equal(context.messages.length, 2);
    assert.equal(context.messages[0].content, payload().request.instructions);
    assert.equal(context.messages[0].toolsAdded, undefined);
    assert.deepEqual(JSON.parse(context.messages[1].content[0].text), {
      packet: payload().request.packet, history: payload().request.history });
    const sent = options.onPayload(body());
    assert.equal(sent.max_output_tokens, 256);
    assert.deepEqual(sent.tools, []);
    assert.equal(sent.parallel_tool_calls, false);
    const response = await options.fetch(endpoint, { method: 'POST' });
    assert.equal(response.status, 200);
    const partial = message();
    partial.content[0].text = '{';
    yield { type: 'text_delta', partial, delta: '{' };
    partial.content[0].text = '{"tool_requests":[]}';
    options.onProviderStreamEvent(terminal());
    yield { type: 'done', message: partial, reason: 'stop' };
  };
  const result = await runTurn(payload(), { stream, apiKey: 'synthetic', fetchImpl: async (_url, init) => {
    calls++;
    assert.equal(init.redirect, 'error');
    return new Response('', { status: 200 });
  } });
  assert.equal(calls, 1);
  assert.equal(result.status, 'completed');
  assert.equal(result.output_text, '{"tool_requests":[]}');
  assert.equal(result.pi_message_events[0].partial.content[0].text, '{');
  assert.deepEqual(result.provider_events, [terminal()]);
  assert.deepEqual(result.usage, terminal().response.usage);
  assert.equal(result._transport_metadata.generation_dispatches, 1);
  assert.match(result._transport_metadata.raw_observation, /not_wire_bytes/);
});

test('commentary stays in audit but cannot contaminate the final JSON answer', async () => {
  const answer = '{"tool_requests":[],"done":false}';
  const completed = terminal();
  completed.response.output = [
    { type: 'message', role: 'assistant', phase: 'commentary',
      content: [{ type: 'output_text', text: '{"operation":"list"}\n' }] },
    { type: 'message', role: 'assistant', phase: 'final_answer',
      content: [{ type: 'output_text', text: answer }] },
  ];
  const stream = async function* (_model, _context, options) {
    options.onPayload(body());
    await options.fetch(endpoint, { method: 'POST' });
    options.onProviderStreamEvent(completed);
    const combined = message();
    combined.content[0].text = '{"operation":"list"}\n' + answer;
    yield { type: 'done', message: combined };
  };
  const result = await runTurn(payload(), { stream, apiKey: 'synthetic',
    fetchImpl: async () => new Response('', { status: 200 }) });
  assert.equal(result.output_text, answer);
  assert.deepEqual(JSON.parse(result.output_text), { tool_requests: [], done: false });
  assert.deepEqual(result.provider_response.output, completed.response.output);
  assert.equal(result.pi_message.content[0].text, '{"operation":"list"}\n' + answer);
});

test('provider attempts to retry cannot produce another HTTP dispatch', async () => {
  let calls = 0;
  const stream = async function* (_model, _context, options) {
    options.onPayload(body());
    await options.fetch(endpoint, { method: 'POST' });
    await options.fetch(endpoint, { method: 'POST' });
    yield { type: 'done', message: message() };
  };
  const result = await runTurn(payload(), { stream, apiKey: 'synthetic', fetchImpl: async () => {
    calls++;
    return new Response('', { status: 200 });
  } });
  assert.equal(calls, 1);
  assert.equal(result.status, 'incomplete');
  assert.equal(result.error.code, 'subscription_stream_failed');
});

for (const status of ['incomplete', 'missing-terminal']) {
  test(`${status} preserves partial content without repair or invented usage`, async () => {
    const stream = async function* (_model, _context, options) {
      options.onPayload(body());
      await options.fetch(endpoint, { method: 'POST' });
      if (status === 'incomplete') options.onProviderStreamEvent(terminal('incomplete'));
      yield { type: 'done', message: message(status === 'incomplete' ? 'length' : 'stop') };
    };
    const result = await runTurn(payload(), { stream, apiKey: 'synthetic', fetchImpl: async () => new Response('') });
    assert.equal(result.status, 'incomplete');
    assert.equal(result.output_text, '{"tool_requests":[]}');
    assert.equal(result._transport_metadata.generation_dispatches, 1);
    if (status === 'missing-terminal') assert.equal(result.usage, null);
    else assert.equal(result.incomplete_details.reason, 'max_output_tokens');
  });
}

test('unsupported cap records a safe HTTP 400 diagnosis and never drops the cap', async () => {
  let calls = 0;
  const stream = async function* (_model, _context, options) {
    options.onPayload(body());
    await options.fetch(endpoint, { method: 'POST' });
    yield { type: 'error', error: { ...message('error'), errorMessage: 'private HTTP body' } };
  };
  const result = await runTurn(payload(), { stream, apiKey: 'synthetic', fetchImpl: async () => {
    calls++;
    return new Response('Unsupported parameter: max_output_tokens, private HTTP body', { status: 400 });
  } });
  assert.equal(calls, 1);
  assert.equal(result.error.code, 'unsupported_max_output_tokens');
  assert.equal(result.error.http_status, 400);
  assert.equal(result._transport_metadata.sent_payload.max_output_tokens, 256);
  assert.equal(JSON.stringify(result).includes('private HTTP body'), false);
});

test('credential redaction covers values, object keys, and structured secret fields', () => {
  const clean = redactor(['synthetic-access', 'synthetic-refresh']);
  assert.deepEqual(clean({ 'synthetic-access': 'echo synthetic-refresh', authorization: 'other',
    nested: [{ access_token: 'third', text: 'safe' }] }), {
    '[REDACTED]': 'echo [REDACTED]', authorization: '[REDACTED]',
    nested: [{ access_token: '[REDACTED]', text: 'safe' }],
  });
});

// Exercise the reviewed installed provider's own retry/stream code with an
// in-memory fetch. No OAuth file is read and no live request is possible.
const providerPath = resolve(homedir(), '.pi/agent/install/releases/1.5.0/node_modules',
  '@earendil-works/pi-ai/dist/api/openai-codex-responses.js');
const fakeJwt = `fake.${Buffer.from(JSON.stringify({
  'https://api.openai.com/auth': { chatgpt_account_id: 'synthetic-account' },
})).toString('base64url')}.fake`;

for (const [modelId, status] of ['gpt-6-luna', 'gpt-6.1-terra'].flatMap(
    id => [200, 400, 429, 503].map(status => [id, status]))) {
  test(`installed Pi provider consumes ${modelId} stream once (HTTP ${status})`, { skip: !existsSync(providerPath) }, async () => {
    const { stream } = await import(pathToFileURL(providerPath).href);
    let calls = 0;
    const completed = terminal();
    completed.response.model = modelId;
    const selected = payload();
    selected.model.id = modelId;
    selected.request.model = `openai-codex/${modelId}`;
    const item = { type: 'message', id: 'msg-id', role: 'assistant', phase: 'final_answer',
      content: [{ type: 'output_text', text: '{"tool_requests":[]}' }] };
    const events = [{ type: 'response.output_item.done', output_index: 0, item }, completed];
    const result = await runTurn(selected, { stream, apiKey: fakeJwt, fetchImpl: async (_url, init) => {
      calls++;
      assert.equal(init.redirect, 'error');
      return status === 200 ? new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''),
        { headers: { 'content-type': 'text/event-stream' } }) :
        new Response('Unsupported parameter: max_output_tokens', { status });
    } });
    assert.equal(calls, 1);
    assert.equal(result._transport_metadata.generation_dispatches, 1);
    assert.equal(result._transport_metadata.sent_payload.max_output_tokens, 256);
    assert.equal(result._transport_metadata.sent_payload.model, modelId);
    if (status === 200) {
      assert.equal(result.status, 'completed');
      assert.equal(result.output_text, '{"tool_requests":[]}');
      assert.deepEqual(result.provider_events, events);
    } else {
      assert.equal(result.status, 'incomplete');
      assert.equal(result.error.http_status, status);
      assert.equal(result.usage, null);
    }
  });
}

test('installed Pi provider retains incomplete response and transport EOF', { skip: !existsSync(providerPath) }, async () => {
  const { stream } = await import(pathToFileURL(providerPath).href);
  for (const includeTerminal of [true, false]) {
    let calls = 0;
    const events = [{ type: 'response.output_item.done', output_index: 0,
      item: { type: 'message', id: 'msg', role: 'assistant', content: [{ type: 'output_text', text: '{"partial":' }] } }];
    if (includeTerminal) events.push(terminal('incomplete'));
    const result = await runTurn(payload(), { stream, apiKey: fakeJwt, fetchImpl: async () => {
      calls++;
      return new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''));
    } });
    assert.equal(calls, 1);
    assert.equal(result.status, 'incomplete');
    assert.equal(result.output_text, '{"partial":');
    assert.deepEqual(result.provider_events, events);
    if (includeTerminal) assert.equal(result.incomplete_details.reason, 'max_output_tokens');
    else assert.equal(result.usage, null);
  }
});

for (const [stage, policy] of Object.entries(ROLE_SCHEMA_POLICY)) {
  for (const selector of policy.models) {
    test(`${stage}/${selector} permits one dispatch and blocks provider retries`, async () => {
      const selected = payload();
      selected.schema_id = policy.schema;
      selected.request.stage = stage;
      selected.request.model = selector;
      selected.model.id = selector.split('/')[1];
      if (stage === 'scientific-judge') {
        Object.assign(selected.request, { instructions: 'Original grading guidance.\n\n', packet: {}, history: [],
          upstream_messages: [{ role: 'user', content: [{ type: 'text', text: 'Original grading guidance.\n\n## Rubric criteria\nOriginal rubric and artifacts.' }] }] });
      }
      let calls = 0;
      const stream = async function* (model, _context, options) {
        assert.equal(options.maxRetries, 0);
        assert.equal(options.reasoningEffort, 'medium');
        const sent = options.onPayload({ ...body(), model: model.id });
        assert.equal(sent.max_output_tokens, selected.request.output_budget);
        await options.fetch(endpoint, { method: 'POST' });
        await options.fetch(endpoint, { method: 'POST' });
        yield { type: 'done', message: message() };
      };
      const result = await runTurn(selected, { stream, apiKey: 'synthetic', fetchImpl: async () => {
        calls++;
        return new Response('', { status: 200 });
      } });
      assert.equal(calls, 1);
      assert.equal(result.status, 'incomplete');
      assert.equal(result.error.code, 'subscription_stream_failed');
    });
  }
}

for (const mutate of [
  selected => { selected.request.stage = 'author'; },
  selected => { selected.schema_id = 'Review'; },
  selected => { selected.request.model = 'openai-codex/gpt-6.1-terra'; },
  selected => { selected.model.provider = 'openai'; },
  selected => { selected.model.thinkingLevelMap.medium = 'high'; },
  selected => { selected.request.config.temperature = 0; },
  selected => { selected.request.stage = 'evaluation'; selected.schema_id = 'EvalLabel'; },
]) {
  test('role/schema/catalog mismatch fails before provider entry', async () => {
    const selected = payload();
    mutate(selected);
    let entered = false;
    await assert.rejects(runTurn(selected, {
      stream() { entered = true; throw new Error('must not enter provider'); },
      apiKey: 'synthetic',
      fetchImpl() { assert.fail('must not dispatch'); },
    }), /invalid_request/);
    assert.equal(entered, false);
  });
}

test('scientific judge transports exact upstream text and real image bytes without added prompt', async () => {
  const selected = payload();
  selected.schema_id = 'ScientificRubricVerdicts';
  selected.model.id = 'gpt-6-astra';
  selected.model.input = ['text', 'image'];
  const image = Buffer.from('synthetic offline image bytes').toString('base64');
  Object.assign(selected.request, { model: 'openai-codex/gpt-6-astra', stage: 'scientific-judge',
    instructions: 'Exact upstream guidance\n\n', packet: {}, history: [], output_budget: 128000,
    upstream_messages: [{ role: 'user', content: [
      { type: 'text', text: 'Exact upstream guidance\n\n## Rubric criteria\nand scientific artifact.' },
      { type: 'text', text: 'Caption preserved in order.' },
      { type: 'image', source: { type: 'base64', media_type: 'image/png', data: image } },
    ] }] });
  let sent;
  const stream = async function* (model, context, options) {
    assert.equal(context.messages.length, 1);
    assert.equal(context.messages[0].role, 'user');
    assert.deepEqual(context.messages[0].content[2], { type: 'image', data: image, mimeType: 'image/png' });
    sent = options.onPayload({ ...body(), model: model.id, instructions: 'provider default must disappear' });
    await options.fetch(endpoint, { method: 'POST' });
    const done = terminal();
    done.response.model = model.id;
    options.onProviderStreamEvent(done);
    yield { type: 'done', message: message() };
  };
  const result = await runTurn(selected, { stream, apiKey: 'synthetic', fetchImpl: async () => new Response('') });
  assert.equal(result.status, 'completed');
  assert.equal(sent.instructions, selected.request.instructions);
  assert.equal(sent.max_output_tokens, 128000);
  assert.equal(Object.hasOwn(sent, 'truncation'), false);
  assert.equal(sent.instructions + sent.input[0].content[0].text, selected.request.upstream_messages[0].content[0].text);
  assert.deepEqual(sent.input, [{ role: 'user', content: [
    { type: 'input_text', text: '## Rubric criteria\nand scientific artifact.' },
    { type: 'input_text', text: 'Caption preserved in order.' },
    { type: 'input_image', detail: 'auto', image_url: `data:image/png;base64,${image}` },
  ] }]);
  assert.deepEqual(result._transport_metadata.sent_payload, sent);
});

test('installed Pi provider emits real scientific image input on the HTTP boundary', { skip: !existsSync(providerPath) }, async () => {
  const { stream } = await import(pathToFileURL(providerPath).href);
  const selected = payload();
  selected.schema_id = 'ScientificRubricVerdicts';
  selected.model.id = 'gpt-6-astra';
  selected.model.input = ['text', 'image'];
  const image = Buffer.from('offline image transport fixture').toString('base64');
  Object.assign(selected.request, { model: 'openai-codex/gpt-6-astra', stage: 'scientific-judge',
    instructions: 'Full upstream guidance.\n\n', packet: {}, history: [], output_budget: 128000,
    upstream_messages: [{ role: 'user', content: [
      { type: 'text', text: 'Full upstream guidance.\n\n## Rubric criteria\nFull rubric and artifacts.' },
      { type: 'image', source: { type: 'base64', media_type: 'image/png', data: image } },
    ] }] });
  const completed = terminal();
  completed.response.model = selected.model.id;
  const text = '{"criteria_results":[{"id":1,"verdict":"PASS","explanation":"Fixture only."}]}';
  const item = { type: 'message', id: 'msg-id', role: 'assistant', phase: 'final_answer',
    content: [{ type: 'output_text', text }] };
  const events = [{ type: 'response.output_item.done', output_index: 0, item }, completed];
  let calls = 0;
  let actual;
  const result = await runTurn(selected, { stream, apiKey: fakeJwt, fetchImpl: async (_url, init) => {
    calls++;
    // Pi compresses SSE request bodies with zstd when the Node runtime supports
    // it. Decode the real wire body rather than assuming a JSON string.
    const bodyBytes = new Headers(init.headers).get('content-encoding') === 'zstd'
      ? zstdDecompressSync(init.body) : init.body;
    actual = JSON.parse(typeof bodyBytes === 'string' ? bodyBytes : Buffer.from(bodyBytes).toString('utf8'));
    return new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''),
      { headers: { 'content-type': 'text/event-stream' } });
  } });
  assert.equal(actual.instructions, selected.request.instructions);
  assert.equal(actual.max_output_tokens, 128000);
  assert.equal(Object.hasOwn(actual, 'truncation'), false);
  assert.equal(actual.instructions + actual.input[0].content[0].text, selected.request.upstream_messages[0].content[0].text);
  assert.deepEqual(actual.input, [{ role: 'user', content: [
    { type: 'input_text', text: '## Rubric criteria\nFull rubric and artifacts.' },
    { type: 'input_image', detail: 'auto', image_url: `data:image/png;base64,${image}` },
  ] }]);
  assert.equal(calls, 1);
  assert.equal(result.status, 'completed');
  assert.equal(result.output_text, text);
});

for (const [bodyText, code] of [
  ['{"detail":"Instructions are required"}', 'subscription_instructions_required'],
  ['{"error":{"message":"Unsupported parameter: truncation"}}', 'unsupported_truncation'],
  ['{"error":{"message":"Maximum context length exceeded"}}', 'subscription_context_rejected'],
  ['{"error":{"code":"invalid_image"}}', 'subscription_image_rejected'],
]) {
  test(`HTTP 400 safely classifies ${code} without exposing body`, async () => {
    const stream = async function* (_model, _context, options) {
      options.onPayload(body());
      await options.fetch(endpoint, { method: 'POST' });
      yield { type: 'error', error: { ...message('error'), errorMessage: 'private-body' } };
    };
    const result = await runTurn(payload(), { stream, apiKey: 'synthetic',
      fetchImpl: async () => new Response(bodyText + ' private-body', { status: 400 }) });
    assert.equal(result.error.code, code);
    assert.equal(JSON.stringify(result).includes('private-body'), false);
    assert.equal(result._transport_metadata.generation_dispatches, 1);
    assert.equal(result.provider_events.length, 0);
  });
}

for (const [instructions, original] of [
  ['', 'Guidance.\n\n## Rubric criteria\nData'],
  ['Changed guidance.\n\n', 'Guidance.\n\n## Rubric criteria\nData'],
  ['Guidance.\n\n', 'Guidance.\n\n## Rubric criteria\nData\n## Rubric criteria\nOther'],
]) {
  test('scientific role adaptation rejects malformed or ambiguous splits before provider entry', async () => {
    const selected = payload();
    selected.schema_id = 'ScientificRubricVerdicts';
    selected.model.id = 'gpt-6-astra';
    Object.assign(selected.request, { model: 'openai-codex/gpt-6-astra', stage: 'scientific-judge',
      instructions, packet: {}, history: [],
      upstream_messages: [{ role: 'user', content: [{ type: 'text', text: original }] }] });
    await assert.rejects(runTurn(selected, {
      stream() { assert.fail('must not enter provider'); }, apiKey: 'synthetic',
      fetchImpl() { assert.fail('must not dispatch'); },
    }), /invalid_request|invalid_upstream_guidance_split/);
  });
}
