# MASTER DEVELOPMENT DOCUMENT v1.4

## Пилот «Распределённая проверка допуска исследовательских задач»

**Статус:** инженерный план разработки, подчинён нормативному `MASTER PROTOCOL v5.1`  
**Архитектурное ревью: CLOSED. M0: CLOSED. M1/D0: можно начинать после двух P0-правок и implementation acceptance tests.**  
**Изменения относительно v1.3:** исправлен порядок фаз для natural corpus; `calibration_ids.json` выбирается из post-P5 projection, а не из `runs.jsonl`; убран self-referential SHA из `AllowedOutcomes`; `execution_id` через canonical JSON/hkey; transport retry с `wire_attempt_id`; canonical JSON = RFC 8785 JCS; `EvalRepresentation` для artifacts; `build_blocks()` как constrained assignment; `EvidenceGrantEvent` ссылается на `evidence_id`; `run_status` как projection.

---

## 0. Принципы и статус

### 0.1. Три инженерных принципа

1. **Упрощать обвязку, не измерение.** Файлы вместо БД, готовые библиотеки вместо собственных движков, annotation-платформа вместо кастомного UI.
2. **Всё детерминированное — seeded и заморожено.** Критичная случайность — HMAC-SHA256 над **RFC 8785 JCS**. Для не-критичных процедур — обычный PRNG с зафиксированной версией runtime.
3. **Всё недетерминированное — immutable-событиями.** Ledger’ы — постфактум проекцией.

### 0.2. Что нельзя инженерно упрощать

Freeze-манифест, \(K^{norm}\) отдельно от \(K^{estimated\_bill}\), blinding evidence map и Terra packets, role separation, perfect seeded matching, corrected \(X_i\), strata, coverage/tie/N-floor/nonsaturation/budget/style gates, ambiguity rules, Terra–human calibration и full-human fallback, защищённый \(R_{gate}\), extractive-only контекст, human style raters, blocked randomization, schema validation, immutable event logging, split `EvidenceRef`/static ACL, runtime grants, sealed storage Terra labels, deterministic \(B\) вне Terra, `EvalRepresentation`.

### 0.3. LOC — ориентир, не KPI

8–15 тыс. строк — ориентир. Основная сложность — access boundaries, resumability, provenance, human workflow.

### 0.4. M0 prerequisites: CLOSED. Implementation acceptance tests remain

Все девять пунктов v1.2 и восемь пунктов v1.3 закрыты. Что остаётся до M1:

- golden-тесты по каждому из P0/P1 v1.4;
- D0 harness.

**Архитектура больше не ревьюится на бумаге.**

### 0.5. Изменения четвёртого ревью

**P0 (до D0):**
1. **Порядок фаз исправлен:** natural screening предшествует evidence map; strata и preflight выполняются для controlled и natural вместе. Natural episode входит в P5 только при \(stratum \in \{1,2,3\}\) и \(L_i^{eval,max}\le L_E\).
2. **`calibration_ids.json` выбирается из post-P5 projection**, а не из `runs.jsonl`: `EventStore.replay → evaluable_runs_index → freeze → calibration_ids → first Terra call`.

**P1:**
3. **`AllowedOutcomes` без собственного `sha256` в payload**; хеш — в CAS и Artifact Ledger.
4. **`execution_id` через `hkey` с `namespace="execution_id"`**, не строковая конкатенация.
5. **Transport retry: два ID** — `execution_id` (intended generation) и `wire_attempt_id = hkey(execution_id, transport_attempt)`.
6. **Canonical JSON = RFC 8785 JCS** с golden vectors.
7. **`EvalRepresentation(artifact)`** для каждого MIME/class; \(L(EvalRepresentation(a))\) входит в \(L_i^{eval,max}\).
8. **`build_blocks()` — constrained assignment problem**, не shuffle: HMAC rank → sequential assignment → deterministic backtracking → assertions. Natural и controlled блоки строятся отдельно, затем HMAC-interleave.
9. **`EvidenceGrantEvent` ссылается на `evidence_id`**, не на полный `EvidenceRef`.
10. **`run_status` — computed enum projection**, не mutable state.

**P2:**
- Редакционный статус: M0 закрыт; M1/D0 — следующий шаг.

---

## 1. Системный контекст

### 1.1. Акторы

| Актор | Роль | Может видеть | Не может видеть |
|---|---|---|---|
| Eligibility rater ×2 | task eligibility | task, parent, allowlist | outcomes, panels, \(f\) |
| Card editor/validator ×2 | valid/def, applicability | frozen corpus, карточку | outcomes, panels, \(f\), map |
| Natural screener ×2 | natural defect | frozen corpus | outcomes, \(f\), map |
| Evidence mapper ×2 | sufficient sets | frozen corpus, static ACL-scoped evidence | panels, \(f\), outcomes, editor metadata |
| Graph auditor ×2 | edge audit | frozen graph, provenance | outcomes, \(f\) |
| Component auditor | moderation/resolution errors | packages | \(f/U\), panels |
| Human calibration (domain ×1 + methodology ×1) | human gold \(E\) на `execute/explore` | human packets, `allowed_outcomes.json` | \(f/U/N\), panel, map, **Terra labels (storage-level sealed)** |
| Y* builder (domain ×1 + methodology ×1) | frozen `allowed_outcomes.json` | valid card, frozen corpus | outcomes, panels, \(f\) |
| Adjudicator | разногласия своей группы | items | первичная разметка своего item |
| Run operator | запускает P5 | run list, canaries | outcome labels до freeze |

### 1.2. Внешние системы

- OpenAI API: `gpt-6-luna`, `gpt-6.1-terra`.
- SCI-AGENT-EXAM benchmark.
- **Argilla self-hosted**; CSV fallback. Annotation container без credentials к `sealed/terra_labels/`.
- Docker с hardening.
- CAS (локальный или S3/MinIO).
- Git: код, схемы, конфиги, manifests, `calibration_ids.json`, `resolved_config.json`.

---

## 2. Архитектура: слои и DAG

