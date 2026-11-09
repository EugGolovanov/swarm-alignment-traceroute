# Последовательный exploratory pilot без отдельной D0

## Исследовательская рамка дальнейшей работы

Основная постановка — [TraceRoute Research: первый proof-of-mechanism](../little_experiment/main_little.md#1-цель-эксперимента). Основной контраст — `RandomHistory` против `WorkProfileOverlapRouted` при одном и том же рабочем пуле, модели, числе reviewers и бюджете review; `Fresh` — диагностический baseline. В существующем harness и frozen artifacts условие `WorkProfileOverlapRouted` называется `RoutedHistory`; старые записи не переименовываются.

Цель — проверить, помогает ли организация cross-review находить и использовать уже возникшие в распределённой работе основания для обнаружения значимых дефектов. Сравнение swarm/direct solver, проверка полных benchmark deliverables и вопрос о необходимости сохранять знания у рабочего владельца — вспомогательные или отдельные исследования, не основной claim и не go/no-go критерии первого PoM. Поэтому DNA executable comparison и текущий SCI executable run не заменяют routing experiment. Переход от обнаружения дефекта к предотвращению ошибочного последующего действия — следующий этап при положительном PoM.

Ниже сохранена история operational-решений и запусков. Уточнение рамки не меняет задним числом frozen protocols, outcomes или budgets.

### Новый запуск после литературного разбора

Актуальный план оператора: [новый routing-PoM](traceroute_routing_pom.md). До новых review outcomes исправляются host-owned evidence references и проверяется evaluator; затем все пять заранее выбранных development-consumed SCI эпизодов проходят однократное создание общего рабочего пула, freeze всех pools/cards/panels, matched RandomHistory/RoutedHistory и Fresh-диагностику. Scientific judge сохранённых полных submissions подключается отдельно через подписку с GPT-6 Astra; это не routing evaluation. Требование нового Anthropic API key было ошибочной интерпретацией одного upstream transport, не зависимостью проекта. Новые оценки не переписывают старый отчёт с unmeasured rubric.

Переход к исполняемому action gate условный: только после положительного routing-PoM с приемлемыми false positives и измеримостью. Отдельно фиксируются provenance, technical failures, evidence coverage/use, defect detection, missingness и фактические затраты. Development-интервенция evidence не заменяет основной контраст и не вводит owner-memory claim.

## Принятое изменение протокола

По явному решению оператора используем три выбранные задачи вместо отдельной D0:

1. `dna-storage-codec`
2. `nanoindentation-property-extraction`
3. `finite-free-stam`

Реализован отдельный `--phase exploratory`, не поддельный успешный D0. `exploratory_freeze_manifest.json` фиксирует отсутствие D0, порядок задач и отсутствие независимого подтверждения. Исходный `--phase final` по-прежнему требует отдельную D0.

Все политики и applicability-решения по всем трём задачам замораживаются до первого worker-вызова. По каждой задаче последовательно выполняются workers → author → valid → def → panels → reviews. Workers следующей задачи требуют manifest reviews предыдущей. Неудачные ветви не пересэмплируются; завершённый этап может содержать зафиксированную missingness.

Сохраняются восемь worker-ветвей, WorkerTurn/WorkerOutput, проверяемое evidence, ProposalCard/ExperimentalCard, pre-outcome applicability, RequiredTags до defect injection, Fresh/RandomHistory/RoutedHistory, два repeats и два reviewer на панель, Review validation, immutable intent/response ledger и ослепление.

**Measurement остаётся общим для трёх задач:** calibration/retest выбираются только после всех reviews, до первых evaluator labels. Нельзя превращать одну глобальную выборку в три независимые маленькие выборки.

## Проверка без внешних сервисов

```bash
uv run --locked --extra live little-pom \
  --run-dir runs/sequential-offline --phase exploratory sequential-run
# Повтор этой команды проверяет resume без новых samples.
```

Без `--live` используется только явно mock-конфигурация. Это проверка ПО, не научный результат.

## JSON-обмен через подписку

Экспорт до readiness/freeze допустим только для pre-outcome applicability:

```bash
uv run --locked --extra live little-pom \
  --run-dir runs/sequential-live --config config/little/live.json \
  --live --phase exploratory subscription-export
```

`config/little/live.json` нужно явно подготовить из `config/little/pilot.json`: `mode=live`, реальный режим каждого подписочного сервиса вместо `unverified`, согласованный `measurement_policy` вместо mock (используйте `little_pom.measurement.measurement_policy` для корректного ID), точные дата/режим/модели. Не отмечайте доступность модели или режима без проверки. Не меняйте конфигурацию после exploratory freeze.

Экспорт содержит:
- `exchange/validator/*.request.json` — запросы validator;
- `exchange/evaluation/*.request.json` — только ослеплённые evaluation/adjudication запросы;
- `*.response-template.json` — шаблон импортируемого ответа по packet ID;
- `exchange/operator/response-bundle-template.<hash>.json` — объединённый шаблон ожидаемых ответов;
- контракт `ExperimentalCard.schema.json` для valid/def карточек.

**Шаблоны с `null` — не ответы моделей.** Они намеренно не проходят импорт. Отправляйте только соответствующий `request.json` указанной модели в свежем подписочном контексте. Подставляйте фактически полученный полный ответ и реальные metadata. Не отправляйте `measurement/private`, операторский bundle или весь run directory.

Скопируйте шаблон bundle **за пределы run directory**, заполните и передайте через `--responses`. Для evaluation `raw_response` — исходный текст JSON-ответа, не пересказ; metadata должны совпадать с экспортированным запросом и фактическим сеансом. Для validator ответ является объектом, его полная трасса сохраняется. Не редактируйте экспортированные файлы на месте: это immutable audit artifacts.

После предварительного экспорта можно дополнить readiness и конфигурацию до freeze. Идентичные научные task projections имеют стабильные IDs между readiness revisions. Изменённые scientific inputs/assignments не дают права переиспользовать старый ответ.

## Live-последовательность

Текущая конфигурация по решению оператора — смешанная: **workers `openai-codex/gpt-6-luna` через подключённую подписку omp/pi**, author/reviewer — **`qwen3.5-397b` через Adaption**. Это отдельный экспериментальный partition, а не продолжение прежних Qwen samples. Старые frozen runs и неоднозначные transport ledgers не переписываются.

До freeze нужен успешный explicit readiness по `docs/little_harness.md`: образы по immutable IDs, ресурсы, runtime/oracles, разрешение DNA verifier риска, model access, structured output, quota/budget и изоляция подписочных контекстов. Отдельная D0 больше не является prerequisite этого режима. Все остальные readiness-проверки сохранены.

```bash
uv run --locked --extra live little-pom \
  --run-dir runs/sequential-live --config config/little/live.json \
  --live --phase exploratory --inspect-docker \
  --readiness readiness/attestation.json --secret-path temp/secrets/adaption_api_key.txt \
  --responses subscription-responses.json sequential-run
```

Команда останавливается на недостающих ответах и возвращает `sequence_paused` с JSON-путями. Дополните внешний bundle и повторите **ту же** команду. Workers/reviews и принятые labels повторно не вызываются. После всех трёх задач экспортируется глобальный evaluation batch; adjudication экспортируется отдельно, когда доступны необходимые primary/A/B labels.

Для ручного этапа используются те же параметры плюс `--task TASK_ID`, например `--task dna-storage-codec workers`. Перескочить к workers следующей задачи нельзя. `eval-export`, `eval-import`, `analyze` работают глобально, без `--task`.

## Подготовка научных сред

Отдельная opt-in команда не вызывает LLM и не ослабляет readiness:

```bash
uv run --locked python -m little_pom.sandbox.prepare \
  --task dna-storage-codec --output runs/science-preparation/dna-new \
  --build --runtime --oracle --timeout 3600
```

Повторите для остальных задач **последовательно**, с отдельными output directories. `--build` явно разрешает сетевые загрузки во время сборки. Build context ограничен upstream `environment/`; tests/solution не монтируются в worker image. Вместо сборки можно передать `--image sha256:...`.

`--runtime` использует production Sandbox с проверкой provenance, ресурсов и non-root/no-network изоляцией. `--oracle` отдельно запускает Harbor oracle; логи находятся в `private-oracle/` и не должны попадать к workers/reviewers. Успех определяется по единственному соответствующему trial, отсутствию exception и `reward=1.0`, не только по exit code. Успешный upstream oracle сам по себе не разрешает отдельный DNA overlay/export symlink риск.

`preparation.json` не перезаписывается. Для нового наблюдения используйте новый output directory; это не разрешение пересэмплировать экспериментальные model calls.

## Текущая политика inference после дополнительного разрешения оператора

В `config/little/pilot.json` клиентский `timeout_seconds` установлен в `null` (SDK получает `timeout=None`), `worker_output_tokens` увеличен до 8192, а четырёхходовый `worker_total_tokens` — до 32768. `max_retries=0` сохранён. Timeout локальных sandbox-команд не отключается. Старые frozen runs не переписываются и не могут молча продолжиться с изменённой политикой.

Отключение клиентского timeout не отключает deadline шлюза Adaption. Для диагностики выполнена отдельная multi-model отправка исходного длинного worker payload: изменяются только model ID и выходной бюджет. Эти ответы не являются экспериментальными samples и не выбирают новую модель для Little-PoM автоматически.

## Отдельный транспортный smoke test

```bash
uv run --locked --extra live little-pom \
  --run-dir runs/transport-smoke --live --secret-path temp/secrets/adaption_api_key.txt model-smoke
```

Это один явно разрешённый платный синтетический вызов, с лимитом 1024 выходных токена и ledger, не worker/review sample. Resume использует сохранённый ответ. Intent без response после сбоя остаётся ambiguous; нельзя менять execution ID/run directory ради автоматического повтора. Для будущих transport failures audit сохраняет безопасный numeric HTTP status, если он доступен, но не provider error body или credentials.

## Фактически наблюдалось при реализации

- Docker: aarch64, 10 CPUs, 8 319 504 384 bytes VM RAM. Для nanoindentation и finite-free-stam требуется 9 126 805 504 bytes с headroom. Поднимите Docker RAM до 10 GiB; последовательный порядок не устраняет недостаток RAM одной задачи.
- DNA overlay собран; production Sandbox runtime успешен. Harbor upstream oracle завершился без исключений с reward 1.0. Артефакты: `runs/science-preparation/dna-runtime-oracle/`. В первом preparation.json oracle ещё помечен как требующий review; фактический trial result проверен отдельно. DNA symlink/export риск отдельно не разрешён.
- Nanoindentation overlay собран; production runtime корректно отказал из-за RAM. Артефакты: `runs/science-preparation/nano-build/`.
- finite-free-stam: upstream build остановился на HTTP 503 при скачивании pinned elan из GitHub; snapshot/Dockerfile не изменены. Лог: `runs/science-preparation/finite-build/upstream-build.log`.
- Adaption: каталог ранее подтвердил прежнюю модель; единственный generation smoke для неё завершился ambiguous без сохранённого ответа. `runs/adaption-transport-smoke/` нельзя повторять новым ID для обхода запрета. Это историческое наблюдение не подтверждает генерацию текущей `qwen3.5-397b`.
- Предварительные applicability JSON и шаблоны: `runs/sequential-pilot-preparation/subscription/exchange/`. Это экспорт из исходной конфигурации для подготовки, не live freeze и не подписочные ответы.

### Последующее изменение RAM для второй задачи

По явному запросу оператора runtime-лимит **только nanoindentation** снижен с 8192 до **7168 MiB**. Резерв VM 512 MiB и запрет swap сохранены. Объявленные upstream требования и snapshot не менялись. Политика находится в `src/little_pom/sandbox/resources.py`, включается в source freeze и явно отражается в preflight/preparation. Для finite-free-stam снижение не применяется.

Фактическая проверка с этой политикой: production Sandbox runtime прошёл; Harbor oracle с `override_memory_mb=7168` прошёл с **reward 1.0**, без exception. Новый отчёт: `runs/science-preparation/nano-7g-runtime-oracle/preparation.json`. Старый отчёт nano-build отражает предыдущее наблюдение и не переписан. Первоначальный RAM-блокер для второй задачи снят; полный запас памяти на любых научных нагрузках этим не гарантируется.

Полный трёхзадачный live-эксперимент не завершён; внешние блокеры не заменяются mock-ответами или ложными attestations.

### Запуск с qwen3.5-397b

Успешный реальный диагностический worker-ответ сохранён в `runs/adaption-model-sweep-20261108T215833Z/c5e158732fafa46d/`: HTTP 200, provider status `completed`, worker schema valid. Это не экспериментальный sample: предложенные tools в диагностике не исполнялись.

Для существующего отдельно разрешённого частичного двухзадачного пути подготовлен новый run `runs/little-pom-qwen35-live/`. Он сохраняет исходные ответы Astra с `mode=internal_agent`, не переименовывает их в subscription и не заявляет full readiness. Конфигурация фиксирует восемь ветвей, четыре хода, 8192 выходных токена на worker-вызов, 32768 total, отсутствие клиентского timeout и ноль транспортных retries.

```bash
uv run --locked --extra live python runs/little-pom-qwen35-live/launch.py \
  --secret-path temp/secrets/adaption_api_key.txt --new-workers 8
```

Этот launcher запускает DNA workers → author → экспорт valid и останавливается для validator. Nanoindentation не запускается до завершения DNA cards/panels/reviews; данный launcher не реализует их продолжение. Finite-free-stam исключена из этого частичного пути из-за неподготовленной среды. Не считать его завершённым трёхзадачным экспериментом.

Неустранённые ограничения: quota/budget attestation, DNA overlay/export verifier risk и readiness внешнего measurement. Стандартный `sequential-run` по-прежнему требует полный readiness и subscription provenance. Старые runs и неоднозначные вызовы не перезапускаются новым model ID.

Фактический первый запуск новой модели: DNA worker 0 получил HTTP 504; ответ не сохранён, ledger фиксирует `ambiguous_remote_execution`. Клиентский timeout был отключён, transport retries — 0. Остальные семь DNA workers и nanoindentation не запускались. Итог: `runs/little-pom-qwen35-live/launch-outcome.json`; audit: `audit/4dd293cdff09447ba2f8ca63380e6f99.json` внутри run. Без разрешения неоднозначности или отдельно зафиксированной явной авторизации повтор запрещён.

### Worker-only переход на Luna subscription

Оператор явно выбрал `openai-codex/gpt-6-luna` только для workers. Используется существующая авторизация omp/pi, без paid OpenAI API fallback и без копирования токенов в конфигурацию или артефакты. `models.worker` фиксирует модель, subscription transport и параметры; `models.swarm` описывает оставшиеся Qwen author/reviewer роли.

Python сохраняет управление WorkerTurn, sandbox и evidence registry: Luna возвращает JSON tool_requests, а не получает прямые host filesystem/shell tools. Для каждой ветви сохраняются локальная история, до четырёх ходов и исходные токенные лимиты. Один WorkerTurn не должен скрывать дополнительные schema-repair, fallback или retry-вызовы.

Fresh/RandomHistory/RoutedHistory теперь сравнивают Qwen reviewers с историями Luna workers. Это другой model-pair estimand; результаты нельзя объединять с прежней однородной Qwen-конфигурацией как один frozen run. Applicability Astra и внешние measurement роли этим решением не меняются.

Новый worker-only partition: `runs/little-pom-luna-workers-live/`. Команда не использует Adaption-ключ и останавливается перед author:

```bash
uv run --locked --extra live python runs/little-pom-luna-workers-live/launch.py \
  --new-workers 8
```

Запускаются восемь DNA worker-ветвей; nanoindentation ждёт завершения DNA author/cards/panels/reviews по принятому task-major порядку. При transport ambiguity повтор не выполняется. Наличие подключённой подписки не заменяет runtime-проверку actual model output; факт её результата фиксируется отдельно после запуска.

Транспорт `OmpSubscriptionBackend` использует установленный Pi **1.5.0**, Node и низкоуровневый `openai-codex-responses` provider. Это не автономная omp task/session: нет дополнительного system context, tools, repair или compaction. Авторизация читается из подключённого Pi OAuth после записи intent; API-key fallback запрещён. Фиксируются хеши bridge, Node, зависимостей и точная запись model catalog; обновление Pi требует отдельной проверки совместимости.

Используется SSE с `maxRetries=0` и защитой от второго generation POST. Лимит `max_output_tokens` передаётся явно; если endpoint его не поддерживает, попытка отклоняется без повторения и без удаления лимита. Raw-артефакт содержит наблюдаемые parsed SSE events и Pi messages, а не заявленные полные wire bytes. Reasoning effort фиксирован как `medium`.

Первая реальная Luna subscription generation завершилась HTTP 200, `status=completed`, одним POST; endpoint принял `max_output_tokens`. Исходный run `runs/little-pom-luna-workers-live/` сохранил полный наблюдаемый ответ, но первый разбор ошибочно склеил commentary с final_answer. Адаптер исправлен: structured WorkerTurn берётся из final_answer; commentary остаётся в аудите.

Продолжение находится в `runs/little-pom-luna-workers-phasefix/`: первый ответ повторно интерпретируется из сохранённого provider response, без повторной генерации, с проверкой точного исходного request/schema и хешей. Старые response/result/state не переписываются. Новые ходы идут через исправленный subscription backend:

```bash
uv run --locked --extra live python runs/little-pom-luna-workers-phasefix/launch.py \
  --new-workers 8
```

Фактический итог Luna worker batch: восемь DNA ветвей, 25 уникальных provider generations со статусом completed/HTTP 200 (24 новых в recovery run плюс один ранее сохранённый ответ). Валидных WorkerOutput — 0/8: шесть `unobserved_evidence`, один `unobserved_final_tools`, один `turn_budget_exhausted`. Author и nanoindentation не запускались. Это успешная проверка subscription transport, но не успешный научный результат. Итог и usage: `runs/little-pom-luna-workers-phasefix/launch-outcome.json`. Контрактные проверки не ослаблялись; полный offline suite — 426 passed.

### Исправление worker/environment контрактов

Два независимых исполнителя согласовали и взаимно проверили `Sandbox.describe(handle)` (`ENVIRONMENT_CONTRACT_VERSION=1`) и worker protocol version `2`. Environment сообщает относительный workspace root, отдельный public task source, ограниченный manifest научных входов, отображения исходных путей, доступность exec и его cwd.

Публичная инструкция теперь существует как реальный неизменяемый UTF-8 файл `__little_pom__/task.txt`, с проверяемым SHA256. Она не увеличивает число научных входов и не выдаётся за измерение. Для DNA нулевой input manifest ожидаем: нужно конструировать/исследовать решение, а не объявлять отсутствие тестовых файлов неисправностью. Для nanoindentation проверены 14 доступных read-only файлов под `data/`.

Worker получает текущий/максимальный/оставшийся turn budget и лимит tools, а также явные правила: связанные tools можно группировать; `done=true` требует `tool_requests=[]` и отдельного хода после наблюдения результатов. Evidence — только точные фрагменты действительно прочитанных файлов или проверенных generated artifacts. Каталоги, list output, stdout и поля packet не становятся файловыми источниками. Task source подтверждает требования, не экспериментальную производительность. Четыре хода и строгие completion/evidence gates сохранены.

Реальная Docker-проверка обеих доступных сред прошла: чтение точной инструкции, обнаружение входов, выполнение Python, запись/чтение результата и независимая регистрация его fragment evidence. Артефакт: `runs/worker-contract-development/environment-smoke.json`. Полный offline suite: 453 passed. Старые восемь неудачных samples не изменены; новый protocol/hash требует отдельной development/frozen revision, не resume старого scientific run.

Отдельный bounded Luna development smoke (`runs/worker-contract-development/luna-baseline/`) не заменяет scientific samples. На первом ходе модель запросила чтение public task file и listing одним batch; sandbox успешно вернул результаты. На втором ходе provider завершил ответ HTTP 200, но final_answer содержал malformed JSON (`Expecting ',' delimiter`, column 582), поэтому WorkerOutput не принят. Обе генерации сохранены, repair-call и повтор не выполнялись. Итог проверки: `runs/worker-contract-development/verification.json`. Исправление environment/worker affordances подтверждено; успешное model-driven completion и надёжность structured output этим прогоном не подтверждены.

### Отдельный development probe: Terra worker / Sol evaluator

Subscription backend допускает явный выбор `openai-codex/gpt-6.1-terra`; Luna остаётся default. Выбранный selector фиксируется в configuration/request hash, сверяется с установленным Pi catalog и должен совпадать с запросом и provider payload. Medium, output cap, отсутствие retries/repair и максимум четыре worker turns не изменены.

Новый launcher: `runs/worker-contract-development/terra-baseline-smoke.py`; отдельный immutable run root: `runs/worker-contract-development/terra-baseline/`. Запускается только DNA baseline worker, не полный scientific pilot и не замена предыдущего sample. В локальном конфиге этого probe внешний blind evaluator выбран как `openai-codex/gpt-6.1-sol`; исторический ключ роли `models.terra` и поле `measurement_policy.models.terra` хранят именно Sol, policy hash пересчитывается. Sol обнаружен в каталоге подключённого omp, но generation/evaluation ещё не проверены: evaluator не вызывается, mode остаётся `unverified`. Разные worker/evaluator модели не гарантируют независимость ошибок.

Фактический Terra probe завершён: `status=ok`, принятый WorkerOutput, три из четырёх ходов, три HTTP 200/completed, по одному dispatch на ход, без retries/repair. Модель прочитала public task file, записала и выполнила Python в Docker, прочитала generated result и зарегистрировала три fragment evidence. Наблюдавшаяся длительность команды — 28.30 s; provider usage — 7864 input / 987 output tokens. Это однобайтовый synthetic baseline, не реализация требуемого DNA file interface и не benchmark pass. Один успех не доказывает надёжность модели; Sol не вызывался.

Итог: `runs/worker-contract-development/terra-baseline/{development-result,verification}.json`; manifest, три intent/response и generated artifacts сохранены отдельно от старых runs. После исправления двух test assertions затронутые runtime/CLI suites: 32 passed (10.26 s). Предшествующий полный прогон дал 452 passed / 2 failed; полный suite после этих test-only исправлений повторно не запускался.

### Новый частичный прогон без Qwen

По уточнению оператора новый `runs/little-pom-terra-sol-live/launch.py` использует **Terra для workers, author и reviewers**, Astra для valid/def карточек и calibration/adjudication, Sol для внешних primary/retest labels. Все новые вызовы идут через подключённую Pi OAuth подписку, без paid API fallback. Это отдельный partition; исходные Luna/Qwen runs и development smoke не продолжаются и не заменяются.

Launcher сохраняет восемь worker-ветвей на задачу, порядок DNA → nanoindentation до reviews каждой задачи и одну общую measurement-выборку после обеих задач. Существующие applicability-решения Astra internal agents сохраняются с исходными hashes/provenance, без переименования в subscription. Новые карточки проходят полный production CardWorkflow с фактическим Astra selector. Evaluator получает только экспортированный blind packet/prompt в отдельном контексте; исходный JSON сохраняется в ledger и импортируется без repair.

Подписочный транспорт теперь проверяет точную комбинацию model/stage/schema: Terra для WorkerTurn/ProposalCard/Review, Astra для SubscriptionCardResponse, Astra или Sol для EvalLabel. Выбор модели должен совпадать с backend freeze и установленным Pi catalog. External calls: medium, 8192 output tokens для card validator, 2048 для evaluation; retries=0. Эти бюджеты входят в launch freeze. Неудачные review slots сохраняются как missingness, не восполняются новыми samples.

Полный readiness не заявляется: finite-free-stam исключена из ранее разрешённого частичного пути, DNA overlay/export verifier risk и quota/budget attestation остаются незакрытыми. Указание `mode=subscription` в конфиге задаёт маршрут исполнения, а не подтверждает ещё не выполненную генерацию.

Фактический запуск завершён за 1070.09 s: 132 intent, 132 единичных dispatch, все HTTP 200 / provider completed, без отсутствующих responses. По фактическому ledger: Terra — 26 worker-ходов, один author и 24 reviews; Astra — две card validation, 48 calibration и две adjudication; Sol — 24 primary и пять retest. Qwen-вызовов нет. Provider usage: 274243 input / 27313 output tokens; это не денежный счёт по подписке.

DNA: приняты 7/8 workers; author, valid/def, все 12 panels и 24 reviews завершены. Восьмая ветвь получила отсутствие заявленного generated artifact, затем закрытый sandbox handle; её результат не принят и не пересэмплирован. Nanoindentation: 0/8 принятых workers — семь `input_budget_exceeded`, один `insubstantial_output`; author не запускался из-за `insufficient_complete_workers`, 12 ожидаемых panels не созданы. Статус orchestration `ok` означает построенный отчёт с явной missingness, а не успех обеих задач.

Внешняя оценка завершена (`measurement_usable`): 24 final labels, 24+24 независимых calibration labels, пять retest и две adjudication. Во всех Fresh/RandomHistory/RoutedHistory на единственном измеренном DNA episode detection = 2/2 defect panels, false positives = 0/2 valid panels. Преимущество routing не наблюдалось; private-target coverage и множество informed exploitable workers пусты. Нельзя трактовать это как научное подтверждение или переносить результат на nanoindentation.

Артефакты: `runs/little-pom-terra-sol-live/report.json`, `verification.json`, immutable stage manifests и intent/response ledger. Frozen source inventory проверен после исполнения. Полный suite до финальной корректировки legacy `transport-smoke` role дал 525 passed / 2 failed; после исправления runtime/subscription/exchange и smoke suites дали 79 passed. Реальный запуск дополнительно упражнил Terra author/reviewer, Astra card/calibration/adjudication и Sol primary/retest пути.

### Direct full-task Terra baseline без routing/reviews

По следующему запросу оператора выполнен отдельный `runs/terra-direct-dna/launch.py`: один Terra solver получает полную публичную DNA-задачу и исполняет local tools, без восьми подзадач, author, карточек, routing, reviewers или evaluator. Существующий WorkerTurn используется только как формат tool-сообщений; WorkerRunner/WorkerOutput и их subtask completion не используются. Заморожены medium, до 16 ходов, 8192 output tokens на ход, 262144 UTF-8 bytes input upper bound, максимум восемь tools на ход, 60 s на команду, без repair/retry.

Фактически: четыре вызова, все HTTP 200/completed, 148.64 s включая независимую проверку; 31001 input / 6717 output tokens. Terra доставила `encoder.py`, `decoder.py`, `common.py`, `codec.c` с заявленной ёмкостью 3596 bytes. После freeze кода host сгенерировал новый случайный бинарный файл этой длины и три независимых deletion/shuffle канала. Encoder выдал ровно 750 последовательностей по 240 ACGT; все три decoder запуска восстановили файл побайтно. Каждый decoder запускался в отдельной свежей Docker-среде только с кодом и corrupted reads, без исходного файла, uncorrupted reads или encoder scratch. Encoder — 0.355 s, decoders — 0.346–0.353 s.

Сопоставление из ledger, только DNA: предыдущие workers+author — 19 вызовов, 46721 input / 6628 output tokens; вместе с reviews, validation и внешним measurement — 124 вызова, 226795 input / 25582 output tokens. В предыдущем прогоне интегрированный кодек не проверялся; результатом были proposal/cards/reviews. Отсутствие такой проверки не означает измеренный провал реконструкции.

Это не чистая ablation routing: цель стала executable solution, input cap вырос с 32768 до 262144 bytes, максимум ходов — с четырёх на отдельного worker до 16 на solver. Фактический последний direct request — 37125 bytes, то есть не поместился бы в старый cap. Один solve и три канальных испытания не доказывают превосходство, максимальную ёмкость или надёжность; проверен публичный `/app`→`/work` mapping, не upstream verifier. Артефакты и оговорки: `runs/terra-direct-dna/{verification,comparison}.json`, код — `runs/terra-direct-dna/solution/`. Исходники и solution hashes проверены после исполнения. Независимый verifier предварительно проверен в Docker: честный однобайтовый codec прошёл 3/3, decoder с попыткой прочесть original data.txt не прошёл 3/3.

### Три DNA diagnostics перед решением о роли задачи

Все три проверки выполнены в отдельных runs, без изменения старых samples. Общая subscription role policy расширена только для Luna Review и Terra EvalLabel; остальные model/stage/schema ограничения сохранены. Runtime/exchange/reviewer regression: 101 passed. Реально наблюдались 27 единичных model dispatch: один Direct Luna, восемь Fresh-Luna reviews + восемь blind Sol scores, десять Terra ablation evaluations.

`runs/luna-direct-dna/`: тот же научный task projection, prompt, cap 262144 UTF-8 bytes, максимум 16 ходов, medium, 8192 output tokens и независимый verifier, что у Direct Terra; отличается run-local task.id. Первый запрос — 7420 bytes, HTTP 200/completed, но final_answer malformed JSON; `invalid_json`, ноль tools, verification не запускался. Время 54.38 s, usage 1587 input / 2684 output. Никаких repair/retry. Это сбой протокола, не измеренный провал кодека и не исчерпание input cap.

`runs/dna-fresh-luna/`: восемь исходных Fresh slots с неизменными публичными task/card/dossier/prompt, без worker history; четыре valid и четыре def, по два reviewers в двух repeats. Luna обнаружила target `unmet_precondition` в 4/4 defective reviews (2/2 panels); на valid controls significant claims — 0/4 (FP panels 0/2). Все восемь blind Sol scores приняты, uncertainty 0/8. Все reviews имеют пустые evidence IDs; дефект обнаруживается по публичному конфликту обязательной `/work` lookup table и условия `/app`-only. Это diagnostic primary labels, без новой calibration/retest/adjudication или human validation.

`runs/dna-terra-evidence-ablation/`: до labels выбраны первые четыре primary packet IDs с непустыми citations и один empty-evidence контроль. Десять Terra calls, medium/2048; в четырёх парах различается только `cited_evidence`, контроль идентичен. `significant_claim` и `normalized_type` неизменны 4/4; `evidence_support` меняется 2/4 — один true→false и один false→true после удаления citations. Контрольные categorical fields неизменны. Цитаты дублируют публичный task text, поэтому вмешательство не удаляет все основания для обнаружения дефекта. Все десять `false_positive=null` допустимы схемой, но не соответствуют boolean-инструкции prompt; значения не исправлялись. Нужна явная семантика cited-only support против task/card support, а не требование принудительного label flip.

**Решение после трёх проверок:** текущий DNA episode оставить development/control-кейсом с публичным ceiling, не продвигать как механистический эпизод для нового confirmatory набора из шести задач. Private target evidence и informed exploitable worker set пусты; headroom для routing здесь не наблюдался. Это не вывод обо всех возможных DNA-задачах. По `main_little.md` §5.2–5.3 задача уже development-consumed; outcome-based replacement и повторное включение в confirmatory selection запрещены. Frozen task IDs и denominators прежних отчётов не менялись. Полное основание: `runs/dna-selection-diagnostics/{plan,decision}.json`.

### EurekaBench: исследование альтернативы, не миграция

По запросу оператора отдельно изучены [EurekaBench](https://github.com/EurekaBench/EurekaBench/tree/58956ee98483b6b781f68e1754c37d06bc7bed19) и [статья v1](https://arxiv.org/html/2610.00492v1). В опубликованном commit — 24 task configs и пять доменов; статья описывает 26 задач и шесть доменов. Структурные development-кандидаты: neural-dynamics (адресуемые интервенции), arc-tokamak (пересекающиеся transport/profile зависимости), galaxy-environment (локальные архивные измерения). Это аргументы о структуре, не наблюдённое превосходство над DNA.

Штатный `launch.run` жёстко требует `nvidia-container-cli` и H100 для любого task; sandbox использует Apptainer, зависимости требуют Harbor 0.23.0. На текущем Darwin/arm64 PATH проверены только Docker и uv, Apptainer/NVIDIA CLI не найдены. Готового omp/pi OAuth adapter нет: агент и judge ожидают API credentials. CPU-компоненты CAMELS/c302 могут быть отдельным адаптированным smoke, но их выполнение здесь не проверено и не эквивалентно штатному benchmark. LICENSE/reuse terms не установлены. Никакие builds, simulator или model runs EurekaBench не запускались.

Критический measurement-конфликт: штатный [insight judge](https://github.com/EurekaBench/EurekaBench/blob/58956ee98483b6b781f68e1754c37d06bc7bed19/domains/neuroscience/neural-dynamics/tests/judges/insights_strict.md) может проводить новые эксперименты, восстанавливая удалённое evidence. Поэтому для PoM нужен отдельный blind next-step endpoint с фиксированным evidence и без simulator access; SC/PA/SI остаются вторичными метриками. До новых model outcomes следует заморозить task pool/порядок, восемь содержательных ветвей, evidence intervention и бюджеты, затем проверить среду и изоляцию. Полный отчёт с первичными ссылками: `runs/dna-selection-diagnostics/eurekabench-research.json`. Ни корпус из шести задач, ни старые результаты не менялись.

### SCI-AGENT-EXAM: отдельный пятизадачный exploratory run

По явному выбору оператора подготовлен отдельный `runs/sci-agent-exam-terra-sol/launch.py`: Terra — workers/author/reviewers, Astra — applicability/valid/def/calibration/adjudication, Sol — blind primary/retest evaluation. Используются все пять публичных примеров frozen SCI snapshot, в порядке bioinformatics → chemistry → materials-science → mathematics → physics-simulation. Корпус не смешивается с TBS, исходные runs не переписываются. Это не полный benchmark и не протокол 6 + D0.

```bash
uv run --locked --extra live python runs/sci-agent-exam-terra-sol/launch.py
```

Сохранены восемь workers, четыре хода, medium, 8192 output tokens на worker turn и отсутствие retries/repair. До исходов заморожен `unmet_precondition` для всех задач; Astra должна подтвердить applicability. Для нового run input upper bound увеличен с 32768 до 262144 UTF-8 bytes: публичные инструкции bioinformatics/physics сами занимают 12–13 КБ и дублируются в исходном task projection. Истории не усекаются. Прямое сравнение со старыми бюджетами некорректно.

Все пять arm64 образов собраны и прошли production Sandbox runtime: публичные входы, запись `/home/agent/results`, UID/GID 65532, read-only image inputs, отсутствие активных non-loopback интерфейсов, capabilities=0 и no-new-privileges. Bioinformatics repair project копируется в writable workspace; остальные данные остаются immutable. Physics overlay добавляет `sqlite3` и `h5dump`. Локальная resource policy — 1 CPU, 4096 MiB RAM; upstream SCI эти лимиты не объявляет. Build contexts не включают private tests/solution/rubric.

Первоначальный runtime smoke ошибочно требовал отсутствия любых интерфейсов кроме `lo`: Docker VM содержит неактивные tunnel interfaces при `--network=none`. Диагностика подтвердила пустую таблицу IPv4 routes; исправленная проверка требует, чтобы все non-loopback interfaces были DOWN. Исходный неуспешный отчёт сохранён в `preparation/`, успешные пять — в `preparation-verified/`. Это исправление smoke assertion, не ослабление Docker network policy.

Проверки: полный suite до последнего smoke-исправления — 563 passed; после него и добавления SCI public/private path regressions — 141 passed в затронутых suites. Штатные SCI oracle/judge не запускались; runtime success не означает scientific benchmark pass. Little-PoM строит и оценивает предложения/reviews, а не итоговые deliverables для upstream scorer.

Фактический запуск завершился за 5182.71 s (86.4 min): 321 intent, 321 единичный dispatch, все HTTP 200 / provider completed, отсутствующих responses нет. По ledger: Terra — 116 worker turns, три author calls и 48 reviews; Astra — девять validator calls и 87 evaluation calls; Sol — 58 evaluation calls. Usage: 1 997 258 input / 110 171 output tokens, не денежный счёт. Все 82 файла source inventory совпали с freeze после исполнения.

Принятые workers: bioinformatics 8/8, chemistry 8/8, materials-science 5/8, mathematics 7/8, physics 7/8. Author/cards/reviews завершились только для chemistry и mathematics: 24 полные panels, 48 reviews из 60 ожидаемых panels на весь корпус. Bioinformatics и physics остановились на `author.input_budget_exceeded`: лимит author остался 32768 bytes, увеличение worker input cap его не меняет. Materials-science author отвергнут за цитирование evidence вне разрешённых handoffs. Worker missingness: три неточных evidence fragments, один insubstantial output и один truncated tool output. Samples не повторялись.

Measurement получил все 48 primary labels, 36+36 calibration, десять retest и 15 adjudication responses. Однако шесть adjudications остались `uncertain=true`: final-scored 42/48, итог `measurement_inconclusive`, не usable measurement. В отчёте denominator false-positive равен нулю во всех условиях; это отсутствие оценимой метрики, не ноль ложных тревог. Преимущество routing по этому прогону не установлено. `launch-outcome.status=ok` означает завершённый orchestration с отчётом, не успех всех задач или benchmark pass.

Итоги: `runs/sci-agent-exam-terra-sol/{launch-outcome,report,verification}.json`; исходные requests/responses, workers, cards, reviews и measurement сохранены в том же run. Менять бюджеты, исправлять model outputs или повторять uncertain labels внутри этого frozen run нельзя.

### Development diagnostics: передача evidence и executable DNA comparison

По запросу оператора добавлены read-only SCI trace audit, production author handoff preflight и отдельный `runs/dna-swarm-development/launch.py`. Указание «DТФ» интерпретировано как DNA в смешанной раскладке (`Т` → `N`, `Ф` → `A`); используется `dna-storage-codec` из Terminal-Bench-Science, не DFT/quantum chemistry. Старые SCI/TBS outcomes не переписываются.

```bash
uv run --locked --extra live python runs/dna-swarm-development/launch.py evidence
uv run --locked --extra live python runs/dna-swarm-development/launch.py compare
```

Каждый stage отказывается повторять уже frozen запуск. Evidence intervention использует ранее полученный Terra codec и отдельную копию с контролируемой ошибкой decoder grouping (`idx//250, idx%250` заменены на `idx%3, idx//3`). Это искусственный development fault, не новая случайная benchmark-ошибка. Оба кандидата проверены на одном новом случайном файле 3596 bytes и трёх deletion/shuffle seeds; decoder каждый раз получает только code и corrupted reads в новой изолированной среде.

Фактически исходный codec восстановил 3/3, изменённый — 0/3. Для каждого кандидата выполнены два repeats трёх условий: measured evidence, omitted evidence, идентичный omitted-control. Независимые Terra reviewers не имеют tools или доступа к другим контекстам. Парные запросы проверены побайтно на уровне canonical JSON: различается только поле evidence, omitted-control идентичен omitted.

Наблюдалось 12 completed calls с единичным dispatch: при measured evidence reviewer дважды выбрал ADVANCE для исправного codec и REPAIR для неисправного; без evidence и в duplicate controls — VERIFY во всех случаях. Все четыре пары изменили решение, все четыре controls согласованы. Это положительный контроль чувствительности решения к проверяемому evidence; не доказательство преимущества routing или downstream swarm performance. Три успешных trial не доказывают общую надёжность. Артефакты: `evidence-intervention/{freeze,report,pair-verification,source-verification}.json`; исходник на момент завершения сохранён как `launch-source.py`.

Executable comparison выделяет каждой ветви одинаковые потолки: Terra medium, 32 generation calls, 8192 output tokens/call, 262144 canonical UTF-8 bytes/request, 256 tools, 60 s/tool. Single solver имеет 32 хода; swarm — восемь независимых specialists × три хода и восемь ходов integrator. Все принятые specialist handoffs вместе с source files должны уложиться в 16384 bytes каждый, без усечения; до workers проверяется конечная верхняя граница первого integrator request, каждый реальный request измеряется отдельно. Принимаются только exact observed evidence fragments. Это сравнение executable decomposition, не routing/review эксперимент.

Обе ветви должны доставить одинаковый интерфейс encoder/decoder и максимизировать payload; после freeze обоих решений создаётся один held-out challenge. Проверяются общие случайные payload prefixes и channel seeds, 750 × 240 ACGT, независимое восстановление и runtime. Фактический расход учитывается отдельно: одинаковые потолки не означают одинаковое потребление. Один development sample на ветвь не даёт статистического доказательства превосходства.

Первая попытка comparison остановилась до freeze и до model intent из-за tuple в JSON manifest. Исправлена только сериализация списка specialties; evidence stage не повторялся. Production code/tests после реализации preflight и audit: 590 passed. Сам audit реально выполнен на пяти SCI задачах без model calls; документация API и проверка реконструированных author requests — `docs/little_harness.md`.

SCI audit дополнен чтением всех 35 принятых worker results/limitations и сохранённых source/tool artifacts: `runs/swarm-design-diagnostics/scientific-findings.json`. Это качественная интерпретация с точными worker/evidence IDs, не автоматическая оценка семантической новизны. Bioinformatics содержит содержательные сравнения популяции пар и downstream metrics; physics — отдельный harmonic/sensitivity расчёт, но обе передачи остановлены до author. Materials generated evidence — преимущественно извлечённый текст статей, не новые эксперименты. В chemistry сохранённое вычисление `29.2422087658` расходится с пересказом `29.4`, который попал в два reviewer packets. В mathematics отсутствие буквальной цитаты «22 против 2» в reviewer packet не означает потерю факта: соответствующий пересказ доступен во всех 24 packets, дополнительное объяснение через translation — только в двух. Поэтому exact-fragment uniqueness/availability нельзя выдавать за уникальное знание или его использование.

### Наблюдаемый результат DNA comparison

`runs/dna-swarm-development/comparison/report.json`: один независимый solve на arm, одинаковые верхние лимиты, общий новый challenge после фиксации обоих решений. Source inventory неизменен после исполнения (`source-verification.json`). Все 21 model dispatch завершились; повторной генерации не было.

| Метрика | Single | Swarm |
|---|---:|---:|
| Payload, bytes | 7346 | 10989 |
| Точное восстановление в изолированных испытаниях | 3/3 | 3/3 |
| Model calls | 3 | 18 |
| Tool calls | 7 | 30 |
| Input tokens | 15713 | 71337 |
| Output tokens | 3120 | 19992 |
| Solve wall seconds | 77.02 | 521.32 |

Swarm integrator получил пять из восьми handoffs. Specialists measurement, robustness и integration не создали обязательный `out/handoff.json`; сохранены `solver_boundary_failure`, без ремонта/перегенерации. Остальные handoffs уложились в cap. Наблюдаемая ёмкость swarm на 49.6% выше при шестиразовом числе model calls; одинаковые потолки не означают одинаковый consumed compute. Wall time относится к этому последовательному запуску специалистов, не к потенциальной параллельной latency. Три channel trials на один solver sample не являются тремя независимыми solver replicates. Это исполняемый development result, не upstream benchmark score, не оценка максимальной ёмкости/надёжности и не доказательство причинного выигрыша от routing или конкретных handoffs.

### Исправленный пятизадачный SCI run

`runs/sci-agent-exam-corrected/` применяет явный author budget 262144 bytes и production request/citation preflight ко всем пяти задачам; allocation plans сохранены до первых model calls. Остальные worker/model бюджеты сохранены. Regression-проверка author/audit: 21 passed. Исходный SCI run не изменялся.

`launch.py` остановился на definitive `invalid_json` biology worker. Отдельный `continuation.py` сохранил этот отказ как missingness без повторного вызова. Затем chemistry `def` был отклонён: Astra потеряла два символа `cb` в evidence ID (62 символа вместо 64), хотя исходный valid card был принят. `complete.py` переиспользовал принятые стадии и сохранённые dispatch results, сохранил rejected card как missingness и выполнил оставшиеся задачи. Все три исходника/freezes и исходы первых двух остановок сохранены; их source inventories после исполнения неизменны.

| Задача | Принятые workers | Author request bytes | Author | Reviews |
|---|---:|---:|---|---:|
| Bioinformatics | 6/8 | 39348 | accepted | 24 |
| Chemistry | 6/8 | 25460 | accepted | 0 |
| Materials science | 6/8 | 32483 | accepted | 24 |
| Mathematics | 7/8 | 15823 | accepted | 24 |
| Physics simulation | 7/8 | 37076 | accepted | 24 |

Все пять author requests прошли, включая biology/physics, превышающие прежние 32768 bytes. Всего сохранены 235 model responses, каждый provider completed и ровно с одним generation dispatch. Это не 235 корректных по контракту ответов: например, один содержит invalid JSON.

Итоговый статус **incomplete**: на `eval-export` штатный exporter отклонил `Review cites missing evidence`. Read-only сверка обнаружила 11 из 96 reviews со ссылками вне общего evidence registry: один biology и десять materials, включая обозначения статей P1–P4 вместо evidence IDs и повреждённые хеши. Никакие ссылки не исправлялись, reviews не исключались из denominator, evaluation labels не генерировались. Наблюдаемый отчёт и точные IDs: `observed-outcome.json`; это отдельный отчёт диагностики, не успешный `Analysis` report.

Данный run проверяет предложения и экспериментальные reviews, а не полные upstream deliverables. Число научно решённых задач **не измерено**; author acceptance, worker completion и число reviews не являются benchmark pass. Исправление author budget подтверждено исполнением, но исправления всего протокола недостаточны для end-to-end measurement.

### Полные executable SCI deliverables и независимые programmatic checks

`runs/sci-agent-exam-executable/{solver,verifier,launch}.py` выполняет отдельную диагностику решения задач, не routing experiment. На каждую из пяти задач — один Terra solver sample без пересэмплирования, до 32 turns, 8192 output tokens/request, 262144 canonical input bytes/request и 256 tools. Solver получает публичную задачу и работает в изолированном Docker без сети; hidden tests доступны только отдельному verifier. Экспорт сохраняет конечные или частичные deliverables до закрытия sandbox, включая boundary failure. Уже существующие outputs не перезаписываются.

Перед модельным запуском реальные smoke runs подтвердили экспорт точных 1048576 binary bytes при нормальном завершении и автоматическом error cleanup, наблюдение nonzero exit и отказ повторного входа. Все пять неизменённых upstream programmatic checkers на пустой submission вернули ожидаемый нулевой score без infrastructure failure. Сохранены `solver-smoke-verified/report.json` и `empty-submission-smoke-verified/<task_id>/verification/result.json`; неуспешные подготовительные smoke outputs также не удалялись.

Основной запуск завершён: 40 completed model calls, пять доставленных решений, 70 экспортированных файлов. После исполнения source inventory и проверяемые deliverables не изменились. Отдельный verifier действительно исполнил каждый официальный `check_programmatic.py` в свежем sandbox. Все пять deterministic artifact preflights прошли; отсутствие обязательных файлов не объясняет обнаруженные ошибки.

| Задача | Programmatic score | Полностью прошла programmatic checks | Наблюдаемые ограничения |
|---|---:|---|---|
| Bioinformatics | 1.000000 | да | Пройдены replay и hidden perturbations; научная rubric не измерена |
| Chemistry | 0.958000 | нет | `runtime_screening_disposition`; submitted code фиксирует `non_authoritative` вместо адаптации к изменённым входам |
| Materials science | 0.888900 | нет | `DISCOVERY_FRONT_END_VALUE`: submitted verdict `mixed` не прошёл checker |
| Mathematics | 0.525256 | нет | Неполные source-claim mapping и hidden review outcomes; часть audit outputs расходится |
| Physics simulation | 0.531500 | нет | Спектральные/window/noise/coupling значения, schema/window-function mutations и воспроизводимость baseline |

**Это не полный benchmark score и не доказательство решения всех пяти задач.** Проверенная часть полностью пройдена у одной из пяти. Upstream дополнительно требует отдельную научную rubric judgment; она не запускалась из-за отсутствующего `ANTHROPIC_API_KEY` для штатного judge transport. Подготовлены неизменяемые judge packets, но full score остаётся `null`. Веса programmatic части различаются: bio 65%, chemistry 45%, materials 20%, mathematics 70%, physics 55%; upstream не задаёт общего бинарного pass threshold.

Машиночитаемый результат: `runs/sci-agent-exam-executable/report.json`. По каждой задаче сохранены `tasks/<task_id>/deliverables/`, `solver/history.json`, `solver/result.json`, `verification/result.json`, официальные checker results, artifact preflight и judge packet. Собственные заявления solver о прохождении self-tests не заменяют независимые результаты. Наблюдаемые дефекты пригодны для development cases; после просмотра этих outcomes те же случаи нельзя представлять свежим подтверждающим тестом reviewer routing.

### Новый TraceRoute routing-PoM: подготовка и реальные pre-review проверки

Протокол: `docs/traceroute_routing_pom.md`; исполняемый runner: `runs/traceroute-routing-pom/launch.py`. В новом запуске canonical evidence IDs остаются на host, а author/validator/reviewer выбирают локальные ссылки `[[ev:1]]`. Host проверяет область доступа, сохраняет provenance и канонизирует принятые ответы. Неизвестная ссылка — `evidence_reference_contract`, не научное отсутствие дефекта. Raw response hash сохраняется отдельно от канонизированного ответа, чтобы resume после успешного импорта не требовал новой генерации.

Реальный smoke сначала воспроизвёл повреждение научного обозначения `E1` при использовании bare aliases, затем подтвердил сохранение `Energy E1 is 3 eV.` и корректное разрешение `[[ev:1]]` после перехода к reserved syntax. Интеграционная проверка после исправлений: 637 tests passed. После отдельной правки scientific transport: 99 runtime/scientific-judge tests passed. Это проверка ПО, не результат научной гипотезы.

Пять предварительно зафиксированных Astra evaluator controls прошли все применимые оценочные оси: `runs/traceroute-routing-pom/execution/evaluator-controls/result.json`. Неопределённые по контракту оси были исключены из gate до первого ответа и сохранены в наблюдениях; критерии после outcomes не менялись. Экспериментальных reviews в этом запуске не было.

Первый scientific judge dispatch (`runs/sci-astra-scientific/`) получил пять HTTP 400 без оценок; исторические response bodies не сохранялись, поэтому точную классификацию первоначального server error восстановить нельзя. Исправленный Codex adapter переносит только точный trusted upstream grading prefix в обязательное поле instructions; rubric, submitted artifacts, captions и исходные image bytes сохраняются. Проверяется восстановление исходного текста без потерь. Model override на Astra и role adaptation отражаются явно; гарантируется отсутствие client truncation, а не неизвестное поведение сервера.

После scientific-only transport revision создан новый freeze: `runs/traceroute-routing-pom/execution-v2/protocol-freeze.json`. Все пять уже полученных controls переиспользуются с проверкой исходных requests, labels, prompt/model/case identities и hashes, без повторных model calls. Исходный freeze и outcomes остаются неизменными. Upper bound нового запуска — 648 calls плюс пять отдельно учтённых ранее выполненных calibration calls; это потолки, не фактические затраты.

Новый scientific grading хранится отдельно в `runs/sci-astra-scientific-v2/`; новый routing-PoM — в `runs/traceroute-routing-pom/execution-v2/`. Статус и выводы следует брать только из завершённых отчётов этих запусков, а не из наличия freeze или факта старта процесса.

### Завершённый scientific judge: Astra через subscription

`runs/sci-astra-scientific-v2/report.json`: пять измеренных оценок, без повторного запуска solver или programmatic checkers. Использован явно обозначенный override `openai-codex/gpt-6-astra` вместо upstream `gpt-5.6-sol`. Anthropic API key не нужен. Предыдущая запись о неизмеренной научной части описывает исторический executable run и не переписана.

| Задача | Scientific rubric fraction | Full weighted score |
|---|---:|---:|
| Bioinformatics | 0.4932 | 0.8226 |
| Chemistry | 0.7590 | 0.8486 |
| Materials science | 0.5512 | 0.6187 |
| Mathematics | 0.3770 | 0.4808 |
| Physics simulation | 0.5949 | 0.5600 |

Macro-average full score — 0.6661; общего бинарного pass threshold у benchmark нет. Это не официальный upstream-model score и не human expert validation. Выполнены пять completed grading dispatches; wall time — 129.72 s. Первые пять HTTP-400 dispatches учитываются отдельно и не объявляются успешными оценками.

Новый adapter ничего не обрезает, но исходные upstream judge packets уже содержали truncation: bio `offline_replay.py`, `activities.tsv`, `compound_fingerprints.tsv`; physics `windowed_signal.csv` и сокращённый trajectory. Bio rubric 8–9 получила FAIL с явным указанием, что repair callsite отсутствует в обрезанном packet. Это нельзя автоматически приписывать ошибке solver: независимый programmatic replay прошёл. После просмотра оценок packet не заменялся и selective regrading не выполнялся.

### Append-only continuation после локального sandbox failure

Первый `execution-v2/run-outcome.json` остановился до experimental reviews на bio worker-4: извлечение PDF в отсутствующий каталог `out/` завершило sandbox; регистрация evidence затем получила `Unknown or closed sandbox handle`. Все модельные turns этой ветви durably completed. Worker не исправляется, не перезапускается и остаётся missingness.

`runs/traceroute-routing-pom/continue.py` и `execution-v2/local-failure-continuation-freeze.json` фиксируют до новых reviews более точное различение доказанного terminal local tool failure и неоднозначного model execution. Проверяются WorkerState, наблюдённый `sandbox_execution_failed`, completed durable response и единственный dispatch каждого использованного turn. Все старые artifacts связаны hashes; прежний stop outcome сохраняется. Неизвестный/неоднозначный model dispatch по-прежнему останавливает запуск. Условия, эпизоды, бюджеты и научные критерии не меняются.

Отчёт оформляется в `docs/traceroute_report.tex`: Introduction, Related Work, Methodology, Results, Discussion, обязательные Limitations and Dual-Use / Ethical Considerations, References и audit appendix. Основной текст ограничен восемью страницами; actual routing outcomes не заменяются оценками отдельных SCI submissions.

Первое continuation подтвердило неизменность прошлых artifacts, затем остановилось на chemistry worker-3 с `tool_output_truncated`. До experimental reviews добавлено второе явное amendment: `continue_terminal.py` / `terminal-worker-continuation-freeze.json`. Оно учитывает `tool_output_truncated` и `unobserved_final_tools` как terminal worker missingness при доказанных completed dispatches всех использованных turns. Сам tool-output limit и требование отдельного финального observing turn не ослабляются. Предыдущие два stop outcomes, source bytes и samples сохраняются; scientific verdict из этих технических отказов не выводится.

### Завершённый routing-PoM: положительный результат не получен

Финальный статус: `runs/traceroute-routing-pom/execution-v2/terminal-worker-continuation-outcome.json` — execution `ok`, measurement `incomplete`, positive PoM `false`. Это завершённый запуск с неопределённой частью научных измерений, не незавершённый model process. Исходные stop outcomes и все предшествующие artifacts остались hash-identical.

Из 40 назначенных рабочих ветвей завершены 33: по задачам 6, 6, 6, 7, 8. Семь missing workers не заменялись. Все пять author/card preparations приняты; frozen pool общий для условий. Выполнены все 60 panels и 120 reviews, без потерь evidence-reference contract на review.

**Основной механизм оказался неидентифицируемым в этих эпизодах:** все target grounds уже представлены в публичном card context. Во всех пяти задачах private-informed count равен нулю; прирост private coverage отсутствует. Это не отсутствие реальной работы у workers и не доказательство бесполезности routing вообще. Конструкция карточек убрала преимущество доступа к отдельным рабочим основаниям.

| Condition | Defect panels detected / scored | Не оценены из 10 | Requested reviews |
|---|---:|---:|---:|
| Fresh | 10/10 | 0 | 40 |
| RandomHistory | 8/8 | 2 | 40 |
| WorkProfileOverlapRouted | 7/7 | 3 | 40 |

Полные paired detection данные есть только для chemistry и mathematics: обе разности нулевые; descriptive bootstrap CI [0,0] относится только к этим двум задачам, не доказывает эквивалентность. Primary five-task contrast не измерен полностью. Target-citation rate на defective reviews — 0.20 в обоих history conditions; supported-use proxy — 0.10 Random и 0.05 Routed, paired SD разности 0.209 по пяти задачам. Proxy сочетает target citation с review-level semantic support; не доказывает причинную роль отдельного основания.

Получены 120 primary labels, 24 retests, две cross-model calibration lanes по 36, 63 adjudications. Девять adjudications остались uncertain; final-scored reviews — 111/120. Все ответы доставлены: это научная неопределённость, не транспортная потеря. Frozen global `fp_report_ready` требует разрешения всей mandatory adjudication queue, поэтому primary false-positive rates остаются `null` во всех условиях. После outcomes правило не ослаблялось. Retest agreement: evidence support 21/24, остальные три оси 24/24.

Фактические расходы нового запуска: 534 calls, 3,473,400 input tokens, 150,935 output tokens; usage missing — 0. Summed provider latency — 6,847.01 s, не wall time. Три execution commands суммарно заняли 6,646.00 s без операторских пауз. Пять прежних evaluator-control calls учитываются отдельно. Review-only расходы и вариативность сохранены в `runs/traceroute-routing-pom/report-data.json`.

Preregistered intervention: `unavailable`, потому что ни один private-defect source-attributed worker не подходит. Это не нулевой causal effect; новые cases не подставлялись. Action gate: `ineligible`; никаких downstream science actions не выполнялось.

Следующая development-инвестиция — outcome-blind eligibility checkpoint до reviews: удостовериться, что отдельные реальные основания не скопированы в общий proposal, различаются между ветвями и адресуются frozen work profiles. Непригодные эпизоды регистрировать, а не пересобирать до победы Routed. Отдельно проверить end-to-end evaluator readiness при unresolved adjudication. Новый независимый confirmatory set должен исключать эти development-consumed задачи. Это не основание переходить к swarm-vs-solver benchmark или заявлять предотвращение ошибочных действий.

Итоговый [отчёт PDF](traceroute_report.pdf) содержит 6 страниц основного текста, 1 страницу References и 2 страницы appendices; лимит 8 main pages соблюдён. Локальный LaTeX-исходник `traceroute_report.tex` собран XeLaTeX в два прохода; PDF разобран strict pypdf, таблицы Results и Costs проверены на отрендеренных страницах. В финальном compile log нет Overfull или Warning. Основной отчёт включает все запрошенные разделы, включая обязательные Limitations and Dual-Use / Ethical Considerations.

### Подготовка рабочего дерева к публикации

Неиспользованные черновики `ground_truth/`, временные download logs, Python/pytest caches и локальный файл Adaption key перенесены в исключённый из Git `/temp/`. Ключ находится в `temp/secrets/adaption_api_key.txt`; содержимое не публикуется. Примеры `--secret-path` выше обновлены под этот локальный путь.

Исходники, тесты, отчёт, литература и экспериментальные артефакты не удалялись. `runs/`, виртуальные окружения и downloaded upstream snapshots остаются на прежних путях и сохраняют существующие ignore rules; это не автоматическая публикация полных raw runs. Перенос `ground_truth` удаляет его из следующей версии дерева, но не из существующей Git-истории. История не переписывалась.

По решению автора литература `/papers/`, LaTeX-исходники и вспомогательные файлы сборки отчёта в `/docs/` также исключены из Git; локальные файлы сохранены. Готовый PDF включён в публикацию. Пути к литературе и raw runs в отчёте описывают локальные audit artifacts, а не файлы, поставляемые с публичным репозиторием.
