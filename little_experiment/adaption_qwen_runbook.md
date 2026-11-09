# Luna workers через подписку omp/pi и Qwen author/reviewer через Adaption

**Статус:** по текущему решению владельца workers переходят на `openai-codex/gpt-6-luna` через существующую подписку omp/pi. `qwen3.5-397b` остаётся для author/reviewer. Ниже примеры Adaption относятся к этим Qwen-ролям; актуальный последовательный harness и исторические наблюдения описаны в `docs/sequential_pilot_launch.md`.

**Принятые решения владельца проекта:** little-эксперимент авторитетнее неиспользованных черновиков ground truth, архивированных локально в исключённом из Git `temp/ground_truth/`; его дизайн уже согласован и не требует повторного согласования. Использование подписки OpenAI для Terra critic доступно и принято как факт проекта. Terra остаётся внешним offline-критиком с предусмотренным ослеплением. Задача интеграции — реализовать эти решения, а не пересматривать их.

## Рабочий корпус и runtime

По решению владельца проекта рабочий корпус заменён на публичный **Terminal-Bench-Science**: `data/terminal-bench-science/`. SCI-AGENT-EXAM остаётся отдельной справочной выборкой, корпуса не смешиваются. Скачивание ограничено тремя задачами по явному выбору владельца: `dna-storage-codec`, `nanoindentation-property-extraction`, `finite-free-stam`. Commit, SHA-256 и inventory сохранены в `data/terminal-bench-science/snapshot_manifest.json`. Текущий этап — целевой feasibility pilot, не полный шестизадачный PoM; численные GO gates 4/6 и 5/6 к нему автоматически не применяются. D0 ещё не назначена. Официальный development split не предполагается. Параметры среды и ограничения описаны в `data/terminal-bench-science/README.md`.

Harbor 0.20.0 установлен в `.venv-harbor/`; версии и инструкции — `environments/harbor/`. Docker Desktop установлен, Engine/Compose и hello-world проверены. Сборка научных задач и интеграция с little runner пока не выполнены.

## 0. Обновление: Astra вместо людей

По решению владельца проекта все ранее человеческие роли выполняет **OpenAI `gpt6-astra` через подписку OpenAI**. Это изменение заменяет human-workflow ниже и соответствующие human-требования в `main_little.md` и `technical_details_little.md`; доступность точного model ID интерфейсом здесь ещё не проверена. Не подменять модель или подписочный путь платным API автоматически.

- **Validator:** Astra проверяет применимость типов дефектов до worker outcomes; затем подтверждает/исправляет `valid`, формирует `def` с одним целевым дефектом и проверяет supporting/private evidence. `RequiredTags` фиксируются до инъекции дефекта. Validator не получает review outcomes.
- **Calibration A/B:** два независимых контекста Astra оценивают 36 отзывов каждый; без Terra labels, condition, reviewer identity, target defect и valid/def labels. Они не наследуют историю validator или друг друга.
- **Adjudicator:** отдельный свежий контекст Astra разрешает разногласия A/B, Terra uncertainty и Terra-positive claims на valid. Получает только материалы, необходимые для adjudication, без routing metadata и истории validator.
- **Terra** остаётся primary blind evaluator; workers используют Luna subscription, author/reviewer — Qwen через Adaption. Это явная смена прежнего однородного model-pair дизайна, не автоматический fallback.
- Для Astra сохраняются frozen prompts, фактическая модель/режим, дата, обезличенный packet и полный ответ. Обмен — через доступный подписочный интерфейс с экспортом/импортом артефактов.

Это **автоматическая cross-model calibration**, а не human calibration: A/B — независимые вызовы одной модели, не независимые эксперты. Agreement с Astra и adjudication не являются человеческим ground truth; возможны коррелированные ошибки Astra и Terra, включая ошибки Astra в создании карточек. Существующие численные measurement thresholds сохраняются как операционные diagnostics/gates, но не подтверждают human validity. В отчёте явно указывать отсутствие человеческой проверки; переименовать human-артефакты и метрики в Astra calibration/adjudication.