```
┌──────────────────────────────────────────────────────────────┐
│  L10 Measurement & Analysis                                  │
│   gates · contrasts · B-deterministic · reliability · fallback│
├──────────────────────────┬───────────────────────────────────┤
│  L9 Human-in-the-loop    │  L8 Automated Evaluation          │
│   annotation · calibration│   TerraPacket (E only) · Terra   │
│   adjudication · audit   │   drift guard                    │
├──────────────────────────┴───────────────────────────────────┤
│  L7 Run pipeline (orchestrator, f/U/N conditions)            │
├──────────────────────────────────────────────────────────────┤
│  L6 Evidence (map, static ACL, strata, X_i, M^I, coverage)   │
├──────────────────────────────────────────────────────────────┤
│  L5 Card (valid, Y*, applicability, injector, matching, style)│
├──────────────────────────────────────────────────────────────┤
│  L4 Routing (pool, f, U, HMAC tie-break)                     │
├──────────────────────────────────────────────────────────────┤
│  L3 Graph & Context (graph, dedup, extractive context, pkg)  │
├──────────────────────────────────────────────────────────────┤
│  L2 Worker & Author (L1, L2, handoff, card generation)       │
├──────────────────────────────────────────────────────────────┤
│  L1 Model (Luna/Terra client, schemas, usage logs)           │
├──────────────────────────────────────────────────────────────┤
│  L0 Data (registry events, corpus, freeze manifest,          │
│           artifact ledger, sandbox, CAS, EvidenceRef,        │
│           static ACL, runtime grants, EvalRepresentation)    │
└──────────────────────────────────────────────────────────────┘
```

**Правило зависимостей:** каждый слой зависит только от нижележащих. L8 и L9 — sibling-слои; L10 зависит от обоих. Порядок фаз: L8 → L9 → L10. Импорт-зависимости L8↔L9 запрещены.

\(B\) вычисляется в L10 детерминированно; Terra в L8 выдаёт только \(E\) и только для \(Y \in \{\text{execute}, \text{explore}\}\).

---

## 3. L0. Данные

### 3.1. C0.1 Task Registry (event-sourced)

```
registry/
  base.json
  events/consume__<event_id>.json
```

`base.json` immutable; consumed через события; `Consumed_t = Replay(events_{\le t})`.

### 3.2. C0.2 Immutable Event Store

```
episodes/<episode_id>/
  corpus/
  graph.json
  context/{W_read/<branch_id>.json, C_read.json, P_read.json}
  cards/{q0.json, valid.json, allowed_outcomes.json, def.json}
  evidence_map.json
  evidence_acl_static.json
  evidence_grants/<run_id>/grant__<event_id>.json
  strata.json
  runs/<run_id>/
    events/...
    intent/<stage>__<execution_id>.json
    transport/<stage>__<execution_id>__attempt_<NN>.json
    grants/grant__<event_id>.json
  eval/{branch_verification.json, terra_packet.json, terra_label.json}
  audit/component_audit.json
```

Никакого `state.json`; всё — immutable events; общие ledger’ы — проекцией; атомарная запись; SHA-256 на каждый файл.

**`run_status` — projection, не поле:**

```python
RunStatus = Literal[
    "planned",
    "running",
    "complete",
    "incomplete_model_failure",
    "incomplete_transport_ambiguous",
    "incomplete_budget",
    "incomplete_structural",
]
```

\[
status = Project(EventStream(run))
\]

Контракт: `EventStore.replay(episode, run) -> RunState`, `EventStore.project_status(episode, run) -> RunStatus`.

### 3.3. C0.3 Freeze Manifest (pre-pilot, immutable)

Содержит: source hashes, code commit, schemas, prompts, `resolved_config.json` SHA, master seed, price table, model config, base registry SHA, calibration selection rule, \(L_E\), thresholds (\(T_{\max}\), caps, \(L_h\), drift thresholds, \(\tau_{style}\), \(\tau_{coverage}\), \(\tau_{tie}\), \(E_{floor}\), \(\tau_{amb}\), budget gates), DAG/role separation matrix SHA, ACL policy schema SHA, block policy SHA, **`EvalRepresentation` table SHA**, **JCS version (RFC 8785)**.

Незаполненный critical item → `PILOT NOT AUTHORIZED`; SHA mismatch после первого run → блокировка анализа; runtime-артефактов не содержит.

### 3.4. C0.4 Artifact Ledger (runtime, append-only)

```json
{
  "artifact_id": "...",
  "sha256": "...",
  "parent_hashes": ["..."],
  "created_at": "...",
  "kind": "...",
  "episode_id": "...",
  "run_id": "...",
  "execution_id": "..."
}
```

**Правило:** хеш хранится **снаружи** хешируемого объекта; payload immutable artifacts (включая `AllowedOutcomes`) **не содержит собственный `sha256`**.

### 3.5. C0.5 Sandbox Builder (усиленный)

`--network=none`, non-root UID, read-only rootfs, writable `/work/out`, CPU/RAM/pids/time limits, без Docker socket, без host credentials/env secrets. API-вызовы модели — снаружи контейнера. Evaluator/gold вне worker-видимости. SHA-256 manifest, image digest, dependency lock.

### 3.6. C0.6 EvidenceRef (identity; stable `evidence_id`)

```python
class EvidenceLocator(BaseModel):
    kind: Literal["text_span", "json_pointer", "table_cell", "artifact_whole"]

class TextSpan(EvidenceLocator):    byte_start: int; byte_end: int
class JsonPointer(EvidenceLocator): pointer: str
class TableCell(EvidenceLocator):   row: int; column: int
class ArtifactWhole(EvidenceLocator): pass

class EvidenceRef(BaseModel):
    source_sha256: str
    representation_sha256: str
    extraction_id: str
    locator: EvidenceLocator
    fragment_sha256: str
    artifact_id: str

    @property
    def evidence_id(self) -> str:
        return sha256(canonical_jcs(self))     # RFC 8785 JCS
```

`representation_sha256` — SHA canonical extraction; extraction procedure фиксируется в manifest. `fragment_sha256` проверяет целостность фрагмента. Никакого `visibility` в этом типе. Во всей системе оперируем коротким `evidence_id`.

### 3.7. C0.7 Static ACL (frozen) + C0.8 Runtime Grants

#### Static ACL (pre-run, frozen)

```python
class ACLStaticEntry(BaseModel):
    evidence_id: str
    principals: list[str]
    phase: Literal["worker", "handoff", "author_precard", "review"]
    granted: bool
```

Строится детерминированно из provenance и circulant assignment; freeze до outcomes; используется для \(\mathcal A_{ij}^{review}\), \(V_{ijd}\), \(M^I_{id}\), corrected \(X_i\); **не описывает** `revision`, `resolution`, `admission`.

