# T02 — Model transport, immutable ledger и resume

## Цель и границы
Надёжный Qwen transport через Adaption с mock backend. Подписочные Astra/Terra здесь не реализуются. Общий контракт — T01; результат сервиса `{status, data, error}`. В рамках реализации не выполнять платные запросы.

## Владение файлами
`src/little_pom/runtime/`, `tests/runtime/`. Не менять config, worker runner, packaging или файлы других тикетов. Требуемые зависимости передать T08.

## Интерфейс
`SwarmClient.call(request, schema_id, execution_id) -> ServiceResult`.
Request содержит model/config, instructions, packet, local history, output budget. `Ledger.lookup(execution_id)` возвращает intent/response/status. `ArtifactStore.put_once(path, payload)` запрещает overwrite; `FreezeStore.create/verify(manifest)` обслуживает stage freeze.

## Сделать
- Host-side чтение ключа только из явно заданного secret path; никаких секретов в hash, packet, logs или sandbox.
- Request hash по полной конфигурации, schema version и контексту; execution ID стабилен. Разный hash при том же ID — ошибка.
- Межпроцессный lock, durable intent до отправки, полный raw response до parsing; атомарная запись и append-only audit.
- Существующий response переиспользуется; intent без response после restart означает incomplete.
- SDK retries выключены. Timeout после возможной отправки → ambiguous execution, без повторного sample. Первый вариант — без transport retries вообще.
- Пустой, усечённый, schema-invalid output → incomplete; никаких repair calls.
- Сохранять реальные usage/latency/model metadata; отсутствующие значения оставлять null.
- Mock backend со сценариями success, invalid JSON, truncation, crash, timeout; не требовать установленного SDK для mock tests.

## Параллельная работа
Начать по dict-контракту T01, внедрять validator/hash через interfaces до появления общей реализации. Mock client используется T04/T06/T08. Ledger не должен знать condition-specific scoring.

## Приёмка
Тесты crash-before-send/after-send/after-response, повторного запуска и конкурентного доступа проходят; сохранённый response не вызывает сеть. Freeze tampering обнаруживается. Log-redaction tests не находят ключ. Все tests offline; живой preflight только отдельной явно разрешённой командой.