## 1. Что запускаем

Точные модели текущей конфигурации: workers — **`openai-codex/gpt-6-luna`**, author/reviewer — **`qwen3.5-397b`**. Авторизация Luna берётся из подключённой подписки omp/pi; платный API не подставляется. Автоматических fallback на другие модели нет. Старые frozen-конфигурации и transport ledgers сохраняются без изменений.

Ниже историческая схема полного PoM, с актуализированным распределением моделей; текущий feasibility pilot включает только три выбранные задачи:

```text
6 задач
  → 8 независимых рабочих ветвей Luna на задачу, до 4 ходов каждая
  → фактические worker outputs, evidence и work profiles
  → отдельный Qwen-author формирует q0
  → человек создаёт valid/def; RequiredTags фиксируются до defect injection
  → детерминированный router / random control / Fresh
  → Qwen-reviewers: 72 панели × 2 = 144 отзыва
  → обезличенные TerraPacket
  → Terra offline + human calibration / adjudication
```

Terra не руководит workers, не выбирает панели и не возвращает исправления рою. Исследуемый контраст — `RoutedHistory` против `RandomHistory`; `Fresh` — диагностический baseline.

«Несколько агентов» здесь — независимые контексты, а не отдельные веса или обучение. Внутри worker-роли используется одна Luna; внутри author/reviewer — одна Qwen. Транспорты omp/pi и Adaption не заменяют наш experiment runner, router, sandbox или evidence boundaries.

## 2. Ключ отдельным .txt

Предлагаемый путь от корня проекта: **`secrets/adaption_api_key.txt`**. Файл содержит только ключ, одной строкой, без кавычек, `Bearer` и `export`. Создать его отдельно; реальный ключ не вставлять в Markdown, код, prompt или логи.

В `.gitignore` исключены `/secrets/` и `/adaption_api_key.txt`. Если файл будет иметь другое имя вне `secrets/`, сначала добавить его точный путь в ignore. Ignore не удаляет уже закоммиченные секреты.

При подготовке рабочего дерева к публикации прежний корневой файл ключа перенесён в `temp/secrets/adaption_api_key.txt`; `/temp/` также исключён из Git. Для существующего локального ключа передавайте этот путь явно; примеры ниже с `secrets/` описывают отдельную первоначальную настройку.

После создания:

```bash
chmod 700 secrets
chmod 600 secrets/adaption_api_key.txt
```

Ключ читает только host-side API-клиент. Не монтировать `secrets/` в worker sandbox; не передавать ключ в worker packets. Путь разрешать явно, не искать автоматически по всем `.txt` проекта.

## 3. Установка и проверка модели

Команды выполняются из корня проекта. По заметкам для inference нужен `adaption>=0.16.0`; проект ориентирован на Python 3.11+.

```bash
python3 -m venv .venv-qwen
source .venv-qwen/bin/activate
python -m pip install 'adaption>=0.16.0'
```

После smoke test закрепить фактически проверенную версию и зависимости в lock-файле (целевой стек проекта — `uv` + `uv.lock`). Не обновлять SDK между условиями или после final freeze.

Проверка доступа, без генерации:

```python
from pathlib import Path
from adaption import Adaption

key = Path('secrets/adaption_api_key.txt').read_text(encoding='utf-8').strip()
if not key or len(key.split()) != 1:
    raise ValueError('Ожидается один непустой API-ключ без пробелов')

with Adaption(
    api_key=key,
    base_url='https://api.prod.adaptionlabs.ai',
    max_retries=0,
    timeout=120.0,
) as client:
    # SDK 0.19.0 возвращает ModelListResponse, модели находятся в .data.
    response = client.models.list(type='hosted')
    available = {m.id for m in response.data}
    if 'qwen3.5-397b' not in available:
        raise RuntimeError('qwen3.5-397b недоступна этому ключу; запуск остановлен')
    print('qwen3.5-397b доступна')
```

