# T06 — Blind review packets и review runner

## Цель и границы
Исполнить фиксированные панели независимыми Qwen reviews, без утечки variant/condition/target. Общие схемы — T01, client/store — T02, worker states — T04, panels — T05. Не считать detection до evaluator labels.

## Владение файлами
`src/little_pom/reviews/`, `tests/reviews/`. Не менять router, evaluator, package metadata. Использовать общие схемы, не дублировать domain models.

## Интерфейс
`ReviewerPacketBuilder.build(task, dossier, card, worker_state=None) -> Packet`.
`ReviewRunner.run(panel, packets, client, store) -> ServiceResult(PanelOutcome)`.
`RunList.build(panels, seed) -> ordered_slots`. Каждый slot имеет внутренний execution ID и отдельный непрозрачный packet ID; mapping хранится вне exported context.

## Сделать
- Единственный frozen review prompt для всех conditions; текст review свободный, без требования выбрать экспериментальный defect type.
- Разрешённый packet: task, dossier, card, собственный рабочий контекст; Fresh без state.
- Не передавать condition, variant label, target defect/evidence list, informed flags, routing scores, panel membership и другого reviewer.
- Проверять поля и текст на случайные metadata leaks; sanitize не должен удалять научное содержание или private evidence молча.
- Каждый review — новая ветвь от immutable worker snapshot; ни valid/def, ни repeats не наследуют предыдущий review.
- Seeded dispatch order и concurrency из frozen config; request/resume только через T02.
- Complete panel: 2 successful reviews; partial: 1; incomplete: 0. Хранить n_requested/n_observed и причины missingness.
- Сохранять доступный review partial panel, не превращать его автоматически в D=0; complete-only primary analysis и partial diagnostics делает T08.

## Параллельная работа
Fixture panels/states достаточны для старта; не ждать настоящих карточек. Передать T07 mock reviews с evidence citations и failures, T08 — panel outcomes. Model calls запрещены по умолчанию.

## Приёмка
Golden blind-packet tests, независимость histories, стабильный run order и resume без второго sample. Исходные states после reviews побитово неизменны. Partial/incomplete явно различимы. Количество слотов выводится из плана, не зашито как 144; для трёх полных задач с текущим дизайном — 72 reviews.
