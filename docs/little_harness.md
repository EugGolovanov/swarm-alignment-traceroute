# Little PoM harness

## Scope and verification status

The default Python 3.12 configuration implements the **three-task feasibility pilot**, not a shortened full-PoM confirmation experiment. Its corpus is Terminal-Bench-Science: `dna-storage-codec`, `nanoindentation-property-extraction`, and `finite-free-stam`. A separately authorized `sci_agent_exam_exploratory` configuration supports exactly the five public SCI-AGENT-EXAM examples; corpora cannot be mixed. Full-PoM 4/6 and 5/6 GO gates are not applied.

**Initial offline verification:** 373 tests passed and the CLI completed an offline pilot and unchanged-artifact resume. Subsequent live experiments and Docker builds are recorded in [sequential pilot launch](sequential_pilot_launch.md). The SCI integration full suite passed 563 tests; after its runtime-smoke correction and additional path-boundary tests, 141 targeted tests passed. All five SCI Docker runtimes and the actual Terra/Astra/Sol launcher ran; scientific measurement remained inconclusive. Runtime success does not establish benchmark correctness or human validity.

## Explicit sequential exploratory amendment

The operator-requested no-D0, task-major workflow is implemented separately as `--phase exploratory sequential-run`. It retains the original final/D0 gates for other phases, freezes all policies before samples, runs each task through reviews before the next task's workers, and performs one global calibration/retest export after all three tasks. See [sequential pilot launch](sequential_pilot_launch.md) for JSON subscription exchange, scientific preparation commands, and current observed blockers. Generated response templates contain null judgments, never fabricated Astra/Terra answers.

## Installation and offline execution

From the repository root:

```bash
uv sync --locked --group dev
uv run little-pom --run-dir runs/offline mock-e2e
uv run little-pom --run-dir runs/offline mock-e2e
uv run pytest
```

The second invocation resumes from immutable stage manifests. It does not request new worker, author, review, evaluation, or adjudication samples. `report.json` contains analysis; `manifests/` contains the stage dependency/hash audit chain. `stages/`, runtime intent/response records, `workers/`, `author/`, `cards/`, `reviews/`, and `measurement/` retain the detailed audit trace. A changed configuration, frozen source, dependency lock, source snapshot, or artifact is an error, not permission to regenerate samples.

`mock-e2e` composes real `TaskAdapter`, `Sandbox`, `WorkerRunner`, `EvidenceRegistry`, `AuthorRunner`, profile/requirements builders, `CardWorkflow`, `MechanismBuilder`, `PanelBuilder`, `ReviewRunner`, measurement export/import/adjudication, and `Analysis`. Only external model responses, sandbox inputs/execution, and subscription responses are explicitly synthetic. Mock transport responses are interpreted by production validation/scoring; no condition-specific hardcoded scores are inserted into analysis. Synthetic scientific input is marked in the text and fake validator trace. Eight actual bounded worker loops per task create frozen state; 36 real panels and 72 requested review slots follow from the design.

Equivalent individual stages:

```bash
uv run little-pom --run-dir runs/offline preflight
uv run little-pom --run-dir runs/offline freeze
uv run little-pom --run-dir runs/offline workers
uv run little-pom --run-dir runs/offline cards-export
uv run little-pom --run-dir runs/offline cards-import
uv run little-pom --run-dir runs/offline panels
uv run little-pom --run-dir runs/offline reviews
uv run little-pom --run-dir runs/offline eval-export
uv run little-pom --run-dir runs/offline eval-import
uv run little-pom --run-dir runs/offline analyze
```

All stages accept `--run-dir`, `--config`, and `--snapshot-root` before the stage name. Global options precede the stage. No arbitrary existing output file unlocks the next stage: prerequisite manifests must exist and verify. The freeze includes actual source files (therefore actual prompt implementations and schemas), `pyproject.toml`, `uv.lock`, snapshot manifest, and the canonical configuration. Dependencies are appended as stage manifests; final freeze artifacts are not rewritten.

## Safe preflight and remaining scientific blockers

```bash
uv run little-pom --run-dir runs/readiness preflight
uv run little-pom --run-dir runs/docker-readiness --inspect-docker preflight
```

Default preflight verifies the downloaded inventory and projects only authorized task instructions/image-input descriptors. It does not read credentials, build images, run containers, call models, or run an oracle. `--inspect-docker` enables read-only Docker resource/image metadata inspection, not a scientific build. The report distinguishes historical environment observations from current inspection.