#### Runtime grants (append-only per run)

```python
class EvidenceGrantEvent(BaseModel):
    event_id: str
    run_id: str
    evidence_id: str               # ссылка на EvidenceRef через registry/CAS
    from_principal: str
    to_principal: str
    phase: Literal["revision", "resolution", "admission"]
    source_review_sha: str
```

Фактическая область автора: \(\mathcal A^{revision}_{ivcr} = \mathcal A_i^{author} \cup \{e : GrantEvent(e, run, revision)\}\). Grants не используются для \(V, M^I, X_i\).

**Контракты:** `StaticACL.check / area / freeze`; `Grants.append / area`.

### 3.9. C0.9 CAS

`cas/sha256/<first2>/<full_sha>` + index в artifact ledger. Raw — в CAS; в Git — только код/схемы/конфиги/manifests/`calibration_ids.json`/`resolved_config.json`.

### 3.10. C0.10 Price Table & Cost Aggregator

\(K^{norm}\) — все input как uncached Standard. \(K^{estimated\_bill}\) — по frozen price table. Reconciliation с invoice отдельно. Cost-claim \(f\) vs \(U\) только по \(K^{norm}\).

### 3.11. C0.11 EvalRepresentation (новый P1)

**Задача:** определить, как artifact превращается в Terra-представление; без этого preflight математически определён, но программно не реализуем.

**Таблица (frozen в manifest):**

| Artifact class | Terra representation | Locator |
|---|---|---|
| text/code | canonical text | `TextSpan` |
| JSON | canonical JSON (RFC 8785) | `JsonPointer` / `ArtifactWhole` |
| CSV/table | frozen tabular serialization (bounded) | `TableCell` |
| image | image input, если разрешено | `ArtifactWhole` |
| binary | metadata + deterministic extracted representation | `ArtifactWhole` |
| directory | canonical manifest + permitted constituent representations | composite |

**Правила:**
- `EvalRepresentation(artifact)` детерминирован;
- \(L(EvalRepresentation(a))\) входит в \(L_i^{eval,max}\) (§11.1);
- extractive-only; никакого LLM-summary;
- изменение таблицы = новая версия протокола.

**Контракты:** `EvalRepresentation.render(artifact) -> bytes`; `EvalRepresentation.length(artifact) -> int`.

---

## 4. L1. Модельный слой

### 4.1. C1.1 Model Client (Luna, Terra)

**Правила:**
- Standard tier; fallback запрещён; Luna `reasoning.effort=medium`; `temperature/top_p/top_logprobs` не задаются;
- **`execution_id = hkey(master_seed, namespace="execution_id", fields={run_id, stage, attempt, intent_hash})`** — canonical JCS, не конкатенация;
- **transport retry: два ID:**
  - `execution_id` — intended generation (один на stage/attempt);
  - `wire_attempt_id = hkey(master_seed, namespace="wire_attempt", fields={execution_id, transport_attempt})` — на каждую сетевую попытку;
  - intent пишется один раз на `execution_id`; каждая transport-попытка — отдельный immutable `transport/<stage>__<execution_id>__attempt_<NN>.json`; response — один на `execution_id`;
  - DNS/connect failure **до отправки** → retry с новым `wire_attempt_id`, тем же `execution_id`;
  - явный 429/5xx без принятого generation → bounded retry, тем же `execution_id`;
  - **timeout/disconnect после возможной отправки** → `ambiguous_remote_execution`; НЕ retry;
- **ambiguous_remote_execution** → provider idempotency key + безопасный retry, иначе **безусловно `incomplete`**;
- **semantic/model failure** → **безусловно `incomplete`**; никакого corrective reprompt, никакого второго sample;
- ручной override запрещён;
- wrapper не расширяет `allowed_evidence_ids`.

**Контракты:** `call(packet, execution_id, idempotency_key=None) -> ValidatedResponse | Incomplete`; `Incomplete.reason ∈ {schema_invalid, content_invalid, refusal, server_semantic, ambiguous_remote_execution, timeout_after_send}`.

### 4.2. C1.2 Schema Registry

Схемы: `WorkerOutput`, `AuthorCard`, `Review`, `Moderation`, `Revision`, `Resolution`, `Admission`, `TerraLabelE`, `EvidenceMap`, `GraphEdge`, `StyleGuess`, `CalibrationLabelE`, `AllowedOutcomes`, `EvidenceRef`, `ACLStaticEntry`, `EvidenceGrantEvent`, `EvaluableRunIndex`. Schema hash в manifest.

### 4.3. C1.3 Usage Logger (nullable)

Поля: `execution_id`, `wire_attempt_id`, `run_id`, `episode_id`, `condition`, `variant`, `repeat`, `stage`, `request_hash`, `response_hash: str | None`, `usage_raw: dict | None`, `latency_ms`, `model_metadata`, `billing_status ∈ {known, unknown_remote_execution, not_executed}`.

DNS fail: `response_hash=None`, `usage_raw=None`, `billing_status="not_executed"`. Timeout-after-send: `None`, `None`, `"unknown_remote_execution"`; стоимость не выдумывается; reconciliation отдельно. Неизвестное — `null`, не 0.

### 4.4. C1.4 Canonical JSON (RFC 8785 JCS)

**Задача:** byte-exact canonical serialization для хешей и HMAC.

**Правило:** RFC 8785 JSON Canonicalization Scheme; golden vectors в тестах; фиксированная версия в manifest.

**Контракты:** `canonical_jcs(obj) -> bytes`; `hkey(master_seed, namespace, fields) -> hex` использует `canonical_jcs`.

---

## 5. L2. Worker & Author

### 5.1. C2.1 Worker Runner (L1/L2)

L1 не видит друг друга; L2 получает только structured output L1; \(L2_j \leftarrow (L1_j, L1_{(j+3)\bmod10})\); `EvidenceRef` обязателен; static ACL детерминирован.

### 5.2. C2.2 Author State Runner

Author state не выполняет worker subtask; полные histories недоступны; handoff — extractive only.

### 5.3. C2.3 Handoff Extractor

Порядок result → limitations → evidence `EvidenceRef` → artifact IDs → provenance; лимит \(L_h\); static ACL фиксирует, что видит author и что остаётся worker-private.

---

## 6. L3. Graph & Context

### 6.1. C3.1 Graph Builder

Типы рёбер по протоколу; source для каждого; карточка не меняет граф.

