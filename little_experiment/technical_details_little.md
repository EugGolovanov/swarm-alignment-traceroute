# TECHNICAL IMPLEMENTATION DOCUMENT v0.4

## Minimal proof-of-mechanism: work-profile-overlap routing in homogeneous LLM swarm

**Статус:** инженерный план разработки; подчинён `MINIMAL PROOF-OF-MECHANISM PROTOCOL v0.4` (см. §0.5 — required protocol delta)  
**Область:** harness для 6 задач, 8 workers, 3 conditions, 72 panels, 144 review outputs, Terra + human calibration + adjudication + retest  
**Тестируемый механизм:** `work-profile-overlap routing`  
**Изменения относительно v0.3:** закрыты 5 P0 и 6 P1/P2 из ревью. Protocol drift закрыт через явный required delta к PoM v0.4. Двухступенчатый freeze D0 → final. Seeded ordering исправлен. Private-evidence gate variant-aware. Idempotency добавлена. `action_class`, self-SHA и `profile_sha256` удалены. Incomplete panel handling определён. Defect-type assignment через pre-outcome applicability matching.

---

## 0. Принципы, статус, protocol delta

### 0.1. Принципы

1. **Минимум обвязки, максимум честности измерения.** Immutable raw outputs, frozen config, frozen routing map, blind evaluation — обязательны.
2. **Router детерминирован; random control выбирается до model call.**
3. **Blinding — через packet schema и golden-тесты**, не соглашениями.
4. **Raw outputs не перезаписываются.**
5. **Никаких изменений router, mapping, thresholds после freeze.** После D0 — явное fix window, затем final freeze.

### 0.2. Что берётся из PoM v0.4

Инварианты §0.3 PoM: одна модель, frozen snapshot, network disabled, реальные подзадачи, structured outputs, evidence provenance, deterministic router, random control до model call, valid/def pairing, condition-blind packets, blind Terra и human, immutable outputs, frozen router и thresholds.

### 0.3. Что сознательно не строится

Из §35 PoM: 20 workers, L2, production graph, Jaccard, production ACL, runtime grants, admission, moderation, revision, resolution, natural corpus, полный evidence-map, graph audit, production event sourcing, canary/drift, cost normalization, blocked randomization, confirmatory power, ResearchClawBench, role-only, history-shuffle, heterogeneous swarm.

### 0.4. Что остаётся от v0.3 без изменений

Physical `ReviewerPacket` / `TerraPacket` как blinding boundaries; split `ProposalCard`/`ExperimentalCard`; public/private coverage; frozen calibration/retest selection до Terra; `MEASUREMENT INCONCLUSIVE` вместо ложного `NO-GO`; workflow `WorkerOutput → WorkProfile → PrivateEvidence → Router → Review → Terra → HumanCalibration`.

### 0.5. Required protocol delta: PoM v0.4

**Этот документ требует PoM v0.4.** Технический документ не может менять нормативный gate молча. Перед реализацией должны быть внесены в PoM v0.4:

1. **Private coverage как primary gate.** §4.4 и §20.2 PoM: primary criterion — \(C_i^{R,private} > q_i^{U,private}\); public coverage — diagnostic. GO criterion §34.1 п.4 соответственно.
2. **Variant-aware private-evidence gate.** §10 PoM: \(E_{i,v}^{private} = E_i^{target} \setminus V_i^{Fresh,v}\) для \(v \in \{\text{valid}, \text{def}\}\); требование private evidence для def (primary) и public — для valid (diagnostic); строгий вариант — union по обеим variants.
3. **Defect-type assignment через pre-outcome applicability matching**, не 1:1 seeded вслепую.
4. **Incomplete reviewer call не обнуляет panel**, если остаётся ≥1 валидный review; `n_reviewers_observed` публикуется отдельно.
5. **Двухступенчатый freeze:** D0 development freeze → D0 → fix window → final PoM freeze → seeded selection 6 PoM IDs.
6. **`action_class` удалён** из Task, `RequiredTagsBuilder`, `RouterF` и causal path.

Без PoM v0.4 technical v0.4 не запускается в D0. В остальном этот документ — инженерная реализация PoM v0.4.

### 0.6. Оценка объёма

Ориентир **3 800–6 100 строк** harness’а. 5k — не потолок; если access boundaries, blinding, human workflow требуют 5.5–6k — это нормально и предпочтительнее, чем экономить на воспроизводимости.

---

## 1. Scope

- 6 development tasks из публичного Terminal-Bench-Science (seeded selection из frozen snapshot; отдельная D0). Это локальный development corpus проекта, не официальный development split. Замена SCI-AGENT-EXAM принята владельцем до model calls.
- 8 workers × \(T_{\max}=4\).
- 1 author call × 6.
- `valid/def` pair × 6.
- 3 conditions × 2 repeats × 2 variants = 72 panels.
- 144 review calls.
- Terra: 144 + ~29 retest.
- Human: 36 × 2 calibration + adjudications.

---

## 2. Системный контекст

### 2.1. Акторы

| Актор | Видит | Не видит |
|---|---|---|
| Run operator | task, workers, panels | Terra/human labels до freeze |
| Human validator | task, q0, worker outputs | review outcomes, condition |
| Terra evaluator | blind evaluation packet | condition, reviewer ID, target defect, valid/def, informed workers |
| Human calibration rater A/B | blind human packet | Terra label, condition, reviewer ID |
| Human adjudicator | blind packet + disagreement | routing metadata |
| Analyst | frozen logs/labels | ничего после freeze |

### 2.2. Внешние системы

