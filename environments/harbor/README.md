# Harbor для little-эксперимента

Установлено локально: Python 3.12.12, `harbor==0.20.0`, окружение `.venv-harbor/`. Проверены `harbor --version`, `harbor --help`, `harbor run --help`, `harbor download --help`. Все установленные версии сохранены в `requirements.lock`.

Восстановление из корня проекта:

```bash
uv venv --python 3.12 .venv-harbor
uv pip sync --python .venv-harbor/bin/python environments/harbor/requirements.lock
source .venv-harbor/bin/activate
harbor --version
```

## Контейнерный backend установлен и проверен

Установлен Docker Desktop 4.97.0 для Apple Silicon (`brew install --cask --no-binaries docker-desktop`). CLI-ссылки добавлены в `/opt/homebrew/bin`, Compose/Buildx — в `~/.docker/cli-plugins` без перезаписи существующих файлов. Docker Desktop запущен.

Проверены Engine 29.9.2 (`linux/arm64`), Compose v5.6.1 и успешное выполнение `docker run --rm --network=none hello-world`. Контейнер после проверки удалён; image остаётся в локальном cache. Docker VM видит 10 CPUs и около 7.75 GiB RAM.

Команды диагностики:

```bash
docker version
docker info
docker compose version
```

Сборки научных задач и oracle smoke test пока не выполнялись. Успешный hello-world не означает совместимость всех задач с arm64 и доступными ресурсами. Upstream также рекомендует Modal/Daytona, но облачные extras, credentials и платные ресурсы здесь не настраивались.

После готовности Docker: выбрать отдельную preflight/D0-задачу, проверить её Dockerfile, ресурсы и архитектуру, затем выполнить один oracle smoke test через `harbor run --path <task-directory> --agent oracle --n-concurrent 1 --env docker` (параметры доступны в `harbor run --help`). Не запускать весь benchmark автоматически.

Harbor предоставляет контейнерное исполнение/проверку задач. Он не заменяет собственный little runner: независимые Qwen contexts, intent/response ledger, router, freeze и blind Astra/Terra workflow ещё требуют реализации. Oracle/solution/test artifacts нельзя передавать workers или reviewers. Сетевую изоляцию little требуется проверять явно, она не считается обеспеченной только установкой Harbor.
