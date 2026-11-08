# Локальная копия SCI-AGENT-EXAM

Источник: https://github.com/SKYLENAGE-AI/Sci-Agent-Exam

Commit: `81f29be06ad155ce41ac4aa4f671594674ae6836` (detached HEAD).

- `upstream/` — скачанный публичный репозиторий (исключён из Git проекта).
- `snapshot_manifest.json` — источник, версия, время загрузки, task IDs и SHA-256 всех 191 tracked files. Контрольные суммы проверены; нескачанных Git LFS pointers нет.

## Ограничение доступности

Это **публичная выборка из пяти примеров**, не development split. Upstream README явно сообщает, что полный набор из 120 задач не включён. В скачанном репозитории development split не обнаружен; поиск Hugging Face по SCI-AGENT-EXAM не вернул datasets.

Для принятого little-протокола (6 основных задач + отдельная D0) этого недостаточно. Нужен полный development corpus от авторов или предоставленный владельцем проекта архив/URL. Основные task IDs и D0 пока не выбраны, model calls не выполнялись, задачи не development-consumed. Скачивание не означает прохождение eligibility gates.

Не выдавать эту выборку за полный корпус и не дополнять её синтетическими задачами без явного изменения протокола.

## Воспроизведение загрузки

Из корня проекта, если `upstream/` ещё отсутствует:

```bash
git clone https://github.com/SKYLENAGE-AI/Sci-Agent-Exam.git data/sci-agent-exam/upstream
git -C data/sci-agent-exam/upstream checkout --detach 81f29be06ad155ce41ac4aa4f671594674ae6836
```

Код upstream, Docker builds и installers не запускались. При реализации worker inputs разрешены только instruction и предусмотренные данные; `tests/`, `solution/`, rubric и golden answers не должны попадать в контекст workers/reviewers. Лицензии научных источников указаны в README отдельных задач.