- OpenAI Responses API: `gpt-6-luna`, `gpt-6.1-terra`.
- Terminal-Bench-Science, frozen commit и SHA-256 manifest в `data/terminal-bench-science/`.
- Docker `--network=none`.
- Файловая структура + immutable JSONL.
- CSV / lightweight annotation workflow.

### 2.3. Модельная политика

Одна модель, одна конфигурация во всех in-system ролях: `gpt-6-luna`, Standard, medium. `temperature/top_p/top_logprobs` не задаются. Fallback запрещён. Terra — offline evaluator, Standard, medium.

---

## 3. Архитектура

### 3.1. Слои

```
┌────────────────────────────────────────────────────────┐
│ A6 Analysis                                             │
│    coverage (private primary + public diagnostic)       │
│    detection · FP · go/no-go · measurement quality      │
├────────────────────────────────────────────────────────┤
│ A5 Measurement                                          │
│    TerraEvaluator · HumanCalibration · Adjudicator      │
│    TerraRetest · MeasurementQualityGate                 │
├────────────────────────────────────────────────────────┤
│ A4 Review Pipeline                                      │
│    ReviewerPacketBuilder · ReviewRunner · RunList       │
│    IdempotencyController                                │
├────────────────────────────────────────────────────────┤
│ A3 Routing                                              │
│    Fresh · RandomHistory · RoutedHistory · RouterF      │
│    CoverageCalculator (variant-aware private + public)  │
├────────────────────────────────────────────────────────┤
│ A2 Cards & Evidence                                     │
│    ProposalCard · ExperimentalCard · CardBuilder        │
│    EvidenceRegistry · MechanismReference                │
│    RequiredTagsBuilder · DefectApplicabilityMatcher     │
├────────────────────────────────────────────────────────┤
│ A1 Worker & Author                                      │
│    WorkerRunner · AuthorRunner · WorkProfileBuilder     │
├────────────────────────────────────────────────────────┤
│ A0 Data & Model                                         │
│    TaskLoader · Sandbox · LunaClient · TerraClient      │
│    ImmutableLogger · IdempotencyStore                   │
└────────────────────────────────────────────────────────┘
```

Зависимости: каждый слой зависит только от нижележащих. A4 не знает о A5. A5 не знает о A6. A3 не знает о A4. IdempotencyStore используется LunaClient и ReviewRunner через C0.5/C0.6.

### 3.2. Компоненты

| ID | Компонент | Слой |
|---|---|---|
| C0.1 | TaskLoader | A0 |
| C0.2 | Sandbox | A0 |
| C0.3 | LunaClient | A0 |
| C0.4 | TerraClient | A0 |
| C0.5 | ImmutableLogger | A0 |
| C0.6 | IdempotencyStore | A0 |
| C1.1 | WorkerRunner | A1 |
| C1.2 | AuthorRunner | A1 |
| C1.3 | WorkProfileBuilder | A1 |
| C2.1 | EvidenceRegistry | A2 |
| C2.2 | RequiredTagsBuilder | A2 |
| C2.3 | CardBuilder | A2 |
| C2.4 | MechanismReferenceBuilder (variant-aware) | A2 |
| C2.5 | DefectApplicabilityMatcher | A2 |
| C3.1 | RouterF | A3 |
| C3.2 | RandomRouter | A3 |
| C3.3 | FreshProvider | A3 |
| C3.4 | PanelBuilder | A3 |
| C3.5 | CoverageCalculator | A3 |
| C4.1 | ReviewerPacketBuilder | A4 |
| C4.2 | ReviewRunner | A4 |
| C4.3 | RunList | A4 |
| C4.4 | PanelOutcomeProjector (incomplete handling) | A4 |
| C5.1 | TerraPacketBuilder | A5 |
| C5.2 | TerraEvaluator | A5 |
| C5.3 | CalibrationSampler | A5 |
| C5.4 | HumanCalibration | A5 |
| C5.5 | Adjudicator | A5 |
| C5.6 | TerraRetest | A5 |
| C5.7 | MeasurementQualityGate | A5 |
| C6.1 | Analysis | A6 |
| C6.2 | GoNoGoEvaluator | A6 |

---

## 4. Data model

### 4.1. Task (без `action_class`)

```python
class Task(BaseModel):
    task_id: str
    goal: str
    parent_task: str
    allowed_inputs: list[str]
    constraints: list[str]
    completion_criteria: list[str] | None
    snapshot_sha256: str
    # action_class УДАЛЁН из causal path (P1)
```

### 4.2. EvidenceFragment (identity — computed property)

```python
class EvidenceFragment(BaseModel):
    source_sha256: str
    source: str
    fragment: str
    fragment_sha256: str

    @computed_field
    @property
    def evidence_id(self) -> str:
        return sha256(canonical_jcs({
            "source_sha256": self.source_sha256,
            "fragment_sha256": self.fragment_sha256,
        }))
```

`evidence_id` **не принимается снаружи** (P1); validator вычисляет его из `source_sha256 + fragment_sha256`. Дублирующего `identity` нет.

```python
class WorkerEvidence(BaseModel):
    worker_id: str
    evidence_id: str
```

### 4.3. WorkerOutput

```python
class WorkerOutput(BaseModel):
    worker_id: str
    subtask_type: str
    subtask_text: str
    turns_used: int
    result: str
    evidence_ids: list[str]
    limitations: list[str]
    complete: bool
```

### 4.4. WorkProfile (без self-SHA)