Для SDK `base_url` — `https://api.prod.adaptionlabs.ai` без `/api/v1`: SDK сам добавляет этот префикс к путям ресурсов. Иначе получается `/api/v1/api/v1/...` и HTTP 404. Этот URL ранее проверен с SDK 0.19.0. Доступность и генерацию `qwen3.5-397b`, квоту и structured output нужно подтверждать отдельно; наблюдения для прежней модели не переносятся.

`120` секунд — предлагаемая настройка, не требование API. Проверить её в preflight и заморозить до D0. Наличие модели в каталоге ещё не гарантирует квоту и успешную генерацию.

## 4. Минимальный async-вызов

Ниже **только платный транспортный smoke test**, не worker stage и не D0. У него нет экспериментального intent/response ledger; не использовать этот фрагмент для сбора результатов PoM и не перезапускать после неоднозначного сетевого сбоя.

```python
import asyncio
from pathlib import Path
from adaption import AsyncAdaption

MODEL = 'qwen3.5-397b'

async def main():
    key = Path('secrets/adaption_api_key.txt').read_text(encoding='utf-8').strip()
    if not key or len(key.split()) != 1:
        raise ValueError('Некорректный файл ключа')

    async with AsyncAdaption(
        api_key=key,
        base_url='https://api.prod.adaptionlabs.ai',
        max_retries=0,
        timeout=120.0,
    ) as client:
        response = await client.responses.create(
            model=MODEL,
            instructions='Отвечай кратко по-русски.',
            input='Одним предложением объясни, что такое evidence provenance.',
            max_output_tokens=512,
            store=False,
        )
        print(response.output_text)

asyncio.run(main())
```

В эксперименте вместо строки `input` передаётся сериализованный разрешённый packet и рабочее состояние. Сохранять весь ответ SDK (`response.model_dump(mode='json')`), не только `output_text`; проверять статус, наличие текста и схему. Лимит `512` выбран только для smoke test, для содержательной работы недостаточность лимита проверяется до freeze.

Не переносить Luna-specific `Standard / reasoning.effort=medium` на Qwen без подтверждения поддержки. `temperature`, `top_p`, reasoning и JSON-schema параметры не добавлять наугад. Конфигурация Qwen — отдельная версия экспериментальной политики, не эквивалент Luna medium.

## 5. Как организовать независимые агенты

Один `AsyncAdaption` можно использовать для параллельных запросов: разделение агентов определяется их packets и локальными историями, а не числом объектов клиента.

- Каждый worker имеет собственные task/subtask, разрешённые inputs, историю ходов и evidence provenance. Workers не читают истории друг друга.
- Ходы одного worker последовательны. Независимые ветви можно запускать параллельно через `asyncio.Semaphore`; начальный предлагаемый предел — 2 одновременных запроса. Проверить квоты до D0, затем заморозить concurrency и порядок диспетчеризации.
- До четырёх ходов — реальная работа с разрешёнными артефактами и сохранёнными результатами. Восемь названий ролей в одиночных prompts не заменяют рабочие состояния.
- Работа с файлами/кодом требует собственного ограниченного tool-loop и sandbox. API-запросы идут снаружи; sandbox — без сети, credentials и Docker socket, с лимитами CPU/RAM/time.
- Author получает только предусмотренные handoffs, не скрытый будущий target defect.
- Reviewers получают task, dossier, card и только своё рабочее состояние. Для `Fresh` worker state отсутствует. Все reviewers используют один review prompt; condition в prompt не включается.
- Каждый review — независимая ветвь от **замороженного worker state**. Нельзя дописывать первый review в историю следующего repeat или другой variant: иначе появится утечка valid/def и результатов.
- История хранится локально; каждый запрос содержит нужный разрешённый контекст. Не использовать общую серверную память, RAG по всем данным эксперимента или скрытые inference stacks.

