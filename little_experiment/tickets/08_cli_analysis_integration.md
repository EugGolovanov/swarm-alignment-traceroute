# T08 — CLI, анализ и end-to-end интеграция

## Цель и границы
Собрать семь независимо разрабатываемых компонентов в воспроизводимый mock pilot, затем подготовить opt-in D0. Источники требований — little runbook/technical/main; общие контракты T01. Полный шестизадачный PoM не имитировать тремя задачами.

## Владение файлами
`pyproject.toml`, `uv.lock`, `src/little_pom/__init__.py`, `src/little_pom/cli/`, `src/little_pom/analysis/`, `tests/integration/`, `tests/analysis/`, `docs/little_harness.md`. Единственный владелец packaging/dependencies. Чужие модули не править: интеграционные ошибки возвращать их владельцам.

## Интерфейс
CLI stages: `preflight`, `freeze`, `workers`, `cards-export/import`, `panels`, `reviews`, `eval-export/import`, `analyze`, `mock-e2e`.
`Analysis.compute(panel_outcomes, labels, mechanism, policy) -> report`. Все commands принимают run directory/config; результаты и resume опираются на stage manifests, не существование произвольного файла.

## Сделать
- Python 3.12, uv lock, pytest; зависимости от владельцев T01–T07. Mock mode default; live, Docker builds и oracle только явными отдельными опциями.
- Начать CLI на injected service stubs: не ждать готовых модулей. Финально подключить реальные interfaces без изменения их signatures.
- Проверки порядка: config freeze → workers/cards → panel freeze → reviews → sample freeze → evaluations/adjudication → analysis.
- Метрики private-def primary coverage, public/private-valid/strict diagnostics, exploitability, evidence use, detection через frozen equivalence + support + relevance; FP только после adjudication.
- Primary detection/FP по complete panels; partial отдельно, missingness/denominators обязательны. Episode — единица paired analysis/bootstrap, не отдельный review.
- Retest claim agreement ≥0.70; Astra BA с class denominators как cross-model diagnostic. Full-PoM GO gates не применять к трём задачам; pilot status отдельно, measurement-inconclusive явно.
- D0 не выбирать автоматически. D0 freeze → одна отдельная назначенная задача → fix window → final freeze; consumed task не выдавать за независимое подтверждение.

## Параллельная работа
Сразу опубликовать scaffold packaging и fixture-driven mock CLI. Не создавать общие conftest, меняемые всеми: fixtures лежат у владельцев. Интеграцию запускать по мере готовности, соблюдая file ownership.

## Приёмка
Одна команда offline mock-e2e строит полный отчёт и audit trace, повторный запуск не генерирует новые samples. Есть tests failures/partial/leaks/freeze violations. README содержит реальные команды и remaining blockers, не заявляет готовность live D0 до preflight. Ни платных calls, ни научных builds автоматически.