### 6.2. C3.2 Graph Auditor Tool (численный gate)

Audit до outcomes; \(n_{audit}\) и пороги FP/missed фиксируются до freeze; `FPR_edge = false_positive_edges / all_claimed_edges_checked`; `missed_edge_rate = missed_edges / sampled_source_records_checked`.

### 6.3. C3.3 Deduplicator

Пустые наборы исключаются до Jaccard; canonization заморожена; producer-consumer не основание для дубликата.

### 6.4. C3.4 Context Preparer

Порядок приоритета фиксирован; лимиты в manifest; общий текст не дублируется; исключённые фрагменты — `EvidenceRef` с static ACL.

---

## 7. L4. Routing

### 7.1. C4.1 Pool Builder

Исключения до проверки размера; при \(m_i<15\) — episode excluded до card construction.

### 7.2. C4.2 Router \(f\) / C4.3 Uniform Sampler \(U\)

Последовательность критериев протокола; HMAC tie-break (§7.4); \(D_i\) фиксируется; лог пересечения \(D_i \cap U_{ir}\).

### 7.4. C4.4 HMAC-based Randomness

\[
r = HMAC\_SHA256(key=master\_seed,\ message=canonical\_jcs(fields))
\]

`fields` — именованные поля; domain separation через `namespace`. `random.shuffle` для критичных процедур запрещён. Golden-тесты для JCS.

### 7.5. C4.5 Tie-break & Seed Manager

Master seed в manifest; `T_tie` из логов.

---

## 8. L5. Card Construction

### 8.1. C5.1 Valid Card Builder

Принимается, только если по \(\mathcal D_i^0\) нет значимого дефекта; два validators.

### 8.2. C5.2 Applicability Tool

Слепая разметка \(A_{id}\); два editors; adjudication; codebook в manifest.

### 8.3. C5.3 Defect Injector

\(q_i^{def}=Inject_{d_i}(q_i^{valid})\) — один целевой дефект; проверка; ручная validation.

### 8.4. C5.4 Global Matcher (batch barrier)

Только после freeze всех valid cards и applicability matrices; только perfect; HMAC tie-break `namespace="matching"`; иначе — следующая задача.

### 8.5. C5.5 Style Test Harness

Blind `def/valid` тремя raters; units — card; gate \(BA_{style}\le0.60\), \(U_{0.95}^{CP}<0.70\); при провале — переработка до outcome calls.

### 8.6. C5.6 Allowed Outcomes Builder (без self-SHA)

```python
class AllowedOutcomes(BaseModel):
    episode_id: str
    valid_card_sha: str
    allowed_actions: list[Literal["execute", "explore", "escalate"]]
    explore_specs: list[ExploreSpec]
    constraints: list[ConstraintSpec]
    frozen_at: str
    # НЕТ поля sha256 — хеш в CAS и Artifact Ledger

class ExploreSpec(BaseModel):
    budget_range: tuple[float, float]
    scope: list[str]
    stop_criterion: str
    reversibility: Literal["reversible", "partially", "irreversible"]

def evaluate_outcome(Y) -> OutcomeEvaluation:
    admissible: bool
    violated_constraints: list[ConstraintViolation]
    matched_explore_spec: ExploreSpec | None
```

**Правила:**
- human experts создают до P5; freeze до первого run;
- \(B = \mathbf{1}\{Y=\text{incomplete} \lor \neg evaluate\_outcome(Y).admissible\}\) — детерминированно в L10;
- Terra **не** получает `allowed_outcomes.json`;
- для `explore` — проверка budget/scope/stop/reversibility, не булев `contains`;
- хеш хранится в Artifact Ledger, не в payload.

---

## 9. L6. Evidence

### 9.1. C6.1 Evidence Map Tool

Blind-разметка sufficient sets из `EvidenceRef` двумя annotators + adjudication; annotators видят static-ACL-scoped evidence; не видят panels, \(f\), outcomes.

### 9.2. C6.2 Coverage & Strata Calculator

\(V_{ijd}\), \(I_{id}\), \(a_{id}\), \(q^I_{id}\), \(M^I_{id}\), corrected \(X_i\), strata 1–5, \(L_{frag}\), \(L_{destroy}\).

Corrected \(X_i\) использует **static ACL** (`StaticACL.area(worker_id, "review")`); pre-run exclusion strata 4–5; \(\tau_{coverage}\ge0.40\); все метрики до outcomes.

---

## 10. L7. Run Pipeline

### 10.1. C7.1 Run List Generator (blocked randomization as constrained assignment)

**Контракт:**

```python
def build_blocks(
    controlled_runs: list[RunSpec],
    natural_runs: list[RunSpec],
    block_size: int,
    constraints: BlockConstraints,
    hmac_key: bytes,
) -> list[Block]
```

**Алгоритм (constrained assignment, не shuffle):**
1. HMAC rank всех runs: `rank = hkey(hmac_key, "block_rank", fields)`;
2. Для каждого run в порядке rank: sequential assignment в первый block, где constraints остаются feasible;
3. При тупике — deterministic backtracking (bounded depth);
4. Финальные hard assertions.

