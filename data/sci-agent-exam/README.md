# Локальная копия SCI-AGENT-EXAM

Источник: https://github.com/SKYLENAGE-AI/Sci-Agent-Exam

Commit: `81f29be06ad155ce41ac4aa4f671594674ae6836` (detached HEAD).

- `upstream/` — скачанный публичный репозиторий (исключён из Git проекта).
- `snapshot_manifest.json` — источник, версия, время загрузки, task IDs и SHA-256 всех 191 tracked files. Контрольные суммы проверены; нескачанных Git LFS pointers нет.

## Ограничение доступности

Это **публичная выборка из пяти примеров**, не development split. Upstream README явно сообщает, что полный набор из 120 задач не включён. В скачанном репозитории development split не обнаружен; поиск Hugging Face по SCI-AGENT-EXAM не вернул datasets.

Для принятого little-протокола (6 основных задач + отдельная D0) этого недостаточно. Нужен полный development corpus от авторов или предоставленный владельцем проекта архив/URL. На момент скачивания основные task IDs и D0 не были выбраны, model calls не выполнялись. Скачивание не означает прохождение eligibility gates.

Не выдавать эту выборку за полный корпус и не дополнять её синтетическими задачами без явного изменения протокола.

## Воспроизведение загрузки

Из корня проекта, если `upstream/` ещё отсутствует:

```bash
git clone https://github.com/SKYLENAGE-AI/Sci-Agent-Exam.git data/sci-agent-exam/upstream
git -C data/sci-agent-exam/upstream checkout --detach 81f29be06ad155ce41ac4aa4f671594674ae6836
```

При первоначальном скачивании код upstream, Docker builds и installers не запускались. Для worker inputs разрешены только instruction и предусмотренные публичные данные; private `tests/`, `solution/`, rubric и golden answers не должны попадать в контекст workers/reviewers. Публичные проверки внутри `environment/data/broken_project/tests/` — часть разрешённого repair project, не private verifier. Лицензии научных источников указаны в README отдельных задач.

По последующему явному выбору оператора все пять примеров включены в отдельный exploratory Little-PoM запуск Terra/Astra/Sol: `runs/sci-agent-exam-terra-sol/`. Все пять Docker-сред собраны и runtime-проверены; подготовка сохранена в `preparation-verified/`. Это development consumption, не confirmatory selection и не upstream benchmark score. Порядок, бюджеты, статус model calls и ограничения — в run artifacts и `docs/sequential_pilot_launch.md`.

## Научное оценивание сохранённых submissions через подписку Astra

`little_pom.scientific_judge` оценивает все пять сохранённых `judge_packet.json` из
`runs/sci-agent-exam-executable/tasks/*/verification/artifacts/`, не запускает
solver/checker повторно и не изменяет старые run artifacts. Это явная замена
upstream-модели `gpt-5.6-sol` на `openai-codex/gpt-6-astra`, а не официальный
результат исходной модели.

Transport revision `exact-trusted-upstream-guidance-prefix-to-codex-instructions/v2`
также явно записывает `role_adaptation`: Codex требует непустое поле `instructions`.
Только доверенный upstream task context и grading guidance до `## Rubric criteria`
переносятся туда из первого text block. Их текст берётся из hash-проверенных
upstream template/config, не из submission. Rubric, artifacts, captions и images
остаются user content. Audit восстанавливает исходный первый text block побайтно:
`instructions + first_user_text`; нет добавленного generic prompt, потерь или дублей.
Пустой, изменённый или неоднозначный guidance prefix отвергается до dispatch.

Локальный preflight (без OAuth/model call; каталог установленного Pi необходим):

```bash
PYTHONPATH=src .venv/bin/python -m little_pom.scientific_judge --output runs/sci-astra-preflight
```

Все пять реальных оценок через уже подключённую подписку Pi OAuth, без нового
API-ключа и без paid-API environment:

```bash
PYTHONPATH=src .venv/bin/python -m little_pom.scientific_judge --output runs/sci-astra-scientific --live
```

Каждый `--output` должен быть новым каталогом вне historical run/upstream.
Существующий каталог не перезаписывается. Автоматических повторов, fallback,
resume после неоднозначного dispatch или повторного solver sample нет.
По умолчанию timeout одного вызова — 1800 секунд; `--timeout` задаёт иной
положительный предел до запуска.

До первого вызова проверяются все пять пакетов, SHA-256 upstream snapshot,
сохранённых submission archives и checker scores, исходные rubric/forced FAILs,
полный лимит ответа **128000 tokens на задачу / 640000 на пять** и контекст модели.
Context preflight использует явно записанную оценку текста (UTF-8 bytes / 3),
консервативный резерв vision tiles и 16000 tokens scaffolding; это не точный
Astra tokenizer и не подтверждение оставшейся subscription quota. Провайдерная
ошибка контекста/квоты/лимита сохраняется как failure, а не обходится обрезанием.
Исходные предупреждения об upstream packet truncation сохраняются; runner не
добавляет нового сокращения, prompt или history. PNG и подписи передаются
настоящими image inputs в исходном порядке, без описаний-замен, resize или удаления.
Поле каталога Pi `inputLimits.images.resize` — профиль нормализации при добавлении
изображений в историю agent session, не жёсткий лимит Codex API. Этот runner не
создаёт agent session и не применяет профиль: исходные PNG, в том числе шире
2000 pixels, отправляются без изменения; реальный отказ API сохраняется как failure.
Параметр публичного Responses API `truncation` в Codex request не передаётся.
Гарантируется отсутствие **клиентского** сокращения, не неизвестное поведение
server-side context handling. HTTP 400 получает только allowlisted diagnosis
(например, `subscription_instructions_required`, `unsupported_truncation`);
сырой HTTP error body/headers не раскрываются. Старый HTTP-rejected run сохраняется
отдельно: это состоявшийся HTTP dispatch без полученного grading output, не «ноль dispatches».

`manifest.json` сохраняет hashes, budgets, model override и role adaptation; для каждой задачи
сохраняются request/source packet, dispatch intent, raw provider response
(включая отправленный payload, SSE observations и usage), verdicts, latency и
result. JSON проходит строгую schema и upstream parser; каждый rubric ID должен
встречаться ровно один раз. Forced FAILs включаются в полный denominator.
Rubric score и полный weighted score вычисляются локально; ошибка не становится
выдуманным нулём или PASS. Итоговый macro mean доступен только при пяти
полных валидных результатах. Общий бинарный порог здесь не вводится.

Offline regression checks (не вызывают модель):

```bash
.venv/bin/python -m pytest tests/runtime/test_subscription.py tests/runtime/test_scientific_judge.py
```