Scientific readiness remains blocked until separate opt-in work establishes image provenance, usable non-root scientific toolchains, architecture compatibility, enough Docker VM RAM/CPU with headroom, and actual runtime/oracle behavior. The DNA directory-symlink verifier issue is reported as unresolved static evidence, not a reproduced or fixed upstream error. No upstream task files are changed. The owned isolation overlay is `src/little_pom/sandbox/Dockerfile`; science images and overlay builds must be requested separately. The package does not automatically authorize a build or oracle.

Workers use the operator-selected `openai-codex/gpt-6-luna` through the existing omp/pi Codex subscription. Author and reviewer roles remain `qwen3.5-397b` through Adaption. This is an explicit mixed-model partition, not a resumed homogeneous Qwen sample. Subscription authentication must not fall back to paid OpenAI API credentials. Astra validator/calibration/adjudication and Terra blind evaluation retain their existing roles. Mock execution does not verify live model access or scientific readiness. Qwen calls require an explicit `--secret-path`; subscription worker calls do not use that key. Scientific preflight remains fail-closed for the standard live lifecycle.

Live readiness is computed, not hardcoded: supply `--readiness PATH --inspect-docker` to preflight. The explicit JSON attestation contains `config_hash` (canonical SHA-256), `snapshot_sha256`, exact `models` entries (`model`, `mode`), pinned `images` by task ID, and `checks`. Each check is `{passed: true, path: "relative-evidence-file", sha256: "..."}`; the referenced regular file must be inside the attestation directory and match its digest. Required checks are `model_access`, `quota_and_budget`, `structured_output`, `subscription_context_isolation`, `<task_id>:runtime`, `<task_id>:oracle`, and `dna-storage-codec:verifier_resolution`. The current Docker inspection must independently show present provenance-matching images, architecture compatibility, and adequate RAM/CPU. These are explicitly **operator attestations backed by hashed artifacts**, not claims that the CLI reran scientific/model checks. Evidence files are included in final freeze verification.

Pass the same `--readiness PATH` to later live commands. Each live configuration/readiness revision gets a distinct immutable preflight manifest; unsuccessful observations do not prevent a later explicit preflight. Model/runtime readiness deliberately does not require D0 completion; the separate final-freeze lifecycle enforces D0, avoiding a circular gate. Frozen `models.worker.parameters` apply to Luna workers; `models.swarm.parameters` apply to Qwen author/reviewer. Unsupported transport-specific inference parameters are rejected.

## Freeze and explicit D0 lifecycle

D0 is never selected automatically and is not one of the three pilot tasks. `d0_task_id` defaults to null. The frozen default mock defect assignments are synthetic pre-outcome assignments, not proof of live applicability. Live applicability must be assessed before worker outcomes.

`ConfigFreeze(root)` provides `freeze_d0(config, task_id, artifacts)`, `record_d0_result(ServiceResult)`, `open_fix_window()`, and `freeze_final(config, artifacts)`. CLI freeze options are `--phase d0 --d0-task ID`, `--phase fix --responses D0_RESULT.json`, and `--phase final`. A separate designated task and a completed external D0 result are required before the fix window. During that window, permitted policy changes must preserve the estimand, routing architecture, and private/public gates. Final freeze records the consumed D0 task and does not claim independent confirmation. A mock freeze is an independent software exercise and never silently substitutes for the D0/final sequence.

The CLI uses distinct `freeze-d0`, `freeze-fix`, and `freeze-final` manifests, so a prior freeze does not mask a requested transition. The external D0 result must be a successful ServiceResult whose `data.task_id` matches the designated task and whose nonempty `data.evidence_artifacts` maps relative paths to actual SHA-256 digests; the CLI verifies those files before accepting the result. **Actual separate D0 execution is currently blocked by a genuine missing prerequisite:** this repository has only the three selected pilot tasks, not separately designated D0 data or its adapter. `--phase d0 workers` reports that exact blocker. The harness neither consumes a pilot task nor downloads a fourth task nor relabels synthetic execution as D0.

## Subscription exchange and blinding

Astra applicability packets are exported during freeze, before workers; `begin_workers` seals that applicability decision. After workers and author q0, valid-card import freezes `RequiredTags`. Only then is the defect packet exported. `cards-import` consumes a JSON object mapping opaque packet IDs to full validator responses via `--responses PATH` in non-mock mode. If the defect response is not yet present, the command exports the packet and stops without resampling; extend the explicit response bundle and resume. Accepted/rejected original responses remain in `cards/traces/`. Frozen accepted stages are not overwritten.

Before live workers, explicitly use `--card-phase applicability cards-export`, then `--card-phase applicability --responses PATH cards-import` with the same run/config/readiness options. That import has its own manifest and is a prerequisite for workers. Exact accepted responses resume without another import; conflicting accepted applicability is rejected. Subsequent ordinary `cards-export`/`cards-import` handle valid then def. Pending imports do not publish a terminal stage manifest, so additional responses can be supplied without overwriting accepted samples.