**Hard assertions:**
- controlled and natural blocks строятся **отдельно** (`Blocks_C`, `Blocks_N`), затем HMAC-interleave по времени (`namespace="block_sequence"`);
- **три repeats одного episode не попадают в один блок**;
- `|\{run \in b : episode(run) = i\}| \le 1`;
- для controlled: `count_c(b) ≈ count_{c'}(b)` для \(c, c' \in \{f, U, N\}\); imbalance \(\le 1\);
- для controlled: `count_{def}(b) ≈ count_{valid}(b)`; imbalance \(\le 1\);
- для natural: `def/valid` constraint **неприменим** (у natural нет valid-варианта); balance по condition и repeat только;
- partial block rule фиксируется в manifest (`partial_block=true`).

**Тесты:** 30 controlled episodes; неполный controlled corpus; natural corpus; controlled + natural вместе.

**Выход:** `runs.jsonl` с `(episode_id, variant, condition, repeat, block_id, order_index)`.

### 10.2. C7.2 Run Orchestrator (idempotency + no silent re-call)

- `execution_id = hkey(master_seed, "execution_id", {run_id, stage, attempt, intent_hash})`;
- перед API-call — `intent/<stage>__<execution_id>.json` (один раз);
- каждая transport-попытка — `transport/<stage>__<execution_id>__attempt_<NN>.json` с `wire_attempt_id`;
- response — `events/<stage>_response__<execution_id>.json` (один);
- на рестарте: response существует → skip; intent без response → `AmbiguousIntent`, **не перезапускать молча**; run → `incomplete`, если нет idempotency key;
- semantic/model failure → `incomplete`;
- ambiguous remote → `incomplete` без idempotency key;
- `R_{gate}` защищён; reviewer+moderation+revision не могут потребить \(K^{run}_{cap}-R_{gate}\);
- rate-limit scheduling без изменения научной случайности.

**Контракты:** `run(run_spec) -> RunRecord`; `resume(run_spec) -> RunRecord | AmbiguousIntent`; `project_status(episode, run) -> RunStatus`.

### 10.3. C7.3 f/U Pipeline

3 review → moderation → revision → resolution (только при \(Z=1\)) → admission; `EvidenceRef` обязателен; `no conflict found` ≠ `insufficient context`; governance не использует непредъявленное; **reviewer’s presented evidence порождает `EvidenceGrantEvent(evidence_id, ...)` для revision**; revision-область автора — `A_i^{author} ∪ grants`.

### 10.4. C7.4 N Pipeline

Self-revision + admission; \(Z=0\); resolution не вызывается; cap меньше.

### 10.5. C7.5 Canary Drift Guard (Luna)

20 canaries до/после сессии; thresholds на D0; выход → пауза.

### 10.6. C7.6 Branch Verifier Integration (offline)

После всех model runs; \(Q^{prog}_{ij}\) или NA.

---

## 11. L8. Automated Evaluation (только Terra, только \(E\))

### 11.1. C8.1 Terra Packet Builder (без `allowed_outcomes`, с `EvalRepresentation`)

**Состав packet (идентичен для `def`, `valid`, natural):**

\[
Packet_E = (C_i,\ O_{i1:20},\ EvalRepresentation(artifacts_i),\ q^{final},\ Y,\ constraints)
\]

**Правила:**
- **никакого `allowed_outcomes.json`** — утечка `valid/def` устранена;
- artifacts рендерятся через `EvalRepresentation` (C0.11);
- сборка детерминирована; никакого retrieval;
- packet не содержит: `allowed_outcomes`, \(f/U/N\), reviewer identities, panels, ranks, отзывы, moderation/resolution transcripts, model identity, evidence-map, \(V,q^I,M^I\), defect metadata, editor identity;
- валидатор проверяет отсутствие запрещённых полей и детерминизм.

**Preflight (P3.5, до P5, для controlled и natural):**

\[
L_i^{eval,max} = L(C_i) + \sum_{j=1}^{20} L(O_{ij}) + \sum_{a} L(EvalRepresentation(a)) + L_{final}^{max} + L_{overhead}
\]

Условие допуска: \(L_i^{eval,max} \le L_E\). Если не выполнено — episode получает `evaluation_packet_overflow` **до outcomes** и не запускается. \(L_E\) фиксируется на D1.

На P7 — assertion: \(L_i^{actual} \le L_i^{eval,max} \le L_E\).

**Контракты:** `preflight_size(episode, L_E) -> {ok, L_max} | Overflow`; `build_packet(run) -> TerraPacket`; `validate_packet(packet)`.

### 11.2. C8.2 Terra Evaluator (только \(E\), только `execute/explore`)

\[
NeedTerra(Y) = \mathbf{1}\{Y \in \{\text{execute}, \text{explore}\}\}
\]

Для `escalate`/`incomplete`: Terra **не вызывается**; \(E=0\) структурно; `incomplete` попадает в sensitivity \(E^{miss}\).

**Выход:** \(E^{final}\); uncertainty flag; `EvidenceRef` IDs; основание; condition guess \(\hat c_E\).

**Правила:** evidence-bounded; `insufficient_evidence_for_evaluation` фиксируется до freeze; Terra **не** получает `allowed_outcomes.json`; **не** вычисляет \(B\).

**Контракты:** `evaluate(packet) -> TerraLabelE`.

### 11.3. C8.3 Terra Drift Guard

10–20 frozen evaluation packets; Terra до/после P7; exact label agreement; confidence distribution; usage metadata; **test–retest ~10%** (\(P(\hat Y_1=\hat Y_2)\)); выход → пауза.

---

## 12. L9. Human-in-the-loop

### 12.1. C9.1 Annotation Gateway (Argilla self-hosted)

Blindness через HMAC ID-маппинг; role separation по access matrix; один человек в двух ролях на пересекающихся partitions запрещён; логирование действий.

**Annotation container не имеет credentials** к `sealed/terra_labels/`; storage-level deny на случайный UI-переход.

### 12.2. C9.2 Role Separation Matrix

Machine-checkable; проверка на `(person_id, task_partition)`; hard fail.

### 12.3. C9.3 Adjudication Queue

Adjudicator не участвовал в первичной разметке; результат фиксируется один раз.

### 12.4. C9.4 Human Calibration Tool (post-P5 projection)

**Порядок (исправлен):**

```
P5 complete
→ EventStore.replay
→ build evaluable_runs_index
→ freeze evaluable_runs_index
→ freeze calibration_ids
→ first Terra call
```

**`evaluable_runs_index`:**

\[
R^{eval} = \{r : RunStatus(r)=\text{complete} \land Y_r \in \{\text{execute}, \text{explore}\}\}
\]

`calibration_ids = Sample(R^{eval}, frozen_sampling_rule)`; хешируется **до первого Terra call**. Human raters не имеют read-permission на `sealed/` (storage-level). Human оценивают \(E\) на том же пространстве; \(B\) не размечается human. Публикуются prevalence, observed agreement, \(\alpha_H\), adjudication rate; \(\alpha_H\ge0.67\). Human labels freeze до сравнения с Terra.

**Контракты:** `EvaluableRunIndex.build(episode_store) -> index`; `EvaluableRunIndex.freeze(index) -> sha`; `Calibration.select(evaluable_run_index, frozen_sampling_rule) -> calibration_ids`; `Calibration.run(ids, experts) -> HumanLabelsE`.

### 12.5. C9.5 Component Audit Tool

Auditors не видят policy; пакеты фиксированы.

---

## 13. L10. Measurement & Analysis

### 13.1. C10.1 Gates Evaluator

Gates: pool, labels (\(\alpha_H\)), Terra validation, ambiguity, budget incomplete, coverage, tie, N floor, nonsaturation, style, graph audit thresholds.

### 13.2. C10.2 Deterministic \(B\) Calculator

\(B_{i,valid,c,r} = \mathbf{1}\{Y=\text{incomplete} \lor \neg evaluate\_outcome(Y).admissible\}\) — из `allowed_outcomes.json` и \(Y\), вне Terra, вне human. Golden-тесты.

### 13.3. C10.3 Evaluator Reliability & Fallback (только \(E\))

\(BA_E\), sensitivity, specificity на calibration subset (только `execute/explore`); Terra labels из `sealed/`; human labels из `blind/`; сравнение после freeze human labels; если gates пройдены — Terra labels freeze; иначе full-human fallback до анализа \(f-U\); выбор по выгодности запрещён; \(B\) не входит в Terra–human comparison.

### 13.4. C10.4 Contrasts Engine

\(f-U\), \(f-N\), \(U-N\) для \(E\), \(B\); distribution of actions; repetitions усредняются внутри episode; episode — единица независимости; cost claim только \(f\) vs \(U\); ambiguity bounds.

### 13.5. C10.5 Coverage Regression

\(g_i = \beta_0 + \beta_1 \overline M^I_i + \varepsilon_i\); causal mediation не заявляется.

### 13.6. C10.6 Drift & Reliability Aggregator

Luna canaries, Terra canaries + test–retest, human reliability, confusion matrices, cost reconciliation.

### 13.7. C10.7 Report Generator

Минимальный итоговый отчёт по §75 протокола.

### 13.8. C10.8 Manifest Validator

Mismatch → анализ блокируется; `freeze_manifest` и `artifact_ledger` валидируются раздельно.

---

## 14. Кросс-компонентные правила

| Правило | Где проверяется |
|---|---|
| Extractive-only контекст | C3.4, C2.3 |
| `EvidenceRef` (identity) vs static ACL (access) vs runtime grants | C0.6, C0.7, C0.8 |
| `evidence_id = SHA256(JCS(EvidenceRef))` | C0.6 |
| Generalized locator + `representation_sha256` | C0.6 |
| `EvidenceGrantEvent` ссылается на `evidence_id` | C0.8 |
| Evidence ID обязателен для содержательного утверждения | C1.1, C7.3 |
| No fallback model; Standard tier | C1.1 |
| `execution_id` через `hkey(namespace="execution_id")` | C1.1, C7.2 |
| `wire_attempt_id` для каждой transport-попытки | C1.1, C1.3 |
| Transport retry ≠ semantic failure; semantic/ambiguous → `incomplete` | C1.1, C7.2 |
| No silent re-call, no operator override | C7.2 |
| Usage logger: nullable `response_hash`/`usage_raw`, `billing_status` | C1.3 |
| Canonical JSON = RFC 8785 JCS | C1.4 |
| `EvalRepresentation` для artifacts | C0.11, C8.1 |
| Preflight \(L_i^{eval,max}\le L_E\) для controlled и natural | C8.1, P3.5 |
| TerraPacket без `allowed_outcomes` | C8.1 |
| Terra только для `execute/explore` | C8.2 |
| \(B\) детерминированно вне Terra | C8.2, C10.2 |
| `calibration_ids` из post-P5 `evaluable_runs_index` | C9.4 |
| Argilla self-hosted без credentials к `sealed/` | C9.1, C9.4 |
| `build_blocks()` — constrained assignment | C7.1 |
| Natural blocks отдельно, HMAC-interleave | C7.1 |
| `run_status` — projection | C3.2 |
| `AllowedOutcomes` без self-SHA | C3.4, C8.6 |
| Immutable events, no mutable `state.json` | C3.2 |
| Consumed tasks через `registry_events` | C3.1 |
| Freeze manifest vs artifact ledger | C3.3, C3.4 |
| API calls снаружи sandbox | C0.5 |
| Terra drift guard + test–retest | C8.3 |
| `resolved_config.json` один раз в CAS | C0.9, §20 |
| Graph audit thresholds до freeze | C3.2 |

---

## 15. Стек

| Слой | Выбор |
|---|---|
| Язык | Python 3.11+ |
| Пакеты | `uv` + `uv.lock` |
| Схемы | Pydantic v2 |
| Canonical JSON | RFC 8785 JCS (собственная byte-exact реализация или `rfc8785`), golden vectors |
| Конфиг | plain YAML + Pydantic validation + `resolved_config.json` (один раз, CAS) |
| Model client | официальный OpenAI SDK + тонкая обёртка |
| Retry | `tenacity`, transport ≠ semantic |
| Логи | `structlog` → JSONL, immutable events |
| Storage | CAS + immutable event files + artifact ledger |
| Analysis store | DuckDB + Polars поверх CAS |
| Sandbox | Docker с hardening |
| Annotation | **Argilla self-hosted**; CSV fallback; без credentials к `sealed/` |
| Reliability | `krippendorff` |
| Анализ | pandas, numpy, scipy, statsmodels |
| Tests | pytest + golden files |
| Версионирование | Git (код, схемы, конфиги, manifests) + CAS (raw) |

**Осознанно не используются:** графовая БД, Airflow/Prefect, Hydra/OmegaConf, DVC, Git LFS для raw artifacts, собственный scheduler, собственный cost engine, собственный UI, собственный статистический движок, LiteLLM, Inspect AI.

---

## 16. Фазы исполнения (исправленный порядок)

| Phase | Что делает | Выход |
|---|---|---|
| D0 | 5 micro-development: worker, L2, graph, context, f/U, N baseline, usage logs, Luna smoke, Terra dry calibration, **измерение \(L_i^{eval,max}\)**, human timing | D0 report |
| D1 | 15 development; freeze \(T_{\max}\), caps, \(L_h\), **\(L_E\)**, budget, drift thresholds, graph audit thresholds, \(\tau_{style}\), block policy, **`EvalRepresentation` table**, JCS version | freeze manifest |
| P0 | seeded eligibility; controlled sequence; natural reserve | registry events |
| P1 | pool, graph, exclusions, dedup, \(m_i\ge15\), handoffs, \(q_i^0\), **static ACL** | pools per episode |
| P2a | все controlled valid cards + applicability matrices; freeze | valid cards frozen |
| P2b | global matching (batch barrier) | assignment |
| P2c | controlled defect injection + validation + edit distance + style test + `allowed_outcomes.json` | def cards, Y* frozen |
| **P2N** | **natural screening** (frozen order, до evidence map) | natural defects identified |
| **P3** | **evidence map + strata для controlled И natural** | coverage report |
| **P3.5** | **evaluation preflight \(L_i^{eval,max}\le L_E\) для controlled И natural**; \(stratum \in \{1,2,3\}\) | overflow/strata 4–5 excluded до outcomes |
| **P4** | **final core-set freeze**: controlled + natural episodes, прошедшие P3.5 | core-set frozen |
| P5 | run list (`build_blocks()` для controlled и natural), canaries (Luna), orchestrator с idempotency, **runtime grants** | run records |
| **P5.5** | **evaluable_runs_index**: replay EventStore → `R^{eval} = {status=complete ∧ Y∈{execute,explore}}` → freeze | evaluable_runs_index.json |
| P6 | offline branch verification | \(Q^{prog}\) |
| P7 | Terra packets (без `allowed_outcomes`), **`calibration_ids` freeze из `R^{eval}`**, Terra labels (только `execute/explore`), Terra drift guard, human calibration (sealed, только `execute/explore`), human labels freeze | outcome labels |
| P8 | component audit | audit report |
| P9 | gates, deterministic \(B\), Terra↔human (L10, sealed), fallback, contrasts, coverage, reliability, drift, cost, decision | отчёт |

**Порядок P7:** `calibration_ids` freeze из `evaluable_runs_index` → Terra runs + drift guard → human calibration (blind к Terra, storage-level sealed) → human labels freeze → Terra↔human comparison (\(E\) only) → fallback decision → contrasts.

---

## 17. Тестирование

1. **Schema tests:** golden files.
2. **Freeze tests:** изменение без manifest bump → fail.
3. **Determinism tests:** HMAC-based \(f\), \(U\), matching, `build_blocks()` воспроизводимы между версиями.
4. **JCS tests:** golden vectors; byte-exact; `-0`, exponent notation, Unicode escaping.
5. **`evidence_id` tests:** два разных source с одинаковым фрагментом → разные `evidence_id`.
6. **Static ACL vs grants tests:** \(V, M^I, X_i\) используют static ACL; revision-область — `author ∪ grants`; grants не влияют на \(X_i\).
7. **Blinding tests:** Terra packet без `allowed_outcomes`, `valid/def`, \(f/U/N\).
8. **Sealed storage tests:** human role не читает `sealed/`; annotation container без credentials.
9. **Locator tests:** `TextSpan`, `JsonPointer`, `TableCell`, `ArtifactWhole` + `representation_sha256` + `fragment_sha256`.
10. **Retry tests:** semantic → `incomplete`; ambiguous → `incomplete` без idempotency key; no silent re-call; **`wire_attempt_id` логируется для каждой transport-попытки**.
11. **Usage logger tests:** DNS fail → `billing_status="not_executed"`; timeout-after-send → `"unknown_remote_execution"`; run не `incomplete` из-за billing-detail.
12. **Idempotency tests:** interrupted intent → ambiguous, не silent re-call.
13. **Event replay tests:** run state из events; consumed из `registry_events`; **`run_status` projection**.
14. **Freeze vs ledger tests:** runtime artifacts не в freeze manifest; payload без self-SHA.
15. **Preflight tests:** controlled и natural; \(L_i^{eval,max}>L_E\) → `evaluation_packet_overflow` на P3.5.
16. **`EvalRepresentation` tests:** каждый MIME/class → детерминированный render; length входит в \(L_i^{eval,max}\).
17. **Terra call tests:** `escalate`/`incomplete` → Terra не вызывается; \(E=0\) структурно.
18. **Deterministic \(B\) tests:** из `allowed_outcomes.evaluate_outcome(Y)`; golden.
19. **Terra drift tests:** before/after batch + 10% test–retest.
20. **`build_blocks()` tests:** constrained assignment; hard assertions; controlled/natural separate; HMAC-interleave; partial block; тупики + backtracking; неполный controlled.
21. **`calibration_ids` tests:** выбор из `R^{eval}`, не из `runs.jsonl`; freeze до Terra.
22. **Graph audit gate tests.**
23. **Cost tests:** \(K^{norm}\) не зависит от cache hit.
24. **Gate tests:** на границе каждого gate.
25. **D0 end-to-end:** один episode от sandbox до Terra packet; \(L_i^{eval,max}\) измерен.
26. **Role separation tests:** hard fail.

---

## 18. Риски и gates

| Риск | Митигация |
|---|---|
| \(m_i\ge15\) не достигается | D0 на 5 задачах |
| Coverage gate не проходит | early P3 check |
| Style leakage | \(BA_{style}\) |
| Bounded governance на Luna | D0 smoke test |
| Terra не проходит calibration | full-human fallback |
| Terra drift | Terra canaries + test–retest |
| Luna drift | canaries + blocked randomization |
| Cache hit как преимущество | \(K^{norm}\) |
| Role violation | access matrix + sealed storage |
| Budget incomplete | \(R_{gate}\) |
| Silent re-call | idempotency |
| Неполный usage breakdown | nullable + `billing_status` |
| Terra packet overflow | preflight P3.5 (controlled и natural) |
| Terra leakage `valid/def` | packet без `allowed_outcomes` |
| \(B\) зависит от Terra | deterministic \(B\) |
| Static ACL знает будущее | static ACL + runtime grants |
| Operator override | no override в C7.2 |
| Freeze/ledger confusion | раздельные manifest/ledger |
| Graph audit без gate | thresholds до freeze |
| Terra cost на `escalate/incomplete` | `NeedTerra(Y)` |
| Natural episode без evidence map | P2N до P3 |
| Calibration из `runs.jsonl` | post-P5 projection |
| Self-referential SHA | hash в ledger |
| Blocked randomization deadlock | constrained assignment + backtracking |
| Artifact не помещается в prompt | `EvalRepresentation` |
| Неоднозначная конкатенация | canonical JCS |
| Потеря transport-попыток | `wire_attempt_id` |

---

## 19. Milestones

| Milestone | Что готово | Срок (2–3 инженера) |
|---|---|---|
| M0: implementation acceptance tests | golden-тесты для `evidence_id`, static ACL/grants, preflight, `build_blocks()`, deterministic \(B\), Terra без `allowed_outcomes`, nullable usage, **JCS**, **`EvalRepresentation`**, **`run_status` projection** | 1–2 недели |
| M1: D0 harness | sandbox hardened, model client, L1/L2, graph, context, f/U, N, usage logs, event store | 3–5 недель |
| M2: Card + evidence | valid, Y* + `evaluate_outcome`, applicability, global matching, injector, style, map, static ACL, strata, preflight | 3–4 недели |
| M3: Run pipeline | `build_blocks()`, orchestrator idempotent, Luna canaries, gating, budget, runtime grants | 3–4 недели |
| M4: Evaluation split | Terra packets (без `allowed_outcomes`), Terra evaluator (L8, `execute/explore`), Terra drift guard, **`evaluable_runs_index`**, human calibration (L9, sealed), component audit | 2–3 недели |
| M5: Measurement | deterministic \(B\), gates, Terra↔human (L10, sealed), fallback, contrasts, coverage, reliability, report | 2–3 недели |
| M6: Human tooling | Argilla self-hosted, access matrix, sealed namespace, adjudication queue | 2 недели |

LOC — ориентир 8–15 тыс., не KPI.

---

## 20. Freeze, версионирование, change control

1. `freeze_manifest.json` immutable после первого пилотного вызова; только pre-pilot артефакты.
2. `artifact_ledger` append-only для runtime; payload без self-SHA.
3. Любое изменение схемы, промпта, seed, конфига, price table, threshold, matching rule, evidence rule, ACL policy, block policy, `L_E`, `EvalRepresentation` или JCS version создаёт новую версию протокола и новый data partition.
4. `main.md` — научная рамка, подчинённая протоколу.
5. `resolved_config.json` — один раз в CAS; каждый run пишет `run.config_sha256`.
6. После пилота — журнал изменений; pilot episodes не входят в confirmatory study.

---

## 21. Сводка контрактов

```
Registry.read / consume / consumed_at / replay
EventStore.append / replay / project_status / ledger
FreezeManifest.freeze / verify
Ledger.append / lineage
Sandbox.build / run
CAS.put / get / exists
EvidenceRef.make / resolve / validate / evidence_id
StaticACL.check / area / freeze
Grants.append / area
canonical_jcs(obj) -> bytes                      # RFC 8785 JCS
hkey(master_seed, namespace, fields) -> hex
Client.call(packet, execution_id, idempotency_key=None) -> ValidatedResponse | Incomplete
UsageLog.write(call_record) -> sha               # nullable response_hash, usage_raw, billing_status
Cost.compute / reconcile
Worker.run(subtask, allowed_inputs, execution_id) -> WorkerOutput
Author.make_state / generate_card
Graph.build / GraphAudit.passes_gate
Dedup.filter
Context.prepare
Pool.build
Router.f / Sampler.u
Match.run
StyleHarness.run
AllowedOutcomes.build / evaluate_outcome(Y) -> OutcomeEvaluation
EvidenceMap.compute
RunList.generate(policy) -> runs.jsonl
build_blocks(controlled_runs, natural_runs, block_size, constraints, hmac_key) -> list[Block]
Orchestrator.run / resume
EvalRepresentation.render(artifact) -> bytes / length(artifact) -> int
TerraPacket.preflight_size(episode, L_E) -> {ok, L_max} | Overflow
TerraPacket.build(run) -> TerraPacket            # без allowed_outcomes
TerraPacket.validate(packet) -> {ok, forbidden_fields[]}
Terra.evaluate(packet) -> TerraLabelE            # только execute/explore
TerraDrift.check(batch_id)
EvaluableRunIndex.build(episode_store) -> index
EvaluableRunIndex.freeze(index) -> sha
Calibration.select(evaluable_run_index, frozen_sampling_rule) -> calibration_ids
Calibration.run(ids, experts) -> HumanLabelsE
sealed_access(role) -> namespace
Reliability.compare(terra_E, human_E) -> {BA_E, sens, spec, confusion}
Fallback.decide(reliability) -> {use_terra | full_human}
B.compute(allowed_outcomes, Y) -> bool           # deterministic, L10
Audit.run / GraphAudit.passes_gate
Gates.evaluate
Contrasts.compute
Report.generate
```

---

## 22. Итог

**Архитектурное ревью: CLOSED. M0: CLOSED. M1/D0: можно начинать после двух P0-правок и implementation acceptance tests.**

Ключевые изменения относительно v1.3:

**P0:**
- **Порядок фаз исправлен:** P2N natural screening → P3 evidence map + strata для controlled и natural → P3.5 preflight для controlled и natural → P4 final core-set freeze → P5. Natural episode входит в P5 только при \(stratum \in \{1,2,3\}\) и \(L_i^{eval,max}\le L_E\).
- **`calibration_ids` из post-P5 projection:** P5.5 `evaluable_runs_index` через replay; `R^{eval} = {status=complete ∧ Y∈{execute,explore}}`; freeze до первого Terra call.

**P1:**
- **`AllowedOutcomes` без self-SHA**; хеш — в Artifact Ledger.
- **`execution_id` через `hkey(namespace="execution_id")`**; canonical JCS.
- **`wire_attempt_id = hkey(execution_id, transport_attempt)`**; каждая transport-попытка — отдельный immutable файл.
- **Canonical JSON = RFC 8785 JCS** с golden vectors.
- **`EvalRepresentation`** для artifacts; length входит в \(L_i^{eval,max}\).
- **`build_blocks()` — constrained assignment** (HMAC rank → sequential assignment → backtracking → assertions); controlled и natural блоки отдельно, затем HMAC-interleave.
- **`EvidenceGrantEvent` ссылается на `evidence_id`**.
- **`run_status` — computed enum projection**.

**P2:**
- Редакционный статус: M0 закрыт; M1/D0 — следующий шаг.

**Что не менялось:** архитектура L0–L10, DAG L8/L9/L10, event sourcing, CAS, batch barrier, extractive-only, freeze/ledger split, gates, HMAC randomness, Terra только для \(E\).

**Следующий результат — не v1.5, а D0-таблица:**

| episode | workers survived | \(m_i\) | dedup | \(X_i\) | \(\overline M^I_i\) | packet tokens | branch tokens | \(E_{def,N}\) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| D0-1 | … | … | … | … | … | … | … | … |
| … | | | | | | | | |

Если на пяти задачах одновременно появляются приличные \(m_i\), private evidence и ненулевое преимущество маршрутизации — конструкция получает реальный экспериментальный объект. Если нет — это более полезный результат, чем ещё двадцать страниц спецификации.