```python
class WorkProfile(BaseModel):
    worker_id: str
    tags: list[str]
    # profile_sha256 УДАЛЁН из payload (P1); хеш — в sidecar/freeze manifest
```

### 4.5. ProposalCard / ExperimentalCard

Без `action_class`. Иначе как v0.3.

### 4.6. RequiredTags (без `action_class`)

```python
class RequiredTags(BaseModel):
    task_id: str
    tags: list[str]
    source: Literal["q0", "valid"]
    frozen_at: str
```

### 4.7. MechanismReference (variant-aware)

```python
class MechanismReference(BaseModel):
    task_id: str
    target_defect_type: str
    target_evidence_ids: list[str]                                   # E_i^target
    informed_workers: list[str]                                      # I_i
    fresh_area_valid: list[str]                                      # V_i^Fresh,valid
    fresh_area_def: list[str]                                        # V_i^Fresh,def
    private_target_evidence_valid: list[str]                         # E_i^{private,valid}
    private_target_evidence_def: list[str]                           # E_i^{private,def}
    private_target_evidence_strict: list[str]                        # E_i^target \ (V_valid ∪ V_def)
    informed_private_workers_valid: list[str]                        # I_i^{private,valid}
    informed_private_workers_def: list[str]                          # I_i^{private,def}
    informed_private_workers_strict: list[str]                       # I_i^{private,strict}
```

Правила построения:
\[
E_{i,v}^{private} = E_i^{target} \setminus V_i^{Fresh,v}, \quad v \in \{\text{valid}, \text{def}\}
\]
\[
E_i^{private,strict} = E_i^{target} \setminus (V_i^{Fresh,valid} \cup V_i^{Fresh,def})
\]
\[
I_{i,v}^{private} = \{j \in I_i : Evidence(w_j) \cap E_{i,v}^{private} \neq \emptyset\}
\]

**Primary** — для def (`I_{i,def}^{private}`). **Diagnostic** — valid. **Строгий вариант** (`I_i^{private,strict}`) публикуется; если непуст, считается наиболее чистым mechanistical evidence.

### 4.8. ReviewerPacket / TerraPacket

Без изменений по blinding относительно v0.3; добавлен `n_reviewers_observed` в `PanelOutcome`.

### 4.9. Panel / ReviewerSpec

```python
class ReviewerSpec(BaseModel):
    reviewer_key: str
    worker_id: str | None

class Panel(BaseModel):
    panel_id: str
    task_id: str
    variant: Literal["valid", "def"]
    condition: Literal["Fresh", "RandomHistory", "RoutedHistory"]
    repeat: Literal[1, 2]
    reviewer_specs: list[ReviewerSpec]
    router_fallback: bool
```

### 4.10. PanelOutcome (P1: incomplete handling)

```python
class PanelOutcome(BaseModel):
    panel_id: str
    task_id: str
    variant: Literal["valid", "def"]
    condition: Literal["Fresh", "RandomHistory", "RoutedHistory"]
    repeat: int
    n_reviewers_requested: Literal[2]
    n_reviewers_observed: int             # 0, 1, 2
    reviews: list[ReviewOutput]
    status: Literal["complete", "partial", "incomplete"]
```

Правила:
- `complete` — оба reviewer call succeeded (valid `ReviewOutput`);
- `partial` — один succeeded; outcome считается по доступному reviewer; `n_reviewers_observed=1`; публикуется отдельно;
- `incomplete` — ни одного; panel не входит в detection/FP denominator, но публикуется как `incomplete` для missingness.

### 4.11. TerraLabel / HumanCalibrationLabel

Без изменений.

### 4.12. Directory layout

```
mini_pom/
  config/
    d0_freeze_manifest.json
    final_freeze_manifest.json
    config.yaml
    vocabulary.yaml
    equivalence_map.yaml
    thresholds.yaml
    seeded_task_order.json
    defect_applicability.json
    work_profiles_sha.json           # sidecar для WorkProfile

  tasks/<task_id>/
    task.json
    inputs/
    workers/{w1..w8}.json
    author/q0.json
    cards/{valid.json, def.json}
    evidence/{registry.json, mechanism_reference.json}
    panels/<panel_id>.json
    intent/<execution_id>.json
    response/<execution_id>.json
    review_packets/<output_id>.json
    reviews/<output_id>.json
    panel_outcomes/<panel_id>.json
    terra_packets/<output_id>.json
    terra_labels/<output_id>.json
    human_calibration/<output_id>__<rater_id>.json
    adjudication/<output_id>.json

  raw/{luna_outputs.jsonl, terra_outputs.jsonl}
  runs.jsonl
  calibration_ids.json
  retest_ids.json
  analysis.ipynb
```

---

## 5. Компоненты и контракты

### 5.1. C0.1 TaskLoader (seeded ordering fixed)

**Правила:**
- применить §5.1 eligibility к полному development split;
- D0 task фиксируется **до** seeded selection и исключается из pool;
- seeded order — HMAC **per task**, не один hash:
  \[
  r_i = \text{HMAC}_{master\_seed}(\text{JCS}(\{\text{namespace:"task_order"},\ \text{task\_id}: i\}))
  \]
  sort by \(r_i\) ascending;
- первые 6 eligible task IDs → PoM corpus; freeze в `config/seeded_task_order.json`.

**Контракты:**
```python
TaskLoader.load(split_path, seed) -> {tasks, d0_task, seeded_pom_ids}
TaskLoader.verify_snapshot(task_id) -> bool
```

### 5.2. C0.2 Sandbox

