# T04 — Workers, evidence registry и author

## Цель и границы
Получить реальные независимые рабочие состояния, а не восемь одиночных role prompts. Общие контракты — T01; SwarmClient — T02; sandbox — T03. Qwen одинаково настроен для workers и author.

## Владение файлами
`src/little_pom/workers/`, `src/little_pom/evidence/`, `src/little_pom/author/`, `tests/workers/`, `tests/evidence/`, `tests/author/`. Не создавать свой transport или router.

## Интерфейс
`WorkerRunner.run(task, subtask, client, sandbox, store) -> ServiceResult(WorkerState)`.
`EvidenceRegistry.register(source, fragment, worker_id) -> Evidence`; `AuthorRunner.run(task, handoffs, client) -> ServiceResult(ProposalCard)`.
WorkerState: immutable history, WorkerOutput, доступные evidence IDs и artifact provenance. Output: result, limitations, turns_used, complete.

## Сделать
- Восемь frozen subtasks little: baseline, provenance, prerequisites, alternative explanation, measurement, robustness, resources, decision link.
- До четырёх модельных ходов; последовательный tool-loop внутри ветви, отдельные workspace/history для каждой.
- Истории локальные, без общей серверной памяти; token budgeting фиксируется, silent truncation запрещён.
- Evidence ID вычислять из source/fragment hashes по общему canonical contract, не принимать придуманный моделью ID.
- Одинаковый fragment одного источника у разных workers — один evidence object, разные provenance records.
- Проверять существование источника и точность fragment; generated artifacts хранить отдельно от произвольных claims модели.
- Complete только при содержательном результате или проверяемом ограничении по frozen правилу; failed workers исключаются из reviewer pool.
- Author получает предусмотренные handoffs, создаёт q0 без будущего target defect; профили/маршрутизация — T05.

## Параллельная работа
Использовать injected mock client/sandbox/store. Передать T05 fixture с 8 различающимися states и shared/private evidence; T06 — frozen state для branching reviews. Не ждать live API или сборки научных images.

## Приёмка
Тесты доказывают отсутствие cross-worker history/files leakage, соблюдение turn budget, reproducible evidence identity и provenance. Raw errors не превращаются в successful output. Author не получает скрытые labels. Сохранённый state пригоден для независимых valid/def/repeat forks и никогда не содержит чужие reviews.
