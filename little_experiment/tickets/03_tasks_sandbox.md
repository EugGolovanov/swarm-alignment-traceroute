# T03 — Task adapters, Harbor и sandbox

## Цель и границы
Безопасный доступ к трём выбранным Terminal-Bench-Science задачам. Общие контракты — T01. Snapshot: `data/terminal-bench-science/snapshot_manifest.json`; Harbor/Docker setup — `environments/harbor/`. Не загружать весь benchmark.

## Владение файлами
`src/little_pom/tasks/`, `src/little_pom/sandbox/`, `tests/tasks/`, `tests/sandbox/`. Любые overlays — внутри этих каталогов; upstream не редактировать. Зависимости передать T08.

## Интерфейс
`TaskAdapter.load(task_id, snapshot_root) -> ServiceResult(Task)`.
`Sandbox.prepare(task, policy) -> ServiceResult(handle)`; `Sandbox.execute(handle, tool_request) -> ServiceResult(tool_result)`; `Sandbox.close(handle)`.
Task содержит goal/instruction, allowed input descriptors, snapshot hash, resource requirements. Tool result: stdout/stderr, exit status, artifacts, provenance, truncation flag.

## Сделать
- Allowlist только dna-storage-codec, nanoindentation-property-extraction, finite-free-stam; проверка SHA перед использованием.
- Разделить instruction/agent inputs и tests/solution/authoring/ground-truth generators. Не монтировать task directory целиком.
- Inputs брать из предусмотренного agent image: nanoindentation генерирует измерения при сборке, но не раскрывает ground truth.
- Host API calls; контейнер non-root, без сети/credentials/Docker socket; writable workspace с CPU/RAM/time/output limits.
- Проверять path traversal, symlinks и artifact boundaries; фиксировать truncation, не скрывать потерю evidence.
- Preflight ресурсов: DNA 4 GB RAM, остальные 8 GB; текущей VM ~7.75 GiB недостаточно запаса. Не менять ресурсы машины молча.
- Отдельно выяснить статус DNA symlink-verifier issue; добавить regression fixture, не заявлять исправление без проверки.
- Разделить build-time network и offline worker runtime; разрешение сети upstream не считать политикой little.

## Параллельная работа
Дать fake sandbox для T04/T08. Пока images не собраны, все unit tests используют synthetic filesystem. Реальные build/oracle команды opt-in и не выполняются автоматически в тестах.

## Приёмка
Whitelist, hashes, isolation и resource gates протестированы. Команда preflight выдаёт проверяемый отчёт, а не ложную readiness. Worker не читает hidden fixtures даже через symlink. Закрытие sandbox гарантировано при ошибке. Oracle outputs никогда не переходят в worker context.