Как в v0.3: `--network=none`, non-root, read-only, writable `/work/out`, CPU/RAM/time limits, без Docker socket, без credentials. API calls — снаружи контейнера.

### 5.3. C0.3 LunaClient + C0.6 IdempotencyStore (P0: crash safety)

**Idempotency protocol:**

Перед API call:
```
intent/<execution_id>.json:
  {execution_id, run_id, stage, request_hash, model, schema_id, ts}
```
После успешного response:
```
response/<execution_id>.json:
  {execution_id, request_hash, response_hash, usage_raw, model_metadata, ts}
```

Правила:
- `execution_id = hkey(master_seed, "execution_id", {run_id, stage, attempt, intent_hash})`;
- **safe retry** — только pre-send failures (DNS/connect до отправки) и explicit 429/5xx без accepted generation; тот же `execution_id`;
- **timeout/disconnect после возможной отправки** — intent без response; `ambiguous_remote_execution`; **не re-call**, run → `incomplete`;
- response exists → skip;
- intent без response после restart → `incomplete` (без операторского override);
- никакого второго model sample.

**Контракты:**
```python
LunaClient.call(packet, schema, execution_id) -> Parsed | Incomplete
IdempotencyStore.write_intent(record)
IdempotencyStore.write_response(record)
IdempotencyStore.lookup(execution_id) -> {intent?, response?}
```

### 5.4. C0.4 TerraClient

Как v0.3: offline only, frozen eval prompt, condition-blind, raw — в `raw/terra_outputs.jsonl`, без target defect в input.

### 5.5. C0.5 ImmutableLogger

Как v0.3. Поля: `execution_id`, `stage`, `task_id`, `worker_id | panel_id | output_id`, `request_hash`, `response_hash | None`, `usage_raw | None`, `latency_ms`, `model_metadata`, `raw_response | None`, `status`.

### 5.6. C1.1 WorkerRunner

Как v0.3: 8 subtasks, \(T_{\max}=4\), structured output, `complete=False` при пустом `result`/`limitations`, `EvidenceFragment` обязателен, workers не видят друг друга.

### 5.7. C1.2 AuthorRunner

Как v0.3.

### 5.8. C1.3 WorkProfileBuilder

**Правила:**
- vocabulary frozen в `config/vocabulary.yaml`;
- теги — extractive matches по `subtask_type`, `result`, `evidence_ids`, `limitations`;
- не использует: target defect, `informed_workers`, human labels, `MechanismReference`, `action_class`;
- детерминирован (golden test);
- пустые `tags=[]` допустимы; это влияет на exploitability, не на pool;
- хеш `WorkProfile` — в sidecar `config/work_profiles_sha.json`, не в payload.

### 5.9. C2.1 EvidenceRegistry

Как v0.3 с P1 fix: `evidence_id` вычисляется, не принимается снаружи.

### 5.10. C2.2 RequiredTagsBuilder (без `action_class`)

**Правила:**
- вход: `q0` или `valid` (до defect injection);
- vocabulary — тот же, что у `WorkProfile`;
- extractive matches; без LLM свободного пересказа;
- не использует `action_class`, target defect, `MechanismReference`;
- если `|Req_i| = 0` → `routing_representation_failure`; router не сможет адресовать informed workers; episode может сохраняться как общая диагностика, но не как mechanism evidence.

### 5.11. C2.3 CardBuilder

Как v0.3: `valid` — минимальная правка `q0` или подтверждение; `def` — `valid` + один target defect; human validator подтверждает 5 пунктов.

### 5.12. C2.4 MechanismReferenceBuilder (variant-aware)

**Вход:** task, workers, registry, `valid`, `def`, common dossier.

**Алгоритм:**
```python
V_valid = Task ∪ CommonDossier ∪ valid
V_def   = Task ∪ CommonDossier ∪ def
E_target = target_evidence
E_priv_valid = E_target \ V_valid
E_priv_def   = E_target \ V_def
E_priv_strict = E_target \ (V_valid ∪ V_def)

I_i = {j ∈ R_i : Evidence(w_j) ∩ E_target ≠ ∅}
I_priv_valid  = {j ∈ I_i : Evidence(w_j) ∩ E_priv_valid ≠ ∅}
I_priv_def    = {j ∈ I_i : Evidence(w_j) ∩ E_priv_def ≠ ∅}
I_priv_strict = {j ∈ I_i : Evidence(w_j) ∩ E_priv_strict ≠ ∅}
```

Freeze до P4.

### 5.13. C2.5 DefectApplicabilityMatcher (P1)

**Задача:** pre-outcome matching между 6 tasks и 6 defect types.

**Правила:**
- для каждой task human validator размечает `A_{i,d} ∈ {0,1}` — применим ли defect type \(d\);
- seeded perfect bipartite matching (`scipy.optimize.linear_sum_assignment` с HMAC tie-break по `namespace="defect_matching"`);
- matching freeze в `config/defect_applicability.json` **до** P3 (card construction);
- если perfect matching не существует, task заменяется следующей по seeded order; consumed task не возвращается.

Это заменяет 1:1 seeded вслепую, при котором episode погибал бы из-за невозможности инъекции, а не из-за механизма.

### 5.14. C3.1 RouterF (без `action_class`)

**Вход:** `Task`, `RequiredTags`, `WorkProfile[]`, seed. **Не получает** `MechanismReference`.

**Алгоритм:** как v0.3; только `RequiredTags` и `WorkProfile`; никакого `action_class`.

`router_fallback=True` логируется; одна panel для `valid/def` на основе `RequiredTags` из `valid`.

### 5.15. C3.2 RandomRouter / C3.3 FreshProvider

