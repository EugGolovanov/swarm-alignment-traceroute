# T07 — Terra evaluation и Astra calibration/adjudication

## Цель и границы
Слепой measurement workflow через подписку OpenAI с экспортом/импортом артефактов. Общие схемы — T01; reviews — T06. Не подменять подписку платным API и не обещать неподтверждённую автоматизацию интерфейса.

## Владение файлами
`src/little_pom/measurement/`, `tests/measurement/`. Свои prompts — в этом модуле, hashes передать T01. Не менять cards/routing/analysis.

## Интерфейс
`EvaluationExporter.export(reviews, evidence, seed, policy) -> batch_manifest`.
`EvaluationImporter.import_response(packet_id, raw_response, metadata) -> ServiceResult(EvalLabel)`.
`AdjudicationQueue.build(labels, calibration_labels, hidden_mapping) -> packets`. Mapping/labels хранятся отдельно от blind packets.

## Сделать
- Terra packet: task, card, review claim, реально процитированное evidence, explanation. Без condition/reviewer/variant/target labels и полного target evidence list.
- Opaque IDs, fresh independent contexts, frozen prompt/model/mode/date и полный raw response. Retest не наследует первую оценку.
- Sampling calibration/retest freeze до Terra labels: calibration 36 (12/condition, баланс variants при наличии), retest 20% successful outputs; округление и shortage policy зафиксировать заранее.
- Astra A/B: два независимых контекста без Terra labels, routing metadata, target и истории Astra-validator. Это не независимые человеческие эксперты.
- Отдельный Astra-adjudicator для uncertainty, Terra-positive на valid и A/B disagreement; только необходимый case context без routing metadata.
- Structured labels и provenance; mismatched/duplicate/missing responses не замещать молча. Primary/retest/adjudication labels хранить раздельно.
- Явно описать, какие labels adjudication меняет в final scoring projection; original Terra labels остаются для stability/agreement, retest не majority vote.

## Параллельная работа
Использовать synthetic reviews из собственных fixtures до интеграции T06. Export/import тестируется offline. T08 получает raw/final labels, queue completion и sample denominators через manifest.

## Приёмка
Blindness tests по структуре и тексту, deterministic sampling, обязательные adjudication items, изоляция A/B/validator. Неполная adjudication блокирует финальный FP report. Agreement маркируется cross-model, не human validity. Никаких live subscriptions calls в тестах.