Для простоты используем `store=False`, без `previous_response_id`. По заметкам серверное продолжение требует `store=True` и имеет отдельную семантику хранения. Локальная история делает границы контекста проверяемыми. Формат её сериализации, обрезка/сжатие и token budgets должны быть frozen; тихая обрезка private evidence недопустима.

## 6. Обязательный контракт экспериментального SwarmClient

Сохраняем `SwarmClient` и immutable ledger; выбираем subscription backend для Luna workers и Adaption backend для Qwen author/reviewer. Router и measurement layer не переписываются.

```text
SwarmClient.call(packet, schema, execution_id) -> Parsed | Incomplete
```

Перед реальными PoM calls реализовать §5.3 technical:

1. Вычислить request hash по полной конфигурации, instructions, packet, истории и schema version. Секрет в hash/лог не включать.
2. Захватить межпроцессный lock для execution_id. Проверить ledger: существующий response с совпадающим request hash читать с диска; несовпадение — ошибка, не cache hit.
3. Intent без response после restart означает `incomplete`, а не разрешение повторить генерацию.
4. До отправки надёжно записать immutable intent. После получения сначала надёжно сохранить полный raw response и metadata, затем парсить и валидировать.
5. Schema/semantic failure, пустой или усечённый ответ — `Incomplete`, без repair-call и второго sample.
6. Timeout/disconnect после возможной отправки — `ambiguous_remote_execution`; никакого автоматического re-call. Даже локальный execution_id не доказывает серверную идемпотентность.
7. SDK `max_retries=0`; свои retries разрешать только при доказанном pre-send failure или подтверждённом отсутствии принятой генерации. Сам по себе статус 5xx такого доказательства не даёт. Консервативный первый вариант — вообще без транспортных retries.
8. Сохранять фактические usage, latency, response ID, возвращённую model metadata, SDK version и параметры запроса. Не выдумывать отсутствующие token counts или snapshot ID.

Structured output проверять локально по frozen Pydantic-схемам. Поддержка provider-side JSON schema именно этой моделью должна пройти preflight; просьба «верни JSON» не является гарантией валидности. Любой выбранный способ фиксируется до D0. Evidence IDs вычисляет registry по источнику/фрагменту; нельзя доверять выдуманным моделью ссылкам.

## 7. Routing и ослепление остаются локальными

`WorkProfileBuilder` извлекает frozen tags из фактически выполненной работы; не читает target defect, informed labels или Terra outcomes. `RequiredTags` строятся до инъекции дефекта. Router — обычный детерминированный код с frozen tie-break и complementary coverage, не LLM.

Одна routed panel используется для valid/def и обоих repeats. Random panels семплируются независимо между repeats из complete worker pool до review calls. Никакие review outcomes не влияют на выбор панели.

`TerraPacketBuilder` экспортирует только task, card, claim, процитированное evidence и explanation. Нельзя экспортировать condition, worker identity, panel composition, routing scores, valid/def label, target defect, informed workers или полный target evidence list. Использовать непрозрачные packet IDs; mapping хранить отдельно. Проверить и текст packets на случайные метаданные, не только названия JSON-полей.

Terra critic использует подписку OpenAI — это согласованный и доступный путь проекта, не открытый вопрос. Он отделён от SDK Adaption и расходов Qwen. Для подписочного workflow фиксировать frozen prompt, модель/режим, дату, packet и полный ответ; сохранять предусмотренную независимость оценок и ослепление. Конкретный способ передачи packets реализуется через доступный подписочный интерфейс, без подмены его обычным платным API. Calibration (36 отзывов × 2 человека), adjudication и ~29 retest сохраняются; Terra не заменяет людей.

## 8. Бюджет

Для текущего трёхзадачного плана и двух подготовленных сред:

| Стадия | Модель/маршрут | Две задачи | Три задачи |
|---|---|---:|---:|
| Workers: 8 ветвей × до 4 ходов | Luna subscription | до 64 | до 96 |
| Author | Qwen Adaption | 2 | 3 |
| Review: 2 variants × 3 conditions × 2 repeats × 2 reviewers | Qwen Adaption | 48 | 72 |
| Всего без preflight/transport attempts | смешанная конфигурация | до 114 | до 171 |

Luna расходует квоты текущей подписки, не Adaption credits; API-тариф Luna не является счётом за subscription. Дополнительные LLM-шаги построения тегов в этот бюджет не входят: builders детерминированы. Внешние Terra/Astra оценки считаются отдельно.

Тариф и размер контекста `qwen3.5-397b` должны быть подтверждены для текущего провайдера до запуска; значения прежней модели не применяются. Это кредиты провайдера, не доллары. Оценка при раздельных счётчиках и подтверждённых ставках за миллион токенов:

```text
credits ≈ (uncached_input × input_rate + cached_input × cached_rate + output × output_rate) / 1_000_000
```

Повторная отправка локальной истории расходует контекст/квоты; cache hit не гарантируется. Qwen author/reviewer расходуют Adaption credits, Luna workers — доступную subscription quota. До запуска фиксируются лимиты и stop-policy; считать нужно также фактические reasoning tokens, не только число calls.

## 9. Порядок запуска по согласованному little-эксперименту

Little-эксперимент принят как авторитетный и уже согласованный. Различия номеров версий и формулировок документов не объявляются новым блокером и не требуют повторного утверждения дизайна. Требования little technical — private coverage, variant-aware evidence, pre-outcome applicability matching, partial panels, freeze и measurement gates — реализуются в рамках принятого плана. Материалы полного эксперимента не переопределяют little.

Порядок:

1. Зафиксировать mixed-model конфигурацию: Luna workers через omp/pi subscription, Qwen author/reviewer через Adaption, внешние роли без изменений; сохранить экспериментальные инварианты little.
2. Проверить текущую subscription-авторизацию и exact Luna model ID без раскрытия credentials; Adaption-ключ в `.txt` требуется только Qwen-ролям.
3. Выполнить отдельный транспортный smoke test на синтетическом prompt; проверить SDK, response format, usage и ограничения модели.
4. Реализовать intent/response ledger, local history, schemas, packet boundaries, sandbox, router и тесты. Одного SDK-фрагмента выше недостаточно.
5. D0 development freeze → D0 на отдельной task вне шести PoM IDs → fix window.
6. Final freeze конфигурации, prompts, budgets, dependencies, seeds и выбранного корпуса согласно согласованному порядку. Указать, доступен ли immutable snapshot модели; alias сам по себе не гарантирует неизменность весов.
7. Workers → author → human cards/evidence → frozen panels → reviews. Не заменять неудачные tasks, модель или конфигурацию по результатам.
8. Freeze calibration/retest IDs → blind Terra → human calibration/adjudication → measurement quality → analysis. Partial/missing результаты публиковать отдельно.

## 10. API и источники

Используем официальный **Python SDK `adaption`** и `responses.create`, не `chat.completions`. По заметкам endpoint: `POST https://api.prod.adaptionlabs.ai/api/v1/responses`, Bearer auth. Adaption поддерживает OpenAI-подобный Responses формат, но полная совместимость с OpenAI SDK/Agents SDK не подтверждена и этому запуску не нужна. AutoScientist, fine-tuning, `stack:<slug>` и `agentic_memory` здесь не нужны.

Ссылки из предоставленных заметок для preflight-проверки:

- https://docs.adaptionlabs.ai/api/python/
- https://docs.adaptionlabs.ai/inference-stacks/quickstart/
- https://docs.adaptionlabs.ai/inference-stacks/models/
- https://docs.adaptionlabs.ai/api/resources/responses/methods/create/
- https://docs.adaptionlabs.ai/api/python/resources/models/methods/list
- https://docs.adaptionlabs.ai/introduction/create-api-keys