Как v0.3.

### 5.16. C3.4 PanelBuilder

Как v0.3: 72 panels, `panel_id = sha256(JCS({task_id, variant, condition, repeat}))`, freeze до первого review call.

### 5.17. C3.5 CoverageCalculator (variant-aware, private primary)

**Public (diagnostic):**
\[
q_i^U = 1 - \binom{m_i - |I_i|}{2} / \binom{m_i}{2}
\]
\[
C_i(S) = \mathbf{1}\{S \cap I_i \neq \emptyset\}
\]

**Private, для каждой variant \(v \in \{\text{valid}, \text{def}\}\):**
\[
q_{i,v}^{U,private} = 1 - \binom{m_i - |I_{i,v}^{private}|}{2} / \binom{m_i}{2}
\]
\[
C_{i,v}^{private}(S) = \mathbf{1}\{S \cap I_{i,v}^{private} \neq \emptyset\}
\]

**Strict private:**
\[
q_i^{U,private,strict} = 1 - \binom{m_i - |I_i^{private,strict}|}{2} / \binom{m_i}{2}
\]
\[
C_i^{private,strict}(S) = \mathbf{1}\{S \cap I_i^{private,strict} \neq \emptyset\}
\]

**Правила:**
- **Primary GO coverage criterion (PoM v0.4 §4.4):** для \(v=\text{def}\) — \(C_{i,\text{def}}^{R,private} > q_{i,\text{def}}^{U,private}\) на ≥4/6;
- diagnostic: \(v=\text{valid}\), strict, public;
- публикуются все четыре пары (R vs U, private vs public, valid vs def).

### 5.18. C4.1 ReviewerPacketBuilder

Как v0.3: blinding, golden test; `worker_context=None` для Fresh.

### 5.19. C4.2 ReviewRunner + C4.4 PanelOutcomeProjector

**ReviewRunner:**
- 2 calls per panel;
- каждый call — через IdempotencyStore (intent + response);
- `Incomplete` (schema invalid, semantic failure, ambiguous transport) → reviewer slot помечается `failed`, но **не** делает panel incomplete автоматически;
- raw — в `raw/luna_outputs.jsonl`;
- parsed — в `reviews/<output_id>.json`.

**PanelOutcomeProjector:**
- `n_reviewers_observed` = число succeeded reviewer calls;
- `status = "complete"` если `n_reviewers_observed == 2`;
- `status = "partial"` если `n_reviewers_observed == 1`; outcome считается по доступному reviewer; публикуется отдельно;
- `status = "incomplete"` если `n_reviewers_observed == 0`; panel не входит в detection/FP denominator, но публикуется как missingness.

### 5.20. C4.3 RunList

Как v0.3: seeded order `hkey(master_seed, "run_order", {})`; resume по существующим `panel_id`; idempotency через C0.6.

### 5.21. C5.1 TerraPacketBuilder / C5.2 TerraEvaluator

Как v0.3. Terra получает по одному `TerraPacket` на каждый успешный `ReviewOutput`; failed slots не отправляются.

### 5.22. C5.3 CalibrationSampler

Как v0.3: 36 outputs (12/12/12 по condition), внутри — баланс valid/def; sampled **до** первого Terra label; freeze.

### 5.23. C5.4 HumanCalibration

Как v0.3: два raters; не видят Terra/condition/reviewer ID; freeze.

### 5.24. C5.5 Adjudicator

Как v0.3: human-human disagreement, `Terra.uncertain`, `valid ∧ TerraPositive`.

### 5.25. C5.6 TerraRetest

Как v0.3: 20% sample; freeze до Terra pass; agreement по 4 осям; `Agreement_T^{claim}` — gate.

### 5.26. C5.7 MeasurementQualityGate (P1: denominator-aware)

**Правила:**
- `Agreement_T^{claim} >= 0.70` → gate pass;
- human calibration BA — **diagnostic**, не жёсткий gate, кроме случая явной insensitivity:
  - публикуется BA с denominator по каждому классу (`n_human_positive`, `n_human_negative`);
  - если `n_human_positive < 8` или `n_human_negative < 8` → BA не используется как gate; публикуется как diagnostic с явной пометкой;
  - если оба класса ≥8 и BA < 0.60 с верхней границей 95% CI < 0.70 → `measurement_inconclusive`;
- при провале `Agreement_T^{claim}` → `MEASUREMENT INCONCLUSIVE` (не `NO-GO`);
- порог 0.70 — нормативный gate; порог BA — условный, с denominator gate. **Оба должны быть внесены в PoM v0.4** (см. §0.5).

### 5.27. C6.1 Analysis

Как v0.3, плюс:
- покрытие публикуется в 4 вариантах (public, private-valid, private-def, strict);
- `n_reviewers_observed` распределение публикуется;
- detection/FP считаются по `complete` panel, а `partial` — отдельно, чтобы не смешивать;
- bootstrap CI не интерпретируется как p-value.

### 5.28. C6.2 GoNoGoEvaluator

**Branches:**
- GO — §34.1 PoM v0.4 (private coverage primary);
- CONDITIONAL GO — §34.2, «большинство» = ≥4/6;
- REDESIGN-routing-representation — \(I_i^{exploit} = \emptyset\) на ≥4/6 **или** \(\rho_i^{high} < \tau_\rho\) на ≥4/6;
- REDESIGN-mechanism — exploitability есть, coverage gain нет на ≥4/6;
- MEASUREMENT INCONCLUSIVE — §34.5;
- **NO-GO (differentiation)** — §4.1 fail (m_i < 6 на ≥2/6) **или** §4.2 fail (private evidence <4/6).

