# T01 — Контракты и frozen-конфигурация

## Цель и границы
Общие схемы и конфигурация трёхзадачного feasibility pilot. Полный PoM на 6 задач не запускаем; gates 4/6, 5/6 не переносим. Актуальные решения — `adaption_qwen_runbook.md`, затем little technical/main. Qwen — swarm; Astra — бывшие human-роли; Terra — evaluator.

## Владение файлами
`src/little_pom/contracts/`, `config/little/`, `tests/contracts/`. Не менять чужие модули, корневые зависимости и upstream snapshot.

## Контракт для всех восьми исполнителей
Python 3.12; package `little_pom`. Межмодульный обмен — JSON-compatible dict, валидируемый на границах. Общие поля: `schema_version`, непрозрачный `id`; внутренние записи также `task_id`. Packet ID не кодирует condition/variant. Пути передаются явно, без глобального mutable state.

Сервисы возвращают `{status: ok|incomplete, data: object|null, error: object|null}`. Статусы эпизода и panel — отдельные поля данных. Публичные интерфейсы перечислены в остальных тикетах; изменения — через согласованное обновление контрактов, не молча.

## Сделать
- Схемы Task, WorkerState/Output, Evidence, Profile, Card, Requirements, MechanismReference, Panel, Review, EvalLabel, Annotation, Packet, ServiceResult.
- Review: status, claim, evidence_ids, decision_relevance_text; EvalLabel: significant_claim, normalized_type, evidence_support, decision_relevance, uncertain.
- Config: seed, модели/режимы, budgets, vocabulary, equivalence mapping, prompts и SHA snapshot. Не выдумывать недоступные model snapshot IDs.
- Обязательные execution_id/request_hash; отдельные внутренние metadata и разрешённые packet schemas.
- Двухэтапный freeze: D0, затем final; stage manifests добавляются отдельно, без изменения final manifest.
- Зафиксировать seed/domain separation, сериализацию JCS и golden fixtures; секреты запрещены в config/logs.

## Параллельная работа
Не ждать реализаций: выдать fixtures всех границ и schema JSON. Остальные начинают с dict fixtures из своих тикетов; не создают альтернативный общий models.py. T08 владеет packaging и зависимостями; передать ему список требований.

## Приёмка
Схемы валидируют positive/negative fixtures; лишние поля в blind packets отклоняются. Изменение frozen config обнаруживается. Qwen/Astra/Terra и 3 задачи отражены явно; D0 не назначается автоматически. Ни одного model call.
