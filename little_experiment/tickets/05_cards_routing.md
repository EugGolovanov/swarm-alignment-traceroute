# T05 — Astra cards, profiles, mechanism и routing

## Цель и границы
Подготовка валидируемых карточек и детерминированных панелей без просмотра review outcomes. Общие схемы — T01, worker/evidence fixtures — T04. Astra работает через экспорт/импорт подписочного workflow, не платный API.

## Владение файлами
`src/little_pom/cards/`, `src/little_pom/profiles/`, `src/little_pom/routing/`, `tests/cards/`, `tests/profiles/`, `tests/routing/`. Не менять measurement module и чужие prompts; свои шаблоны держать в своих каталогах, T01 включает их hashes в freeze.

## Интерфейс
`CardWorkflow.export(stage, inputs) -> packets`; `import_response(packet_id, response) -> ServiceResult`.
`ProfileBuilder.build(worker_output, vocabulary) -> Profile`; `RequirementsBuilder.build(valid, vocabulary) -> Requirements`.
`MechanismBuilder.build(cards, evidence, visibility_labels) -> MechanismReference`; `PanelBuilder.build(pool, profiles, requirements, seed) -> panels`.

## Сделать
- Applicability labels от Astra до worker outcomes. На трёх задачах не навязывать matching всех шести defect types; назначения фиксировать до outcomes, failures не заменять удобными задачами.
- q0 → Astra-confirmed valid → freeze Requirements → def с одним дефектом. Проверка evidence и сохранение полной validator trace.
- Work tags извлекать детерминированно из фактической работы, не одной назначенной роли; без target/informed labels.
- Private evidence variant-aware: valid/def/strict. Видимость устанавливать по содержанию, не только наличию evidence ID; спорные случаи явно помечать.
- Overlap-router: first max, complementary second, total-overlap fallback, deterministic tie-break. Router не получает MechanismReference.
- Routed pair одна для variants/repeats; Random pair одна на repeat для обеих variants, независимое sampling между repeats без replacement внутри пары.
- Fresh slots без worker state. Freeze всех panel specs до review calls; пустые requirements/exploitability — диагностические failures.

## Параллельная работа
Начать на fixture pool, Astra ответы импортировать из fixtures. Freeze store внедряется из T02; временный in-memory mock допустим. Packet export не зависит от автоматизации браузера.

## Приёмка
Golden tests routing/fallback/randomness, отсутствие target leakage и неизменность панелей между variants. После freeze импорт, меняющий карточку/панель, отклоняется. Проверены private-valid/def/strict и источники work tags. Никаких Astra calls при реализации.