---

## 6. Cross-component rules

| Правило | Где проверяется |
|---|---|
| Одна модель, одна конфигурация | C0.3, config freeze |
| Standard, medium, no temperature | C0.3 |
| No fallback | C0.3 |
| **Seeded order — HMAC per task_id + sort** | C0.1 |
| D0 task вне seeded pool | C0.1 |
| Network disabled | C0.2 |
| **Idempotency: intent + response; ambiguous → incomplete** | C0.6, C0.3, C4.2 |
| Structured outputs | C1.1, C4.2, C5.2 |
| `complete=False` вне reviewer pool | C1.1 |
| **`evidence_id` вычисляется, не принимается снаружи** | C2.1 |
| Provenance отдельно | C2.1 |
| Work tags из фактического output | C1.3 |
| WorkProfileBuilder не видит mechanism reference / action_class | C1.3 |
| **WorkProfile без self-SHA (sidecar)** | C1.3, config |
| RequiredTags без `action_class` / target defect | C2.2 |
| `|Req_i| = 0` → routing_representation_failure | C2.2 |
| **Variant-aware private evidence: valid, def, strict** | C2.4 |
| **Defect applicability matching pre-outcome** | C2.5, config |
| Router не видит target/informed | C3.1 |
| Routed panel одинакова для valid/def | C3.1, C3.4 |
| Random panel до model call | C3.2 |
| Panel freeze до review | C3.4 |
| **Private coverage — primary; public — diagnostic** | C3.5, C6.2 |
| Reviewer не видит condition | C4.1 |
| **Incomplete reviewer: partial сохраняется** | C4.4 |
| Terra не видит condition/target/valid-def | C5.1 |
| Human raters не видят Terra/condition | C5.4 |
| Calibration IDs freeze до Terra | C5.3 |
| Retest IDs freeze до Terra pass | C5.6 |
| Terra-positive valid → adjudication | C5.5 |
| Terra uncertain → adjudication | C5.5 |
| Detection через Terra-normalized type + equivalence map | C6.1, C5.2 |
| FP только после adjudication | C6.1, C5.5 |
| **MeasurementQualityGate denominator-aware** | C5.7 |
| No p-value claims | C6.1 |
| Episode — единица интерпретации | C6.1 |
| Raw outputs append-only | C0.5 |
| `Incomplete` не retry молча | C0.6, C0.3, C4.2 |
| `hkey` + JCS для критичной случайности | C0.3, C3.1, C3.2, C4.3, C5.3, C5.6, C0.1, C2.5 |
| **Никаких изменений после final freeze** | change control |

---

## 7. Стек

| Слой | Выбор |
|---|---|
| Язык | Python 3.11+ |
| Пакеты | `uv` + `uv.lock` |
| Схемы | Pydantic v2 |
| Canonical JSON | RFC 8785 JCS |
| Конфиг | plain YAML + Pydantic validation; `final_freeze_manifest.json` |
| Model client | официальный OpenAI SDK |
| Retry | `tenacity` для pre-send; semantic — `incomplete` |
| Логи | `structlog` → JSONL append-only |
| Sandbox | Docker `--network=none` |
| Analysis | pandas, numpy |
| Notebook | Jupyter |
| Tests | pytest + golden files |
| Human workflow | CSV с frozen ID mapping |

Не используются: Argilla, event sourcing, CAS, ACL, blocked randomization, Hydra, DVC, графовая БД, LiteLLM, Inspect AI, cost normalization.

---

## 8. Phases (двухступенчатый freeze)

| Phase | Действие | Freeze |
|---|---|---|
| **P0a** | D0 development freeze: config, vocabulary, equivalence_map, thresholds, subtask templates, schemas, prompts | `d0_freeze_manifest.json` |
| **P1a** | D0 task: 8 workers, author, cards, mechanism, panels, reviews, Terra packets, 4–6 human calibration; end-to-end trace | `tasks/<d0>/` |
| **P0b** | **Fix window:** разрешено менять vocabulary, tags, prompts, schemas, decomposition, thresholds, `\tau_{tag}`, `\tau_\rho`; **нельзя** — estimand, router architecture, private/public gates | — |
| **P0c** | **Final PoM freeze:** все pre-pilot артефакты + seeded task order (HMAC per task) + applicability matrix + 6 PoM IDs | `final_freeze_manifest.json` |
| **P1** | 8 workers × 6 tasks | `workers/` |
| **P2** | Work profiles + author q0 | `work_profiles_sha.json`, `author/q0.json` |
| **P3** | Human validator: valid/def, variant-aware MechanismReference, applicability matrix | `cards/`, `evidence/`, `config/defect_applicability.json` |
| **P4** | DefectApplicabilityMatcher; PanelBuilder: 72 panels; freeze; compute \(I^{exploit}\), \(\rho\) diagnostics | `panels/`, `panels_freeze_sha` |
| **P5** | 144 review calls с idempotency | `intent/`, `response/`, `reviews/`, `panel_outcomes/` |
| **P6** | CalibrationSampler + TerraRetest; freeze IDs | `calibration_ids.json`, `retest_ids.json` |
| **P7** | Terra evaluates 144 + retest | `terra_labels/` |
| **P8** | Human calibration + adjudication | `human_calibration/`, `adjudication/` |
| **P9** | MeasurementQualityGate; Analysis; GoNoGo | `analysis.ipynb`, `GoNoGoReport` |