For explicitly configured live subscription exchange, construct `CardWorkflow(root, vocabulary, validator_model="openai-codex/gpt-6-astra")`; omitted configuration preserves legacy `"Astra"` packet identity. Live exports include the complete validator response contract and ExperimentalCard schema. Imports require the exact exported model/mode and model-supplied nonempty trace/timestamp; they never relabel a response.

`little_pom.runtime.subscription_exchange.SubscriptionExchange(exchange_root, client)` accepts an exact-model `SwarmClient`. `dispatch_card(workflow, packet, output_budget=8192)` dispatches/imports an exported card packet; `dispatch_evaluation(measurement_root, request, output_budget=2048)` dispatches/imports one exported measurement subscription request using `openai-codex/gpt-6.1-sol` or `openai-codex/gpt-6-astra`. Both use medium reasoning and empty history. Evaluation sends only the exported blind packet and prompt, then imports the ledger's original `output_text` with unchanged exported model/mode/date/context/prompt hash. Under the exchange root, `requests/{Ledger.key(execution_id)}.json`, `import_intent/{key}.json`, and `results/{key}.json` freeze dispatch/import state; execution IDs are `subscription-exchange:{card|evaluation}:{packet_id}`. Provider intent/raw response/result remain in the supplied client's ledger. Saved outcomes resume unchanged; uncertain execution or import completion cannot create a replacement sample.

Only scientific `{text: ...}` projections cross reviewer task/dossier/card boundaries. Reviewer history comes from a verified frozen worker snapshot, never a previous review. `Fresh` has no worker context. `ReviewRunner.run_all` uses one globally seeded dispatch plan and frozen concurrency; partial outcomes retain their actual observed denominator. Model calls use immutable intents and full raw responses; intent-only restart or ambiguous timeout is incomplete and cannot authorize a retry. SDK retries are zero; parse/semantic failures do not trigger repairs.

Worker completion requires a final answer with at least 20 characters grounded in independently verified evidence, or a substantive limitation tied to an observed tool failure. The loop allows at most four model turns and eight sequential tools per turn; final answers cannot request new tools. Full contexts are budgeted without silent truncation. Failed branches remain in the audit trace and are not replaced. If an episode cannot form a panel or author a card, other episodes continue, while analysis records that episode's missing panels/coverage instead of silently dropping the task.

Measurement files have deliberately separate privacy boundaries:

- Send **only** `measurement/subscription/{opaque_id}.json` to a fresh subscription context.
- Never send `measurement/private/`, routing metadata, worker identity, card variant, target defect, informed-worker sets, or complete target evidence lists.
- `eval-import --responses PATH` consumes a mapping from packet ID to `{raw_response, metadata}`. Metadata includes exact model, mode, date, context ID and prompt SHA-256. Responses and failed/duplicate attempts are retained. Correct IDs and metadata are mandatory.
- Calibration A and B are independent fresh Astra contexts, not independent humans. Retest is a separate Terra context. Adjudication uses another fresh Astra context and no previous labels.
- Required adjudication is exported only after primary/A/B inputs are available. Resume import with the new response bundle; original primary/retest/A/B labels remain immutable. Adjudication replaces scoring axes only in the final projection. Retest never votes.

The frozen sample policy is 36 calibration outputs, target 12 per condition, six per variant where possible. Variant shortages fill within that condition only; condition shortages retain all available without replacement or cross-condition borrowing. Retest is `ceil(successful outputs / 5)`. Both samples are frozen before the first evaluator label. Unresolved mandatory adjudication or missing responses blocks final FP reporting. Actual UI context isolation remains an operator obligation, not a claimed browser capability.

If collecting the remaining subscription responses is impossible, explicitly select `--finalize-incomplete eval-import` (with the available `--responses` bundle). This seals the available projection and permits `analyze` to produce missingness and `measurement_inconclusive`; it does not invent labels or permit later resampling. Without this flag, imports remain resumable and explain which packets are pending. Mandatory adjudication is built only when its primary/A/B prerequisites exist.

## Analysis interpretation

`Analysis.compute(panel_outcomes, labels, mechanism, policy)` returns a JSON report and does not mutate its inputs.