Порядок необратим. P5 не начинается до P4 freeze; P7 не начинается до P6 freeze; P9 не начинается до P8 freeze. **P0c нельзя пересматривать после первого worker call на PoM task.**

### 8.1. Freeze manifests

- `d0_freeze_manifest.json` — только для D0; можно менять после D0 в fix window.
- `final_freeze_manifest.json` — immutable после P0c.

SHA: task snapshots, seeded order, config, vocabulary, equivalence_map, thresholds, subtask templates, applicability matrix, `work_profiles_sha`, panels_freeze_sha, calibration_ids, retest_ids, mechanism reference.

---

## 9. Тестирование

1. **Schema tests:** golden для `Task` (без `action_class`), `WorkerOutput`, `WorkProfile` (без self-SHA), `ProposalCard`, `ExperimentalCard`, `RequiredTags`, `MechanismReference` (variant-aware), `ReviewerPacket`, `TerraPacket`, `ReviewOutput`, `TerraLabel`, `HumanCalibrationLabel`, `Panel`, `PanelOutcome`.
2. **JCS tests:** golden vectors; `-0`, exponent, Unicode.
3. **`evidence_id` tests:** computed property; нельзя передать снаружи; одинаковый fragment у разных workers → один `evidence_id`; разные source → разные.
4. **`hkey` tests:** детерминизм; domain separation.
5. **Seeded ordering tests:** HMAC per task_id + sort; D0 task исключён; freeze до P0c.
6. **Idempotency tests:** intent без response после restart → `incomplete`, не re-call; response exists → skip; pre-send retry → тот же `execution_id`; timeout after send → `incomplete`.
7. **`WorkProfileBuilder` tests:** golden; детерминизм; отсутствие доступа к mechanism reference / action_class.
8. **`RequiredTagsBuilder` tests:** из q0/valid; без action_class; `|Req_i| = 0` → `routing_representation_failure`.
9. **`MechanismReferenceBuilder` tests:** variant-aware; `E_priv_valid`, `E_priv_def`, `E_priv_strict`; `I_priv_def` как primary.
10. **DefectApplicabilityMatcher tests:** seeded perfect matching; infeasible → task replacement; freeze.
11. **`RouterF` tests:** argmax, tie-break, complementary, fallback; одна panel для valid/def; не получает `MechanismReference`.
12. **Panel freeze tests:** построенная после первого review call → fail.
13. **`ReviewerPacket` blinding:** golden.
14. **`TerraPacket` blinding:** golden.
15. **PanelOutcomeProjector tests:** `complete` (2), `partial` (1), `incomplete` (0); partial сохраняется; `n_reviewers_observed` публикуется.
16. **Coverage tests:** public, private-valid, private-def, strict; **GO criterion по private-def**; публикуются все.
17. **`CalibrationSampler` tests:** 12/12/12; баланс valid/def; freeze до Terra.
18. **`TerraRetest` tests:** 20% sample; freeze; agreement по 4 осям.
19. **`HumanCalibration` tests:** blinding; freeze до сравнения.
20. **`Adjudicator` tests:** обязательные items; adjudicator не в первичной разметке.
21. **Detection criterion tests:** Terra-normalized type + equivalence_map; partial → `D=0`.
22. **FP criterion tests:** только после valid adjudication.
23. **`MeasurementQualityGate` tests:** retest ≥0.70; BA denominator gate; малые классы → diagnostic; явная insensitivity → `MEASUREMENT INCONCLUSIVE`.
24. **`GoNoGoEvaluator` tests:** все branches, включая NO-GO (differentiation); численное «большинство» ≥4/6.
25. **`Analysis` tests:** 4 варианта coverage; partial panels отдельно; episode — единица.
26. **End-to-end D0 test:** D0 task: 8 workers → q0 → valid/def → variant-aware mechanism → panels → reviews (idempotency) → Terra packets → 4–6 human calibration → Terra labels → analysis.
27. **Change control tests:** после P0c изменение vocabulary/thresholds → fail; D0 fix window разрешён только между P1a и P0c.

---

## 10. Risks and gates

| Риск | Митигация |
|---|---|
| Work pool не различается | P1 gate ≥5/6 с \(m_i \ge 6\) |
| Private evidence пуст | P4 gate ≥4/6 с \(\|E_{i,\text{def}}^{private}\| \ge 1\) |
| Exploitability пуст | P4 diagnostic; REDESIGN-routing-representation |
| Router fallback частый | gate ≤20% routed panels |
| Panel не frozen до review | P4 freeze |
| Reviewer/Terra/Human видят лишнее | packet golden-tests |
| **Двойной model sample после сбоя** | IdempotencyStore; ambiguous → incomplete |
| **Protocol drift по coverage** | PoM v0.4 required delta |
| **Seeded ordering bug** | HMAC per task_id + sort |
| **D0 слишком поздно** | двухступенчатый freeze |
| **Private-evidence valid/def ambiguity** | variant-aware mechanism |
| **Self-SHA в payload** | `evidence_id` computed; `profile_sha256` — sidecar |
| **`action_class` рудимент** | удалён из Task/RequiredTags/Router |
| **BA gate нестабилен** | denominator-aware; малые классы → diagnostic |
| **1:1 defect assignment вслепую** | DefectApplicabilityMatcher pre-outcome |
| **Partial panel отбрасывается** | PanelOutcomeProjector |
| Terra unstable | C5.7 + §34.5 |
| Freeze нарушен | SHA manifest; change control |
| Bootstrap → p-value overclaim | C6.1 |

---

## 11. Freeze and change control

1. `d0_freeze_manifest.json` — D0 scope; можно менять в fix window (P0b).
2. `final_freeze_manifest.json` — immutable после P0c.
3. `panels_freeze_sha` — до первого review call.
4. `calibration_ids.json`, `retest_ids.json` — до первого Terra call.
5. `human_labels_freeze_sha` — до сравнения с Terra.
6. После первого review call (на PoM task) нельзя менять: router rules, panel construction, card contents, mechanism reference, review prompt.
7. После первого Terra label нельзя менять: Terra prompt, equivalence_map, calibration IDs, retest IDs.
8. После первого human label нельзя менять: detection/FP criterion, blinding packets.
9. Любое изменение = новая версия PoM v0.4+ и новые development tasks (development-consumed не возвращаются).

---

## 12. Milestones

| Milestone | Что готово | Срок (1–2 инженера) |
|---|---|---|
| **M0a: PoM v0.4 + D0 prerequisites** | required delta в PoM v0.4; config, vocabulary, equivalence_map, thresholds, schemas, prompts; `d0_freeze_manifest`; seeded ordering (HMAC per task); golden tests | 4–6 дней |
| **M0b: D0 development** | D0 task end-to-end; idempotency; variant-aware mechanism; partial panel; 4–6 human calibration | 1 неделя |
| **M0c: fix window** | правки vocabulary, thresholds, prompts, schemas; финальный freeze; seeded 6 PoM IDs; applicability matrix | 3–5 дней |
| **M1: worker + author + profile** | 8 workers × 6; WorkProfile sidecar | 1.5–2 недели |
| **M2: cards + mechanism** | valid/def; variant-aware mechanism; RequiredTags | 1 неделя |
| **M3: routing + panels** | RouterF, RandomRouter, PanelBuilder, CoverageCalculator (4 варианта) | 1 неделя |
| **M4: reviews + idempotency** | 144 calls; intent/response; PanelOutcomeProjector | 1 неделя |
| **M5: measurement** | TerraPacketBuilder, TerraEvaluator, CalibrationSampler, HumanCalibration, Adjudicator, TerraRetest, MeasurementQualityGate (denominator-aware) | 1.5–2 недели |
| **M6: analysis** | Analysis (4 coverage variants), GoNoGoEvaluator (all branches) | 3–5 дней |

Итого: **6–9 недель** для 1–2 инженеров.

---

## 13. D0 acceptance

D0 на одной D0 task (не входит в 6 PoM IDs), **до** P0c final freeze.

Обязательные проверки:

1. `d0_freeze_manifest` frozen; D0 task вне seeded pool.
2. 8 workers; ≥6 complete.
3. `evidence_id` computed; provenance отдельно.
4. Work profiles различаются; `WorkProfile` без self-SHA.
5. Author `q0`.
6. Valid/def; variant-aware `MechanismReference` (valid, def, strict).
7. `I_i^{private,def}` вычислен; диагностика valid/strict.
8. RequiredTags без `action_class`; `|Req_i| = 0` обработан.
9. RouterF/RandomRouter; router_fallback логируется.
10. ReviewerPacket/TerraPacket blind.
11. **Idempotency:** intent/response для каждого call; simulated crash → `incomplete`, не re-call.
12. **PanelOutcome:** complete/partial/incomplete; `n_reviewers_observed`.
13. Reviews парсятся; `Incomplete` логируется.
14. Calibration/retest IDs freeze до Terra.
15. Terra labels structured; equivalence_map применяется.
16. 4–6 human calibration; adjudication.
17. **Coverage (4 варианта), detection, FP** end-to-end.
18. **MeasurementQualityGate** (denominator-aware) выдаёт verdict.
19. **GoNoGoEvaluator** по всем branches.
20. Freeze manifests проверяются.
21. **Fix window** открыт; после правок — final freeze и seeded selection 6 PoM IDs.

Если D0 не проходит — PoM не масштабируется; перерабатывается decomposition, vocabulary или criterion, не harness. Если D0 проходит, но fix window требует существенных изменений — это фиксируется в change log, final freeze включает их.

---

## 14. Итог

v0.4 закрывает 5 P0 и 6 P1/P2:

- **Protocol drift** закрыт явным required delta к PoM v0.4 (private coverage как primary, variant-aware mechanism, applicability matching, partial panel, двухступенчатый freeze, `action_class` removal).
- **Seeded ordering** — HMAC per task_id + sort.
- **D0 двухступенчатый:** D0 dev freeze → D0 → fix window → final freeze → seeded 6 PoM IDs.
- **Variant-aware private-evidence:** \(E_{i,\text{valid}}^{private}\), \(E_{i,\text{def}}^{private}\), \(E_i^{private,strict}\); primary — def.
- **Idempotency:** intent + response; ambiguous → incomplete; no silent re-call.
- **P1:** `evidence_id` computed; `profile_sha256` в sidecar; `action_class` удалён; MeasurementQualityGate denominator-aware; DefectApplicabilityMatcher pre-outcome; PanelOutcomeProjector с partial.
- **P2:** artifact ledger заменён на freeze manifest/sidecar; LOC 3.8–6.1k как ориентир, 5k не потолок.

Оценка: **3.8–6.1k строк**, **6–9 недель** для 1–2 инженеров. Первый deliverable — не новый документ, а одна полностью пройденная D0 task с сохранённой трассой `WorkerOutput → WorkProfile → PrivateEvidence → Router → Review → Terra → HumanCalibration`. Если трасса работает end-to-end и остаётся blind, дальнейший риск — исследовательский, а не инженерный.