- Private-def coverage is primary. Public, private-valid, and strict-private are diagnostics. Expected random coverage is computed analytically as `1 - C(m - |I|, 2) / C(m, 2)`, not estimated from two sampled random panels. Realized panel coverage is separate.
- Exploitability retains actual profile overlap, informed/exploitable worker sets, and threshold diagnostics. Evidence use counts real target-evidence citations in reviews, not mere availability.
- Detection requires a significant claim, frozen defect-family equivalence, supported evidence, and decision relevance. Unsupported, irrelevant, uncertain, or mismatched claims do not become detections.
- Valid-card positives become FP only after required adjudication explicitly resolves them; a genuine missed defect in an accepted card is not automatically an FP.
- Primary detection/FP use complete panels. Partial and incomplete panels are separate, with review counts, missingness and denominators. Paired analysis averages both repeats within each episode and includes an episode only when both conditions and both repeats are complete and measured. Bootstrap resamples episodes, not individual reviews; its interval is not a p-value.
- Retest claim agreement must be at least 0.70. Missing frozen responses or insufficient measurement quality yields `measurement_inconclusive`, not routing NO-GO. Astra balanced accuracy reports positive/negative class denominators. Clear insensitivity is operationally gated only if both classes have at least eight observations, BA is below 0.60, and its upper 95% bound is below 0.70. The interval averages simultaneous exact binomial class bounds with Bonferroni coverage, rather than mislabeling independent marginal intervals as jointly 95%.
- Cross-model agreement is not human validity; correlated Astra/Terra errors remain possible. Full-PoM GO gates and independent scientific confirmation are explicitly false for this pilot.

## Observed verification

Verified on Python 3.12.12 with the locked environment:

```bash
uv run --locked --extra live pytest -q
uv run --locked --extra live little-pom --run-dir runs/verified-final mock-e2e
uv run --locked --extra live little-pom --run-dir runs/verified-final mock-e2e
uv run --locked --extra live little-pom --run-dir runs/verified-readiness-final --inspect-docker preflight
```

- Tests: **373 passed**. The optional Adaption SDK was exercised only through local fake/in-memory transport tests; no credentials or remote model calls were used.
- CLI: both invocations exited 0. The report contains three tasks, 36 complete panels, 72 reviews, and ten stage manifests.
- Resume: all **1,764 artifact paths and SHA-256 hashes** remained unchanged. Report: `runs/verified-final/report.json`; SHA-256 `cda298ad5f788dd83885f792cfd57010961d3e05350b4cc9f3517e7484cf20d7`.
- Read-only preflight verified all 92 inventoried source files. Docker Desktop reported that it was manually paused, despite `docker info` exiting 0. The harness correctly reports the daemon unavailable and current CPU/RAM unverified, not zero-resource shortages. No Docker state or machine resources were changed.
- `live_ready` and `scientific_readiness` remain false. Separate D0 data/adapter, model/subscription verification, scientific images/runtime/oracles, resources, and DNA verifier resolution remain external prerequisites.

Behavioral tests cover immutable resume and crash recovery, isolation and source provenance, stage ordering, failed episodes, incomplete measurements, leaks, freeze violations, analytic coverage, supported/relevant equivalence, complete-only pairing, FP adjudication, retest quality, and Astra class-denominator gating.

## Handoff preflight and saved-run diagnostics

`budgets.author_input_bytes` is now explicit (default 32768 UTF-8 bytes, not provider tokens). `AuthorRunner.inspect(task, handoffs, cited_evidence_ids=...)` measures the exact canonical host request using the same assembly as dispatch, itemizes each handoff, and checks citation membership. Rejection reports are persisted under `author/preflight/`; no silent truncation, citation repair, or retry is performed.

`AuthorRunner.plan(task, worker_count=..., worker_output_tokens=..., worker_max_turns=..., worker_handoff_bytes=...)` runs before workers in the CLI. Without a byte cap it reports an honest structural minimum and allocation warning, not an invented token-to-byte upper bound. With a per-handoff byte cap it reports a conditional finite maximum; callers must enforce that cap. Exact inspection still runs after handoffs exist.

The read-only saved-run audit is:

```bash
uv run --locked python -m little_pom.analysis.trace_audit \
  --run runs/sci-agent-exam-terra-sol \
  --output runs/swarm-design-diagnostics \
  --author-input-budget 32768
```

It refuses output overwrite and never changes the source run. `trace-audit.json` traces verified exact source/fragment identities through workers, author handoffs, proposal citations, cards and reviewer inputs. `trace-audit-summary.json` distinguishes public instructions, input sources and generated files. Exact-fragment uniqueness is not semantic novelty; packet availability is not demonstrated use.

Verification: full suite 590 passed. The actual audit ran across all five SCI tasks without model calls. Production author preflight on saved handoffs measured reconstructed requests of 43187 bytes for bioinformatics and 37423 for physics, both correctly rejected at 32768 and fitting an explicitly selected 262144-byte cap. These are current reconstructed host requests, not observed historical undispatched wire payloads. Results are in `runs/swarm-design-diagnostics/preflight-smoke.json`.
