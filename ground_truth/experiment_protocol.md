# MASTER PROTOCOL v5.1
## Распределённая проверка допуска исследовательских задач в роях LLM-агентов

**Статус:** pre-freeze master protocol пилота выполнимости  
**Дата версии:** 2026-11-04  
**Основной источник задач:** SCI-AGENT-EXAM  
**Единая модель исследуемого роя и governance \(M_S\):** GPT-6 Luna  
**Внешний offline evaluator \(M_E\):** GPT-6.1 Terra  
**Основной контраст:** dependency-aware routing \(f\) против равномерной панели \(U\)  
**Якорь:** \(N\) — self-revision без внешнего рецензента  
**Целевой controlled sample:** 30 эпизодов  
**Natural sample:** до 6 эпизодов  
**Максимальный объём при полном наборе:** 594 экспериментальных запуска  

---

# 0. Назначение документа

Настоящий документ является единым нормативным описанием пилотного эксперимента.

Он определяет:

1. источники данных;
2. временную границу и единицу анализа;
3. отбор задач;
4. построение рабочих ветвей;
5. построение графа и пула рецензентов;
6. информационные области автора и рецензентов;
7. построение `valid`, `def` и natural-карточек;
8. evidence-map;
9. экспериментальные условия \(f\), \(U\), \(N\);
10. последовательность одного запуска;
11. основные и вторичные исходы;
12. экспертную оценку;
13. анализ;
14. бюджет и ограничения ресурсов;
15. drift-control;
16. критерии выполнимости;
17. freeze-манифест и правила изменения протокола.

Документ **не** является подтверждающим протоколом и **не** используется для доказательства эффективности \(f\). Пилот предназначен для проверки измеримости механизма, воспроизводимости процедуры и планирования подтверждающей стадии.

---

# 1. Основной исследовательский вопрос

Основной вопрос:

> При одинаковом размере панели, одинаковой модели рецензентов, одном и том же релевантном пуле, одинаковых контекстных лимитах и одинаковом ресурсном лимите на запуск снижает ли dependency-aware routing \(f\) частоту дефектных допусков относительно равномерного выбора \(U\), не достигая этого простой блокировкой обоснованных задач?

В пилоте эффект не подтверждается статистически. Проверяется, существует ли измеримый контраст и достаточная дисперсия для будущего confirmatory study.

Якорь \(N\) используется для оценки того, насколько сама внешняя критика изменяет поведение модели. \(N\) не является вычислительно сопоставимым с \(f/U\).

Весь **исследуемый causal path** строится на одной архитектуре и одной замороженной конфигурации GPT-6 Luna:

\[
M_{\mathrm{worker}}
=
M_{\mathrm{author}}
=
M_{\mathrm{reviewer}}
=
M_{\mathrm{moderation}}
=
M_{\mathrm{resolution}}
=
M_{\mathrm{admission}}
=
M_S.
\]

Следовательно, эксперимент не использует архитектурную гетерогенность как экспериментально варьируемый источник качества. Проспективная конструкция пула намеренно создаёт различающиеся рабочие состояния одной и той же модели через совместное действие:

\[
\text{task decomposition},
\quad
\text{role/task instruction},
\quad
\text{накопленной истории},
\quad
\text{доступа к evidence},
\quad
\text{позиции в графе зависимостей}.
\]

Для кандидатов поэтому выполняется:

\[
M_S=\mathrm{const},
\qquad
H_{ij}\neq H_{il},
\qquad
\mathcal A_{ij}\neq\mathcal A_{il},
\]

но core pilot **не идентифицирует причинный источник этих различий**. В нём перечисленные механизмы связаны конструкцией пула.

Основной вопрос core pilot уже: позволяет ли dependency-aware routing среди **уже сформированных различающихся рабочих состояний одной архитектуры** улучшать допуск относительно равномерной маршрутизации.

Отдельная идентификация вклада role prompting, накопленной history и соответствия history позиции в графе требует будущих `role-only` и `history-shuffle` контролей (§77).

GPT-6.1 Terra не входит в causal path допуска и не может менять карточку, возражение или решение роя. Она используется только после завершения экспериментального запуска как внешний автоматизированный evaluator, калибруемый человеческими экспертами.

---

# 2. Единица анализа и временная граница

## 2.1. Эпизод

Единица независимости:

\[
i=\text{исследовательская точка выбора}.
\]

Для каждой benchmark-задачи определяется момент заморозки \(t_i\).

Замороженный корпус:

\[
\mathcal D_i^0
\]

содержит только сведения, существовавшие к \(t_i\):

- исходную научную цель \(G_i\);
- root/parent task \(P_i\);
- журналы;
- состояния рабочих ветвей;
- созданные артефакты;
- provenance;
- зарегистрированные ограничения;
- гипотезы;
- противоречия;
- зарегистрированный граф рабочих отношений.

Будущие результаты запрещены во входах агентов и в основной outcome-разметке.

## 2.2. Parent task

**Parent task \(P_i\)** — это исходная benchmark-задача в том виде, в котором она передаётся экспериментальной системе **до создания любых worker branches**.

Она является корневым узлом task graph.

До запуска ветвей фиксируются:

- идентификатор \(P_i\);
- текст цели;
- разрешённые входные файлы;
- общие ограничения;
- исходные артефакты;
- критерии завершения, если они присутствуют в benchmark.

Каждая worker subtask регистрируется как дочерний узел \(P_i\) **до её исполнения**.

Карточка \(q_i^0\) не является parent task и появляется только после завершения worker stage.

---

# 3. Значимый дефект

Дефект считается **значимым**, если его устранение по сведениям, доступным к \(t_i\), требует изменить хотя бы одно:

1. цель действия;
2. масштаб действия;
3. обязательные условия;
4. решение о допуске.

Используются шесть типов:

1. несоответствие исходной цели;
2. игнорирование установленного результата;
3. принятие предположения за факт;
4. невыполненное предварительное условие;
5. отсутствие связи результата с дальнейшим решением;
6. несоразмерный масштаб исполнения.

Ошибка метода входит в предмет исследования только тогда, когда она меняет допуск, обязательные условия или масштаб.

---

# 4. Финальные решения

Для эпизода \(i\), варианта \(v\), условия \(c\), повторения \(r\):

\[
Y_{ivcr}
\in
\{
\mathrm{execute},
\mathrm{explore},
\mathrm{escalate},
\mathrm{incomplete}
\}.
\]

`explore` является отдельным действием:

\[
q'=(U,B,R,S),
\]

где:

- \(U\) — конкретная неопределённость;
- \(B\) — ограниченный бюджет;
- \(R\) — допустимая обратимость;
- \(S\) — stop condition.

Если разведка воспроизводит исходный значимый дефект, она считается дефектным допуском.

---

# 5. Проверенный реестр внешних фактов

Перед freeze сохраняются локальные PDF/HTML-копии источников и контрольные суммы.

## 5.1. SCI-AGENT-EXAM

Подтверждено по arXiv:2610.32441:

- submission: 23 октября 2026;
- 120 задач;
- 11 научных областей;
- 7 типов research workflow;
- задачи требуют executable и textual artifacts;
- executable artifacts оцениваются программно;
- textual artifacts — calibrated LLM judge;
- benchmark содержит 10 evaluated frontier agents;
- в статье указан публичный код/данные.

Источник:  
https://arxiv.org/abs/2610.32441

Конкретный GitHub commit/release benchmark должен быть скачан и хеширован до freeze. Наличие URL в статье не заменяет проверку конкретной версии.

## 5.2. GPT-6 Luna

Подтверждено по OpenAI:

- model ID: `gpt-6-luna`;
- knowledge cutoff: 18 мая 2026;
- API release: 22 сентября 2026;
- context: 1,050,000;
- max output: 128,000;
- `medium` поддерживается и является default;
- reasoning + tools: Responses API;
- Chat Completions function calling для Luna — только при reasoning `none`;
- при reasoning effort выше `none` должны быть удалены `temperature`, `top_p`, `top_logprobs`;
- Standard, prompt \(\le272K\):
  - input: \$0.10 / 1M;
  - cached input: \$0.01 / 1M;
  - cache write: \$0.125 / 1M;
  - output: \$0.50 / 1M;
- при \(>272K\) input:
  - input/cache rates ×2;
  - output ×1.5;
- Batch/Flex дешевле Standard;
- Fast дороже;
- regional processing может добавлять premium;
- датированного snapshot ID для Luna в model page нет.

Источники:

https://developers.openai.com/api/docs/models/gpt-6-luna  
https://developers.openai.com/api/docs/changelog  
https://developers.openai.com/api/docs/guides/latest-model

В пилоте используется только **Standard processing**.

## 5.3. GPT-6.1 Terra как внешний evaluator

Подтверждено по официальным источникам OpenAI:

- model ID: `gpt-6.1-terra`;
- API/public release: 26 октября 2026;
- knowledge cutoff: 1 июня 2026;
- context: 1,050,000 токенов;
- max output: 128,000 токенов;
- `reasoning.effort=medium` поддерживается и является default;
- tool calling требует Responses API;
- Standard pricing:
  - input: \$1.00 / 1M;
  - cached input: \$0.05 / 1M;
  - cache write: \$1.25 / 1M;
  - output: \$5.00 / 1M;
- при input \(>272K\):
  - input/cache rates ×2;
  - output ×1.5;
- Batch/Flex дешевле Standard;
- Fast дороже Standard.

Источники:

https://developers.openai.com/api/docs/models/gpt-6.1-terra  
https://developers.openai.com/api/docs/pricing  
https://openai.com/index/introducing-gpt-6-1-terra/

В настоящем протоколе Terra обозначается

\[
M_E=\texttt{gpt-6.1-terra}
\]

и используется **только offline после завершения действий исследуемого роя**.

Terra:

- не участвует в worker stage;
- не создаёт карточку;
- не является reviewer;
- не модерирует отзывы;
- не разрешает возражения;
- не выполняет admission;
- не передаёт информацию обратно в рой.

Поэтому стоимость и ошибки Terra относятся к **измерительному слою эксперимента**, а не к стоимости или способности исследуемой swarm architecture.

Terra и Luna принадлежат одной модельной линии OpenAI, поэтому Terra не считается архитектурно независимым «gold judge». Её пригодность как автоматизированного evaluator должна быть эмпирически подтверждена на человеческой calibration subset (§55–§57).

## 5.4. ResearchClawBench

Подтверждено:

- base benchmark: 40 задач;
- 10 областей;
- задачи основаны на реальных опубликованных статьях;
- arXiv v1: 28 мая 2026;
- GitHub README сообщает initial public release 19 марта 2026.

Источники:

https://arxiv.org/abs/2606.07591  
https://github.com/InternScience/ResearchClawBench

Поскольку публичный initial release предшествует cutoff Luna 18 мая 2026, ResearchClawBench **не считается чистым anti-contamination holdout для Luna**.

Он может использоваться только как будущий внешний replication corpus с отдельным contamination analysis.

## 5.5. Autoresearch Novelty Bench

Подтверждено dataset card:

- 10,380 experiments;
- 646 formal ideas;
- 3,249 proposals;
- 40 time-anchored snapshots;
- raw run timestamps/outcomes/linkage.

Источник:

https://huggingface.co/datasets/evo-hq/autoresearch-novelty-bench

Используется только development-side для temporal/provenance tooling.

## 5.6. OpenDiscoveryTrace

Подтверждено по arXiv:2609.09203:

- 558 complete trajectories;
- 124 tasks;
- structured per-step traces;
- tool calls, observations, errors, revision triggers, confidence.

Источник:

https://arxiv.org/abs/2609.09203

Используется только development-side.

---

# 6. Разделение наборов

## 6.1. Development

15 SCI-AGENT-EXAM tasks.

Первые 5 из них образуют **D0 micro-development** для обязательного pre-freeze dry run.

Дополнительно:

- Autoresearch Novelty Bench;
- OpenDiscoveryTrace.

PrimeIntellect/OpenDiscoveryTrace не используются как замена проспективному pool \(m_i\ge15\).

## 6.2. Controlled pilot

Цель:

\[
N_C=30.
\]

Задачи набираются последовательно по seeded eligibility order.

Если эпизод исключён до outcomes из-за:

- \(m_i<15\);
- неприменимого controlled defect;
- evidence strata 4–5;
- failed style-blindness;

переходят к следующей задаче в замороженном candidate order.

Все просмотренные/подготовленные задачи считаются consumed и не возвращаются в confirmatory pool.

Если 30 routable episodes получить не удаётся в пределах заранее замороженного candidate list, используется фактический \(N_C<30\).

## 6.3. Natural reserve

До 20 отдельных задач.

Порядок замораживается до natural screening.

Резерв не расширяется после просмотра labels/outcomes.

Natural sample:

\[
N_N\le6.
\]

## 6.4. Confirmatory pool

Все задачи SCI-AGENT-EXAM, которые:

- не использовались development;
- не открывались при controlled/natural screening;
- не использовались replacement-процедурами,

остаются заблокированными для будущего confirmatory study.

ResearchClawBench рассматривается только как потенциально контаминированная внешняя репликация.

---

# 7. Eligibility задач

## 7.1. Seeded order

SCI-AGENT-EXAM task IDs перемешиваются локальным PRNG с замороженным seed.

Eligibility оценивается **последовательно в этом порядке**.

Post-hoc пропуск «неудобной» задачи запрещён.

## 7.2. Критерий

Два независимых human eligibility raters должны подтвердить наличие не менее двух содержательно разумных следующих research steps.

Действия \(a\) и \(b\) должны:

1. быть допустимыми;
2. уменьшать релевантную неопределённость;
3. различаться содержательно;
4. не быть заведомо доминируемо неверными;
5. требовать исследовательского суждения.

При расхождении — третий adjudicator.

---

# 8. Безопасный task workspace

## 8.1. Allowlist, а не denylist

Worker sandbox создаётся **из allowlist файлов, предназначенных benchmark для агента**.

В sandbox не копируются:

- gold answers;
- target solutions;
- evaluation rubrics, если они скрыты от benchmark-agent;
- evaluator code;
- private tests;
- expected outputs;
- target paper/solution;
- любые файлы, предназначенные только для scoring.

## 8.2. Изоляция evaluator

Evaluator и gold находятся в отдельной файловой области, недоступной worker/author/reviewer processes.

Исполняемый benchmark verifier запускается только внешним evaluation process.

## 8.3. Хэши

Для каждого task workspace фиксируются:

- SHA-256 manifest всех файлов;
- container image digest;
- dependency lock;
- benchmark commit/revision.

Network disabled.

---

# 9. Проспективное создание рабочих ветвей

На задачу:

- 20 worker branches;
- 1 author state.

## 9.1. Worker template bank

До freeze фиксируется банк предметно-нейтральных шаблонов:

1. goal decomposition;
2. data/provenance check;
3. baseline/reproduction;
4. assumptions/preconditions;
5. alternative explanation;
6. falsification/contradictory evidence;
7. metric/measurement adequacy;
8. feasibility/compute constraints;
9. robustness/sensitivity;
10. decision relevance.

Шаблоны назначаются seed-процедурой независимо от будущей карточки и дефекта.

---

# 10. Level 1

10 независимых ветвей:

\[
L1_i=\{w_{i,1}^{(1)},\dots,w_{i,10}^{(1)}\}.
\]

Каждая получает:

- parent task \(P_i\);
- allowlisted benchmark workspace;
- собственную subtask;
- одинаковый tool class;
- одинаковый \(T_{\max}\).

L1 не видит друг друга.

---

# 11. Level 2

После полного завершения L1 и до \(q_i^0\):

\[
L2_i=\{w_{i,1}^{(2)},\dots,w_{i,10}^{(2)}\}.
\]

## 11.1. Circulant assignment

Фиксируется:

\[
s=3.
\]

Ветвь \(L2_j\) получает структурированные результаты:

\[
L1_j,\qquad L1_{(j+s)\bmod10}.
\]

Индексы считаются по \(\{0,\dots,9\}\).

Следовательно, каждый L1-result используется ровно двумя L2-ветвями.

Никаких случайных исправлений пар после назначения не требуется.

## 11.2. Что видит L2

L2 получает **только structured output** L1:

\[
O_{ij}
=
(
result,\,
evidence\_ids,\,
limitations,\,
artifact\_ids,\,
provenance
).
\]

L2 не получает:

- полную L1 conversation history;
- скрытые reasoning traces;
- недоступные исходные материалы;
- evaluator outputs.

---

# 12. Лимит worker interaction

Стартовое D0-значение:

\[
T_{\max}=8.
\]

Окончательное значение выбирается только на 15 development tasks по:

- completion rate;
- truncation;
- cost;
- duplicate rate;
- branch quality.

После freeze \(T_{\max}\) неизменяем.

---

# 13. Обязательный branch output

Каждая ветвь обязана вернуть:

\[
O_{ij}
=
(
R_{ij},
E_{ij},
L_{ij},
A_{ij},
P_{ij}
),
\]

где:

- \(R_{ij}\) — result;
- \(E_{ij}\) — evidence IDs;
- \(L_{ij}\) — limitations;
- \(A_{ij}\) — created artifact IDs;
- \(P_{ij}\) — provenance/dependencies.

Номинальная роль без результата или проверяемого ограничения кандидатом не считается.

---

# 14. Author state

Author state создаётся **до worker generation** как отдельный proposer state:

\[
a_i^0=
(
P_i,\,
\mathcal C_i^{read},\,
H_i^{author}
).
\]

До завершения workers author state:

- не выполняет worker-subtask;
- не получает worker histories;
- не является кандидатом-рецензентом.

После завершения workers author получает только frozen handoffs.

После получения handoffs author state превращается в:

\[
a_i^{precard}
\]

и генерирует \(q_i^0\).

Именно этот frozen author state копируется для последующего revision.

Таким образом «ветвь автора» означает proposer-state, а не 21-ю исследовательскую worker branch.

---

# 15. Граф рабочих отношений

## 15.1. Создание графа

Граф:

\[
\mathcal G_i^0
\]

строится до \(q_i^0\).

Узлы:

- parent task;
- worker subtasks;
- artifacts;
- constraints/evidence nodes.

Рёбра:

1. `child_of`;
2. `producer_of`;
3. `consumer_of`;
4. `shared_data_or_constraint`;
5. `alternative_or_contradiction`;
6. `goal_or_acceptance_relation`.

## 15.2. Регистрация

- `child_of` создаётся при назначении subtask;
- `producer_of` — при сохранении artifact;
- `consumer_of` L2 — при circulant assignment до L2 execution;
- остальные — только при наличии зарегистрированного machine/human-readable provenance.

Карточка \(q_i^0\) не изменяет \(\mathcal G_i^0\).

## 15.3. Graph audit

До outcomes случайная pre-registered подвыборка эпизодов проходит audit двумя независимыми аудиторами.

Оцениваются:

\[
FPR_{edge}
=
\frac{\text{ложные рёбра}}{\text{проверенные существующие рёбра}},
\]

и пропуски на sampled source records.

Публикуются:

- false-positive edges;
- missed edges;
- disagreement.

Graph audit не использует outcomes.

---

# 16. Ограничение искусственности L2-структуры

Producer-consumer edges L2 частично созданы экспериментальной конструкцией.

Поэтому положительный \(f-U\) не должен интерпретироваться как доказательство пользы на полностью естественных research graphs.

Обязательны два diagnostics:

## 16.1. L1-native diagnostic

На L1 pool из 10 ветвей, где нет искусственных L1→L2 consumer edges, описательно рассчитываются:

- естественные relations;
- evidence diversity;
- потенциальное coverage для \(k=3\).

Outcome experiment на L1-only pool не проводится, поскольку он не удовлетворяет основному \(m_i\ge15\).

## 16.2. Edge-removal sensitivity

После freeze, но до outcome analysis вычисляется:

\[
f^{-pc}
\]

— политика \(f\) на полном пуле после удаления искусственно созданных L1→L2 `consumer_of` edges.

Публикуются:

- overlap \(D_i\) и \(D_i^{-pc}\);
- изменение tie-break rate;
- изменение predicted coverage.

Это diagnostic, а не отдельное experimental condition.

---

# 17. Дедупликация

Общие исходные benchmark-файлы исключены из сравнения.

Для кандидата:

\[
\mathcal A_{ij}^{gen}
\]

— собственные созданные artifacts/fragments.

\[
d_J(j,l)
=
1-
\frac{
|\mathcal A_{ij}^{gen}\cap\mathcal A_{il}^{gen}|
}{
|\mathcal A_{ij}^{gen}\cup\mathcal A_{il}^{gen}|
}.
\]

Стартовый threshold:

\[
d_J\ge0.20.
\]

Проверяется на development и замораживается.

Producer-consumer provenance relation не является основанием считать ветви дубликатами.

После exclusions:

\[
m_i=|R_i|\ge15.
\]

Иначе эпизод исключается до card construction.

---

# 18. Контекст кандидата

Экстрактивная подготовка:

1. subtask;
2. result;
3. limitations;
4. used evidence;
5. provenance;
6. remaining history по времени.

\[
\mathcal W_{ij}^{read}
\]

— фактически доступная область.

Лимит \(|\mathcal W_{ij}^{read}|\) замораживается development.

---

# 19. Общее досье

\[
\mathcal C_i^{read}
\]

содержит:

- \(P_i\);
- исходную цель;
- обязательные ограничения;
- action classes;
- budget rules;
- stop conditions.

Worker results туда не копируются.

---

# 20. Graph package

\[
\mathcal P_i^{read}
\]

формируется из графа \(\mathcal G_i^0\), существовавшего **до \(q_i^0\)**.

Включаются только непосредственно связанные с \(P_i\):

- зарегистрированные противоречия;
- входные artifacts;
- выходные artifacts;
- ограничения/acceptance links.

Лимит:

\[
|\mathcal P_i^{read}|\le2048
\]

токенов.

Truncation rule замораживается development.

---

# 21. Reviewer area

\[
\mathcal A_{ij}^{review}
=
\mathcal W_{ij}^{read}
\cup
\mathcal C_i^{read}
\cup
\mathcal P_i^{read}.
\]

Ссылка на недоступный artifact не расширяет область.

---

# 22. Author handoff

Для каждого worker:

\[
h_{ij}
=
ExtractiveSummary(O_{ij}).
\]

Порядок:

1. result;
2. limitations;
3. evidence IDs;
4. artifact IDs;
5. provenance IDs.

Свободный LLM-summary не используется.

Лимит \(L_h\) фиксируется development.

Author получает:

\[
\mathcal A_i^{author}
=
P_i
\cup
\mathcal C_i^{read}
\cup
\bigcup_j h_{ij}.
\]

Полные worker histories недоступны.

---

# 23. Карточка \(q_i^0\)

GPT-6 Luna author state генерирует:

\[
q_i^0.
\]

Обязательные поля:

1. proposed action;
2. uncertainty;
3. grounds/evidence;
4. expected decision impact;
5. scale/budget;
6. preconditions.

---

# 24. Controlled valid-card

Два human validators, не являющиеся outcome raters, строят/проверяют:

\[
q_i^{valid}.
\]

Минимальная правка \(q_i^0\).

Valid-card принимается только если по \(\mathcal D_i^0\) не найден значимый дефект.

---

# 25. Applicability дефектов

До назначения target defect для каждой candidate valid-card слепо размечивается:

\[
A_{id}\in\{0,1\},
\]

где \(A_{id}=1\) означает, что defect type \(d\) можно внести:

- одним содержательным изменением;
- без необходимости второго дефекта;
- без разрушения задачи до бессмысленности.

Разметку делают два независимых editors/validators.

При расхождении — adjudicator.

---

# 26. Seeded defect assignment

Создаются 30 defect slots:

\[
5\times d_1,\dots,5\times d_6.
\]

На основе applicability matrix \(A_{id}\) строится bipartite matching task↔defect-slot.

Если существует несколько perfect matching:

- используется deterministic seeded tie-break;
- objective не использует outcomes, \(f\), coverage или future results.

Если perfect matching нет:

- переходят к следующей eligible candidate task по frozen order;
- пересчитывают matching;
- consumed tasks не возвращаются в confirmatory pool.

---

# 27. Def-card

После assignment:

\[
q_i^{def}
=
Inject_{d_i}(q_i^{valid}).
\]

Проверяется:

1. присутствует target defect;
2. не внесён второй значимый defect;
3. остальные существенные поля пары сохранены.

---

# 28. Edit distance и style leakage

Token-level normalized Levenshtein:

\[
d_{edit}(a,b)
=
\frac{
LevTokens(a,b)
}{
\max(|a|,|b|)
}.
\]

## 28.1. Style distinguishability

Цель теста — проверить, можно ли угадать `def`/`valid` по поверхности карточки без доступа к исходному исследовательскому корпусу.

Каждая карточка предъявляется трём blind style raters. Они получают только текст карточки и бинарно классифицируют её как `def` или `valid`.

Для каждой карточки определяется одно card-level решение простым большинством трёх оценок. Таким образом статистическая единица теста — **карточка**, а не отдельная оценка рейтера.

При полном controlled corpus тест содержит:

\[
30\ \mathrm{def}
+
30\ \mathrm{valid}
=
60
\]

карточек с точно сбалансированными классами. Поэтому на полном наборе:

\[
BA_{style}
=
Accuracy_{style}.
\]

Основной критерий отсутствия практически опасной style leakage:

\[
BA_{style}\le0.60
\]

и одновременно односторонняя точная 95%-я верхняя граница Clopper--Pearson должна удовлетворять:

\[
U_{0.95}^{CP}(Accuracy_{style})<0.70.
\]

Для полного набора из 60 карточек это означает не более:

\[
35/60
\]

правильных card-level классификаций, поскольку

\[
35/60\approx0.5833,
\qquad
U_{0.95}^{CP}(35,60)\approx0.691,
\]

тогда как при

\[
36/60=0.60
\]

верхняя односторонняя 95%-я граница уже превышает \(0.70\).

Если до style-test контролируемый корпус содержит менее 30 пар, используется тот же критерий на фактическом числе карточек:

\[
BA_{style}\le0.60,
\qquad
U_{0.95}^{CP}<0.70.
\]

Дополнительно публикуются:

- confusion matrix;
- sensitivity к `def`;
- specificity к `valid`;
- agreement трёх style raters.

Если aggregate set до outcome calls не проходит критерий:

- editing procedure перерабатывается;
- либо пары заменяются/исключаются по заранее определённой процедуре;
- никакие \(f/U/N\) outcomes не могут использоваться при этом решении.

---
# 29. Natural corpus

Natural reserve обрабатывается в frozen order.

\(q_i^0\) не редактируется.

Два human natural-screeners определяют наличие значимого дефекта.

При расхождении — adjudicator.

В natural core routing sample включаются первые до 6 эпизодов, удовлетворяющих:

1. естественный значимый defect подтверждён;
2. после evidence-map основной defect относится к strata 1–3.

Strata 4–5 natural cases логируются как `non-routable` и не запускаются в core routing experiment.

Резерв после просмотра labels не расширяется.

---

# 30. Evidence-map

Два независимых blind annotators размечают для defect \(d\):

\[
\mathcal B_{id}^{(1)},\dots,\mathcal B_{id}^{(s)}.
\]

Третий adjudicates.

Map annotators не видят:

- \(f\);
- \(U\);
- panels;
- outcomes.

---

# 31. Evidence strata

1. каждый кандидат индивидуально покрывает набор;
2. некоторые, но не все;
3. индивидуально никто, но объединение панели \(k\le3\) покрывает;
4. никакая панель \(k\le3\) не покрывает;
5. доступность не установлена.

## 31.1. Core routing eligibility

Controlled target defect или natural primary defect с stratum 4–5 **исключается из core outcome runs до первого экспериментального запуска**.

Причина фиксируется.

Это не считается отрицательным результатом \(f\); такой defect не позволяет тестировать routing mechanism.

---

# 32. Индивидуальная доступность

\[
V_{ijd}
=
\mathbf 1
\left\{
\exists s:
\mathcal B_{id}^{(s)}
\subseteq
\mathcal A_{ij}^{review}
\right\}.
\]

\[
I_{id}(S)=\max_{j\in S}V_{ijd}.
\]

Пусть:

\[
a_{id}
=
\sum_{j\in R_i}V_{ijd}.
\]

Для равномерной панели \(k=3\):

\[
q^I_{id}
=
1-
\frac{
\binom{m_i-a_{id}}{3}
}{
\binom{m_i}{3}
}.
\]

\[
M^I_{id}
=
I_{id}(D_i)-q^I_{id}.
\]

\(M^I\) — conditional expectation contrast, а не observed \(D-U\).

---

# 33. Исправленная метрика эксклюзивного evidence

Определим shared/author area:

\[
\mathcal S_i
=
\mathcal A_i^{author}
\cup
\mathcal C_i^{read}
\cup
\mathcal P_i^{read}.
\]

Маршрутизационно релевантное evidence существует только тогда, когда достаточный набор требует хотя бы одного фрагмента из private worker area.

\[
X_i
=
\mathbf1
\left\{
\exists d,s,j:
\mathcal B_{id}^{(s)}
\subseteq
\left(
\mathcal S_i
\cup
\mathcal W_{ij}^{read}
\right)
\right.
\]

\[
\left.
\land\
\mathcal B_{id}^{(s)}
\nsubseteq
\mathcal S_i
\land
\left(
\mathcal B_{id}^{(s)}
\cap
\mathcal W_{ij}^{read}
\right)
\setminus
\mathcal S_i
\neq\varnothing
\right\}.
\]

То есть общий пакет или общее досье сами по себе **не могут** сделать \(X_i=1\).

Минимум один критический фрагмент достаточного набора должен:

- находиться в \(\mathcal W_{ij}^{read}\);
- отсутствовать у автора;
- отсутствовать в \(\mathcal C_i^{read}\);
- отсутствовать в \(\mathcal P_i^{read}\).

---

# 34. Coverage gate

Для episode:

\[
\overline M_i^I
=
\frac1{n_i}\sum_dM^I_{id}.
\]

Core information-mechanism episode удовлетворяет:

\[
\overline M_i^I\neq0
\quad\land\quad
X_i=1.
\]

Порог:

\[
\tau_{coverage}\ge0.40.
\]

Если менее 40% core episodes удовлетворяют этому условию, пилот признаётся неинформативным относительно заявленного distributed-information mechanism.

---

# 35. Потеря evidence усечением

Публикуется:

\[
L_{frag}
=
P(
\text{из sufficient set удалён хотя бы один фрагмент}
).
\]

Отдельно:

\[
L_{destroy}
=
P(
\text{truncation уничтожил последнее доступное полное покрытие sufficient set}
).
\]

---

# 36. Post-hoc branch correctness covariate

Benchmark evaluator/gold недоступен агентам.

**После завершения всех experimental model runs** выполняется отдельный offline evaluation.

Если branch artifact имеет официальный программный verifier SCI-AGENT-EXAM, сохраняется:

\[
Q_{ij}^{prog}.
\]

Если программная проверка неприменима:

\[
Q_{ij}^{prog}=NA.
\]

\(Q_{ij}^{prog}\):

- не доступен \(f\);
- не доступен агентам;
- не используется для исключения runs;
- является только post-hoc explanatory covariate.

Textual LLM judge benchmark не используется как gold correctness ветви в primary analysis.

---

# 37. Политика \(f\)

\[
D_i=f(R_i,\mathcal D_i^0,3).
\]

Последовательно:

1. maximum uncovered relation-type coverage;
2. maximum direct links to \(P_i\)/registered artifacts;
3. minimum overlap generated materials;
4. PRNG tie-break.

\(D_i\) фиксируется для episode.

## 37.1. Tie-break

\[
T_{tie}
=
\frac{
N_{tie\ slots}
}{
3N_f
}.
\]

Gate:

\[
T_{tie}\le0.25.
\]

---

# 38. Политика \(U\)

\[
U_{ir}
\sim
Uniform\{S\subset R_i:|S|=3\}.
\]

Одна \(U_{ir}\) применяется к `valid` и `def` в repetition \(r\).

\[
|D_i\cap U_{ir}|
\sim
Hypergeom(m_i,3,3),
\]

\[
E|D_i\cap U_{ir}|=\frac9{m_i}.
\]

---

# 39. Условие \(N\)

Название:

> **Self-revision without external reviewer**

В \(N\):

- нет reviewer calls;
- нет reviewer evidence;
- frozen author state выполняет один self-revision;
- затем выполняется admission.

Поскольку внешних возражений нет:

\[
Z=0.
\]

Отдельный substantive resolution call не выполняется.

Admission не имеет права делать hidden scientific review.

\(N\) является behavioural anchor и **не является cost-matched control**.

---

# 40. Модели

## 40.1. Единая модель исследуемой системы \(M_S\)

Для **всех компонентов, способных повлиять на финальный допуск**, используется одна модель и одна замороженная конфигурация:

\[
M_S=\texttt{gpt-6-luna}.
\]

Она используется для:

- worker branches;
- author branch;
- primary reviewers;
- moderation;
- author revision;
- resolution;
- admission.

Техническая конфигурация:

- OpenAI direct;
- Standard processing;
- Responses API;
- `reasoning.effort=medium`;
- web/search отключены;
- разрешены только локальные экспериментальные tools;
- provider fallback запрещён.

При `medium` не задаются:

- `temperature`;
- `top_p`;
- `top_logprobs`.

Иными словами:

\[
M_R=M_G=M_S=\texttt{gpt-6-luna}.
\]

Различие между worker, reviewer, moderator, resolver и admission-agent создаётся **не моделью**, а только:

- разрешённым контекстом;
- накопленной историей;
- task decomposition;
- роль-ограничивающей инструкцией;
- местом компонента в процедуре.

## 40.2. Ограничение governance-компонентов

Использование той же Luna для governance не даёт moderator/resolver/admission права выполнять дополнительный полный научный review.

Каждая управляющая роль получает только минимальный пакет, необходимый для своей функции.

**Moderation** получает:

- карточку;
- первичные reviews;
- evidence, предъявленное в reviews.

**Resolution** получает:

- конкретное существенное возражение;
- evidence;
- ответ автора;
- относящееся изменение карточки.

**Admission** получает:

- финальную карточку;
- статусы moderation/resolution;
- формальные ограничения;
- бюджетные/процедурные логи.

Governance-компонент не может использовать непредъявленное parametric knowledge как новое основание для научного возражения.

Любое содержательное утверждение governance, влияющее на решение, должно ссылаться на evidence ID из разрешённого входа.

## 40.3. Внешний evaluator \(M_E\)

\[
M_E=\texttt{gpt-6.1-terra}.
\]

Terra запускается только **после завершения экспериментального run**.

Она получает evaluation packet (§55), но не получает права изменить:

- review;
- revision;
- resolution;
- admission;
- финальное действие.

Поэтому:

\[
M_E\notin
\text{causal path}
\left(
q_i
\rightarrow
Y_{ivcr}
\right).
\]

Terra используется как автоматизированный аналог части экспертной outcome-разметки. Человеческая calibration subset остаётся обязательной.

---

# 41. Smoke test однородного Luna-governance

Поскольку governance выполняется той же архитектурой, smoke test проверяет не «силу другой модели», а способность role/context boundary удерживать Luna в узкой управляющей функции.

На development создаётся human-labelled набор:

- 20 moderation cases:
  - 10 substantial;
  - 10 non-substantial;
- 20 resolution cases:
  - 10 resolved;
  - 10 unresolved;
- 20 admission cases:
  - 10 admissible;
  - 10 requiring block/escalation.

Минимальные условия:

\[
BA_{\mathrm{moderation}}\ge0.80,
\]

\[
BA_{\mathrm{resolution}}\ge0.80,
\]

\[
BA_{\mathrm{admission}}\ge0.80.
\]

Structured-output validity:

\[
\ge0.95.
\]

Hallucinated/invalid evidence-ID rate:

\[
\le0.05.
\]

Дополнительно измеряется **Unauthorized New Objection Rate**:

\[
UNOR
=
P(
\text{governance создаёт новое содержательное научное возражение,
которого нет в разрешённом входе}
).
\]

Требование:

\[
UNOR\le0.05.
\]

Если эти условия не выполняются, нельзя заменять governance моделью другого семейства внутри текущего core protocol. Необходимо переработать role prompt, input boundary или governance interface на development и повторить smoke test. Если bounded governance на Luna не достигается до freeze, core pilot не авторизуется.

# 42. Повторения

\[
r\in\{1,2,3\}.
\]

Это независимые API calls одной frozen configuration.

API seed не предполагается доступным.

Local seeds используются для:

- task ordering;
- defect matching;
- U-panels;
- f tie-break;
- run order;
- expert order.

Единица независимости — episode, не repetition.

---

# 43. Canary drift guard

Поскольку Luna доступна только через alias без dated snapshot, вводится canary set.

## 43.1. Canary set

На development создаются 20 fixed non-benchmark canaries:

- 8 deterministic extraction/classification;
- 8 deterministic reasoning problems с коротким проверяемым ответом;
- 4 local-tool structured tasks.

Canaries не используют SCI-AGENT-EXAM materials.

## 43.2. Запуск

Canary batch запускается:

- перед каждой крупной experimental session;
- после каждой session.

Фиксируются:

- exact/programmatic score;
- output schema validity;
- input/output/reasoning tokens;
- latency;
- model metadata.

## 43.3. Drift threshold

На D0 определяется baseline variability.

До pilot freeze фиксируются numerical drift thresholds.

При выходе за threshold:

- session приостанавливается;
- experimental outcomes этой session не анализируются до решения;
- решение принимается без сравнения \(f/U/N\) effect signs.

---

# 44. Service tier и cache

Для Luna:

- только Standard;
- не Batch;
- не Flex;
- не Fast;
- один provider;
- fallback запрещён.

Для каждого call сохраняются:

- uncached input;
- cached input;
- cache writes;
- output;
- reasoning tokens;
- actual billed cost.

## 44.1. Две стоимости

**Actual billed cost:**

\[
K^{bill}.
\]

**Normalized uncached Standard cost:**

\[
K^{norm},
\]

где все input tokens тарифицируются как uncached Standard tokens по frozen price table.

\(K^{norm}\) используется для resource-equivalence \(f\) vs \(U\).

\(K^{bill}\) используется для фактического бюджета проекта.

Это предотвращает превращение случайного cache hit в кажущееся преимущество условия.

---

# 45. Equal-resource rule для \(f\) и \(U\)

\(f\) и \(U\) имеют одинаковые:

- \(k=3\);
- reviewer context caps;
- max reviewer output;
- revision cap;
- \(M_S=\text{GPT-6 Luna}\) во всех in-system ролях;
- reasoning effort;
- normalized per-run budget.

## 45.1. Development estimates

На development измеряются:

\[
K^{norm}_{review},
K^{norm}_{moderation},
K^{norm}_{revision},
K^{norm}_{resolution},
K^{norm}_{admission}.
\]

## 45.2. Protected gate reserve

Определяется:

\[
R_{gate}
=
1.25
\times
P95
\left(
K^{norm}_{resolution}
+
K^{norm}_{admission}
\right).
\]

Если \(Z=0\), resolution component в величину фактического run не входит; reserve не перераспределяется reviewer/revision stages.

## 45.3. Per-run cap

До pilot freeze:

\[
K^{run}_{cap}
\]

задаётся на основании development distribution с заранее зарегистрированным safety multiplier.

Reviewer+moderation+revision stages не могут потребить:

\[
K^{run}_{cap}-R_{gate}.
\]

Admission reserve защищён.

Одинаковый \(K^{run}_{cap}\) применяется \(f\) и \(U\).

## 45.4. N

Для \(N\) используется отдельный меньший cap.

Следовательно:

\[
K_f-K_N
\]

и

\[
K_U-K_N
\]

не интерпретируются как efficiency comparisons.

Cost-comparison разрешён только:

\[
f \text{ vs } U.
\]

---

# 46. Один \(f/U\)-run

Индексы:

\[
(i,v,c,r).
\]

## 46.1. Review

3 независимых reviewer calls.

Формат:

> несоответствие → основание → последствие для допуска

Evidence ID обязателен для содержательного утверждения.

`no conflict found` и `insufficient context` различаются.

## 46.2. Moderation

Luna governance instance \(M_S\) устанавливает:

\[
Z_{ivcr}=1
\]

если хотя бы одно проверяемое возражение способно изменить:

- цель;
- масштаб;
- обязательные условия;
- допуск.

## 46.3. Revision

Frozen author copy получает:

- карточку;
- отзывы;
- только предъявленные evidence fragments.

Один call.

Ответ:

1. исправить;
2. опровергнуть;
3. explore;
4. escalate.

## 46.4. Resolution

Resolution вызывается **только если**:

\[
Z_{ivcr}=1.
\]

Он оценивает конкретные существенные objections и responses.

Неразрешённый конфликт:

\[
Y=\mathrm{escalate}.
\]

При \(Z=0\) отдельного resolution call нет.

## 46.5. Admission

Всегда вызывается при наличии оставшегося gate budget.

Проверяет:

- procedure completion;
- constraints;
- status objections;
- explore format;
- отсутствие скрытой полной замены задачи.

Не выполняет full scientific review.

---

# 47. Один N-run

1. frozen author state получает исходную/экспериментальную карточку;
2. один self-revision call;
3. новых evidence нет;
4. admission;
5. resolution отсутствует, так как внешних objections нет.

---

# 48. Call inventory

При полном \(N_C=30\), \(N_N=6\):

## 48.1. \(f/U\)

\[
396
\]

runs.

Базовые calls/run:

- 3 reviews;
- 1 moderation;
- 1 revision;
- 1 admission.

Итого:

\[
396\times6=2376.
\]

Дополнительные resolution calls:

\[
N_Z=\sum\mathbf1\{Z=1\},
\]

\[
0\le N_Z\le396.
\]

## 48.2. \(N\)

\[
198
\]

runs.

- 1 self-revision;
- 1 admission.

\[
198\times2=396.
\]

## 48.3. Core pipeline

\[
N_{calls}^{core}
=
2772+N_Z.
\]

Диапазон:

\[
2772\le N_{calls}^{core}\le3168.
\]

Не включены:

- worker turns;
- initial card generation;
- development calls;
- canaries.

---

# 49. Основной исход \(E\)

\[
E^{final}_{ivcr}
=
\mathbf1
\{
\text{разрешено действие со значимым дефектом}
\}.
\]

\(E=1\):

- исходный defect не исправлен;
- revision создал новый defect;
- разрешена defect exploration;
- нарушено обязательное constraint.

Для:

- `escalate`;
- `incomplete`;

формально \(E=0\), но это не считается успехом.

---

# 50. Обструкционный исход \(B\)

Для `valid`:

\[
\mathcal Y^\star_{i,valid}
\]

— human-accepted set.

\[
B_{i,valid,c,r}
=
\mathbf1
\left\{
Y_{i,valid,c,r}
=\mathrm{incomplete}
\lor
Y_{i,valid,c,r}
\notin\mathcal Y^\star_{i,valid}
\right\}.
\]

---

# 51. Совместная основная отчётность

Для каждого condition:

\[
(E_{def,c},B_{valid,c}).
\]

Также обязательно:

\[
P(execute),
P(explore),
P(escalate),
P(incomplete).
\]

Снижение \(E\) при росте блокировки не интерпретируется как однозначное улучшение.

---

# 52. Sensitivity для incomplete

Для defect:

\[
E^{miss}_{ivcr}
=
\mathbf1
\{
E^{final}_{ivcr}=1
\lor
Y_{ivcr}=\mathrm{incomplete}
\}.
\]

---

# 53. N floor criterion

Условие \(N\) должно оставлять существенный запас для внешней критики.

Стартовый gate:

\[
E_{def,N}\ge0.50.
\]

Если:

\[
E_{def,N}<0.50,
\]

self-revision/admission уже устраняет слишком большую долю defects и внешний reviewer effect может быть трудно различим.

## 53.1. Ненасыщенность \(f/U\)

Проверка только на точные \(0\) и \(1\) недостаточна: значения, близкие к границам, также оставляют слишком мало пространства для различимого эффекта.

Для episode-weighted pilot aggregates требуется:

\[
0.10
\le
E_{def,f}
\le
0.90,
\]

\[
0.10
\le
E_{def,U}
\le
0.90.
\]

Если хотя бы одно из условий выходит за диапазон:

\[
[0.10,0.90],
\]

фиксируется floor/ceiling saturation warning, и confirmatory design не замораживается без переработки корпуса, defect construction или иной заранее обоснованной части дизайна.

Этот gate является критерием **информативности пилота**, а не тестом эффективности \(f\).

---

# 54. Ошибки moderation/resolution

Отдельная component-audit team оценивает сохранённые пакеты.

## 54.1. Moderation

Пакет:

- card;
- primary reviews;
- evidence.

Размечаются:

- missed substantial objection;
- false substantial objection.

## 54.2. Resolution

Пакет:

- objection;
- evidence;
- author response;
- revision.

Размечаются:

- false resolved;
- false unresolved.

Component auditors не видят policy \(f/U\).

---

# 55. Offline outcome evaluation: Terra + human calibration

## 55.1. Принцип

Все experiment runs после завершения swarm procedure передаются внешнему evaluator:

\[
M_E=\text{GPT-6.1 Terra}.
\]

Terra не видит process traces, которые не нужны для оценки финального решения.

Evaluation packet содержит:

- замороженное досье \(\mathcal D_i^0\) в объёме, разрешённом outcome evaluator;
- исходную карточку;
- финальную карточку;
- финальное действие \(Y_{ivcr}\);
- обязательные ограничения;
- evidence/artifacts, необходимые для проверки допустимости финального действия.

Evaluation packet **не содержит**:

- название \(f/U/N\);
- reviewer identities;
- состав панели;
- ранги \(f\);
- отзывы;
- moderation transcript;
- resolution transcript;
- model identity роя;
- evidence-map;
- \(V\), \(q^I\), \(M^I\);
- defect injection metadata;
- editor identity.

Terra должна оценить конечный результат относительно frozen evidence, а не реконструировать процесс его получения.

## 55.2. Evidence-bounded evaluation

Содержательное основание Terra считается валидным только при наличии ссылки на предоставленный evidence/artifact ID.

Parametric knowledge Terra, отсутствующее в frozen evaluation packet, не может само по себе служить основанием для:

- постановки \(E=1\);
- постановки \(B=1\);
- признания действия допустимым или недопустимым.

Если evaluator считает внешнее знание необходимым, он должен вернуть `insufficient_evidence_for_evaluation`, а не заполнять пробел из parametric knowledge.

## 55.3. Condition-blindness audit

После каждой Terra-разметки evaluator дополнительно угадывает:

\[
\hat c_E\in\{f,U,N,unknown\}.
\]

Публикуются:

- overall guess accuracy;
- balanced accuracy \(f\) vs \(U\);
- доля `unknown`.

Та же диагностическая догадка собирается у human calibration raters.

Если \(f/U\) различимы существенно выше chance по evaluation packets, это считается риском measurement bias.

---

# 56. Роли и запрещённые пересечения

| Роль | Что делает | Не может одновременно быть |
|---|---|---|
| Eligibility rater | определяет task eligibility | human outcome calibrator, natural screener |
| Card editor/validator | valid/def, applicability | human outcome calibrator, evidence mapper |
| Natural screener | natural defect | human outcome calibrator |
| Evidence mapper | sufficient sets | human outcome calibrator, component auditor |
| Graph auditor | audit graph | author/editor для того же episode |
| Terra evaluator \(M_E\) | оценивает все финальные outcomes offline | не участвует ни в одном этапе swarm decision path |
| Human domain calibrator | human gold для \(E,B,\mathcal Y^\star\) на calibration subset | editor, mapper, natural screener для тех же episodes |
| Human methodology calibrator | human gold для \(E,B,\mathcal Y^\star\) на calibration subset | editor, mapper, component auditor для тех же episodes |
| Component auditor | moderation/resolution errors | evidence mapper, working governance component |
| Adjudicator | разрешает спор своей human-группы | не участвует в первичном rating данного item |

Один человек может работать в разных human-ролях только на непересекающихся task partitions, если это зарегистрировано заранее.

---

# 57. Валидация Terra как outcome evaluator

## 57.1. Terra оценивает все runs

Каждый завершённый run получает один blind Terra evaluation.

При полном наборе:

\[
N_E=594.
\]

Terra возвращает как минимум:

- \(E^{final}\);
- для `valid`: \(B\);
- допустимое множество действий \(\mathcal Y^\star\), когда оно требуется;
- uncertainty flag;
- evidence IDs;
- краткое основание;
- condition guess \(\hat c_E\).

Terra label не считается автоматически ground truth.

## 57.2. Human calibration subset

До просмотра Terra labels выбирается стратифицированная calibration subset.

При полном \(N_{\mathrm{runs}}=594\):

\[
n_{\mathrm{cal}}=120.
\]

При меньшем фактическом числе runs:

\[
n_{\mathrm{cal}}
=
\min
\left(
N_{\mathrm{runs}},
\max
\left(
120,
\left\lceil0.20N_{\mathrm{runs}}\right\rceil
\right)
\right).
\]

Выбор выполняется без использования Terra predictions и стратифицируется насколько возможно по:

- \(f/U/N\);
- `def/valid/natural`;
- defect type;
- `execute/explore/escalate/incomplete`.

Каждый calibration run независимо оценивают:

- один human domain expert;
- один human methodology expert.

При расхождении по \(E\), \(B\) или \(\mathcal Y^\star\) используется третий adjudicator.

Human raters не видят Terra output.

Adjudicated human label обозначается:

\[
Y_H.
\]

## 57.3. Human reliability

Для calibration subset публикуются:

- prevalence;
- observed agreement;
- Krippendorff \(\alpha\);
- interval uncertainty;
- adjudication rate.

Стартовое условие:

\[
\alpha_H\ge0.67.
\]

При extreme prevalence \((<0.10\) или \(>0.90)\) \(\alpha_H\) не используется как единственный показатель качества human labels.

## 57.4. Terra–human agreement

Для \(E\) вычисляются:

\[
BA_E,\qquad
Sensitivity_E,\qquad
Specificity_E.
\]

Стартовые требования:

\[
BA_E\ge0.85,
\]

\[
Sensitivity_E\ge0.85,
\]

\[
Specificity_E\ge0.85.
\]

Для \(B\) на доступной `valid` calibration subset:

\[
BA_B\ge0.85.
\]

Дополнительно публикуются:

- raw agreement;
- confusion matrices;
- agreement по condition;
- agreement по defect type;
- agreement по action type;
- disagreement с human uncertainty.

## 57.5. Решение о применении Terra labels

Если human reliability и Terra-validation gates пройдены, pilot outcome analysis может использовать Terra labels для всех runs, а human calibration labels публикуются как независимая проверка измерителя.

Если Terra не проходит gate, она **не может** использоваться как основной outcome evaluator.

В этом случае:

1. outcome-effect analysis \(f-U\) не открывается;
2. запускается полная human outcome-разметка всех оставшихся runs;
3. только после её freeze разрешается основной анализ.

Нельзя выбирать между Terra и human labels по тому, какой вариант даёт более выгодный \(f-U\).

## 57.6. Shared-family limitation

Terra и Luna относятся к одной GPT-модельной линии.

Поэтому даже прошедшая human calibration Terra не считается доказательством архитектурно независимой верификации.

Она является **validated measurement instrument**, а не независимым научным арбитром.

Основное преимущество этой схемы — сокращение человеческой outcome-нагрузки без введения другой архитектуры внутрь исследуемого swarm system.

# 58. Неоднозначность

Для любого contrast:

\[
\Delta_Q^{A-B}
=
Q_A-Q_B,
\]

где **меньше — лучше** (\(Q=E\) или \(B\)).

Консервативная для заявления «A лучше B» граница:

- ambiguous \(A\rightarrow1\);
- ambiguous \(B\rightarrow0\).

Противоположная граница:

- ambiguous \(A\rightarrow0\);
- ambiguous \(B\rightarrow1\).

Это правило одинаково применяется:

- \(f-U\);
- \(f-N\);
- \(U-N\).

\[
\tau_{amb}\le0.15.
\]

---

# 59. Основной объём

При полном наборе:

Controlled:

\[
30\times2\times3\times3=540.
\]

Natural:

\[
6\times3\times3=54.
\]

Максимум:

\[
594.
\]

Если pre-run exclusions уменьшают выборку, публикуется фактический \(N\).

Automated Terra evaluations при полном наборе:

\[
N_E=594.
\]

Базовые human calibration ratings:

\[
120\times2=240.
\]

Human adjudications добавляются только при расхождениях.

Если Terra не проходит validation gates, включается full-human fallback до:

\[
594\times2=1188
\]

первичных human ratings.

---

# 60. Анализ

Повторения усредняются внутри episode.

\[
\overline E_{ivc}
=
\frac13\sum_rE_{ivcr}.
\]

Основные descriptive contrasts на defect:

\[
\widehat\Delta_E^{f-U}
=
\frac1N
\sum_i
(
\overline E_{i,def,f}
-
\overline E_{i,def,U}
).
\]

Аналогично:

\[
\widehat\Delta_E^{f-N},
\qquad
\widehat\Delta_E^{U-N}.
\]

Для valid:

\[
\widehat\Delta_B^{f-U},
\quad
\widehat\Delta_B^{f-N},
\quad
\widehat\Delta_B^{U-N}.
\]

\(f-N\) и \(U-N\) являются **behavioural**, но не cost-matched contrasts.

---

# 61. Coverage analysis

\[
g_i
=
\overline E_{i,def,f}
-
\overline E_{i,def,U}.
\]

\[
g_i
=
\beta_0
+
\beta_1\overline M_i^I
+
\varepsilon_i.
\]

Описательно.

Causal mediation не заявляется.

---

# 62. Pilot estimands

Пилот используется для оценки:

- episode-level variance;
- repeat variance;
- ICC/cluster structure;
- \(P(Z=1)\);
- \(m_i\);
- tie-break rate;
- evidence coverage;
- truncation;
- incomplete rate;
- evaluator reliability и Terra–human agreement;
- API token distribution;
- human calibration time.

Pilot effect size не используется для выбора \(\delta_E,\delta_B\).

---

# 63. Confirmatory power planning

После pilot:

1. внешне фиксируются practically meaningful:
   \[
   \delta_E,\delta_B,\delta_K;
   \]
2. строится simulation, воспроизводящая:
   - pairing;
   - three repeats;
   - observed ICC;
   - joint \(E/B\);
   - missing/incomplete;
   - confirmatory decision rule;
3. sample size выбирается по simulation-based power.

---

# 64. Стоимость

Стоимость разделяется на **стоимость исследуемой системы** и **стоимость измерительного слоя**.

## 64.1. Swarm actual cost

\[
K^{bill}_{swarm}
\]

— фактическая стоимость provider invoice для Luna calls, входящих в worker/reviewer/governance path.

## 64.2. Swarm normalized cost

\[
K^{norm}_{swarm}
\]

— Luna Standard cost без cache discount.

Именно \(K^{norm}_{swarm}\) используется для \(f/U\) resource-equivalence.

## 64.3. Evaluator cost

\[
K_E
\]

— стоимость offline Terra evaluation.

Она **не** включается в \(K^{norm}_{swarm}\), потому что Terra не является частью исследуемой production architecture.

Для Terra Standard при prompt \(\le272K\):

\[
C_E
=
1.00\frac{T_{in}}{10^6}
+
5.00\frac{T_{out}}{10^6},
\]

с отдельным учётом caching и long-context pricing.

При полном наборе требуется до:

\[
N_E=594
\]

Terra evaluation calls.

Evaluator cost публикуется как research measurement cost.

## 64.4. Routing cost

Основной \(f\) — deterministic code над frozen graph.

Следовательно:

\[
K_{routing}^{API}=0.
\]

Логируются:

- CPU time;
- memory;
- wall time.

Graph construction — dataset-construction cost и учитывается отдельно, одинаково до условия.

Если future version использует LLM routing, это новый протокол.

# 65. Development-based budget

Нельзя использовать плоское предположение «20k input на worker turn».

На D0 и полном development измеряются эмпирические распределения токенов **по номеру хода**, потому что context растёт.

Для типа вызова \(g\):

\[
\bar K_g,\quad
P95(K_g)
\]

получаются из фактического usage.

Плановая модельная стоимость:

\[
K_{plan}
=
\sum_gn_g\bar K_g
+
E[N_Z]\bar K_{resolution}.
\]

Project API cap:

\[
K_{project}^{cap}
=
2K_{plan}.
\]

Это относится к model API и не включает human labor/sandbox.

---

# 66. Call count зависит от \(Z\)

При полном наборе:

\[
N_{f/U}=396.
\]

Fixed calls:

\[
396\times6=2376.
\]

Resolution:

\[
N_Z.
\]

N:

\[
198\times2=396.
\]

Core:

\[
N_{core}=2772+N_Z.
\]

---

# 67. Human calibration hours

При полном пилоте базовая human calibration subset:

\[
n_{\mathrm{cal}}=120.
\]

По два первичных human ratings:

\[
N_{\mathrm{human,base}}
=
2n_{\mathrm{cal}}
=
240.
\]

Если среднее время:

\[
t_{\mathrm{out}}
\]

минут, то базовая human outcome-нагрузка:

\[
H_{\mathrm{cal}}
=
\frac{
240t_{\mathrm{out}}
}{
60
}.
\]

При:

\[
t_{\mathrm{out}}=10
\]

получается:

\[
H_{\mathrm{cal}}=40
\]

экспертных часов плюс adjudication.

Это заменяет исходный дизайн с обязательными 1188 human ratings только **при условии прохождения Terra-validation gates**.

## 67.1. Worst-case fallback

Если Terra не проходит валидацию, требуется полная human outcome-разметка:

\[
N_{\mathrm{human,fallback}}
=
2N_{\mathrm{runs}}
\le1188,
\]

то есть при 10 минутах до:

\[
198
\]

первичных экспертных часов плюс adjudication.

Отдельно считаются:

- eligibility;
- editing;
- validation;
- evidence map;
- graph audit;
- component audit;
- adjudication.

Human labor входит в общий project budget.

# 68. Feasibility gates

## Pool

\[
\tau_{pool}\ge0.70.
\]

## Human calibration labels

\[
\tau_{labels}:\alpha_H\ge0.67.
\]

## Terra evaluator

Terra может заменить полную human outcome-разметку только если:

\[
BA_E\ge0.85,
\qquad
Sensitivity_E\ge0.85,
\qquad
Specificity_E\ge0.85,
\]

и на `valid` calibration subset:

\[
BA_B\ge0.85.
\]

## Ambiguity

\[
\tau_{amb}\le0.15.
\]

## Budget incomplete

\[
\tau_{budget}\le0.10.
\]

## Coverage

\[
\tau_{coverage}\ge0.40.
\]

## Tie

\[
\tau_{tie}\le0.25.
\]

## N reserve

\[
E_{def,N}\ge0.50.
\]

## f/U nonsaturation

\[
0.10\le E_{def,f}\le0.90,
\qquad
0.10\le E_{def,U}\le0.90.
\]

## Style

\[
BA_{style}\le0.60
\]

и

\[
U_{0.95}^{CP}(Accuracy_{style})<0.70.
\]

На полном наборе из 60 карточек это эквивалентно максимум 35 правильным card-level классификациям.

---

# 69. Pre-freeze D0 dry run

До окончательного freeze обязательны 3–5 задач из development; нормативно используются первые 5.

D0 должен измерить:

1. token growth по worker turn;
2. duplicate rate после corrected Jaccard;
3. фактический \(m_i\);
4. исправленный \(X_i\);
5. \(\overline M_i^I\);
6. \(E_{def,N}\) на development-injected defects;
7. \(P(Z=1)\);
8. cache behaviour;
9. Luna bounded-governance smoke-test behaviour;
10. Terra-evaluator calibration behaviour;
11. human calibration time;
12. branch verifier applicability;
13. style leakage.

Только после D0 и полного 15-task development разрешено фиксировать:

- \(T_{\max}\);
- context caps;
- \(L_h\);
- budget;
- drift thresholds;
- \(\tau_{style}\), если D0 требует более строгого предварительно обоснованного значения.

Pilot outcomes для этих решений запрещены.

---

# 70. Решение после пилота

Допустимы:

1. **continue**;
2. **narrow scope**;
3. **redesign and repeat pilot**;
4. **stop component**;
5. **stop project**.

Решение основывается на gates, а не на знаке \(f-U\).

---

# 71. Что означает провал отдельных gates

## Low coverage

Не означает, что \(f\) неэффективен.

Означает:

> корпус не создаёт необходимого распределённого информационного контраста.

## High tie-break

Означает:

> текущий \(f\) недостаточно отличается от random ranking.

## Low \(E_{def,N}\)

Означает:

> self-revision baseline уже слишком силён; reviewer headroom недостаточен.

## Saturated \(f/U\)

Означает:

> задача/модель не создаёт различимости для выбранного outcome.

---

# 72. Post-freeze запреты

После первого pilot run нельзя менять:

- \(f\);
- \(k\);
- \(T_{\max}\);
- context caps;
- Jaccard;
- defect types;
- matching;
- evidence definition;
- stratum rules;
- \(X_i\);
- \(E/B\);
- ambiguity rule;
- model/provider/service tier;
- budget cap;
- gates.

Любое изменение создаёт новый protocol version и новый data partition.

---

# 73. Freeze-манифест

До pilot должны быть заполнены:

## Sources

- SCI benchmark commit + hashes;
- OpenAI docs snapshot;
- Anthropic docs snapshot;
- contamination note ResearchClawBench.

## Data

- development IDs;
- controlled candidate order;
- natural reserve order;
- confirmatory lock list.

## Sandbox

- allowlist builder;
- image digest;
- dependency lock;
- evaluator isolation proof.

## Workers

- template bank;
- \(T_{\max}\);
- L2 \(s=3\);
- output schema.

## Graph

- schema;
- provenance rules;
- audit procedure;
- \(f\) implementation.

## Context

- \(\mathcal W^{read}\) cap;
- \(L_h\);
- 2048 graph package;
- tokenizer/version;
- truncation.

## Cards

- valid procedure;
- applicability codebook;
- matching seed;
- injectors;
- edit distance;
- \(\tau_{style}\).

## Evidence

- sufficient-set codebook;
- strata rules;
- corrected \(X_i\);
- pre-run strata exclusion.

## Models

- Luna Responses/medium/Standard for **all in-system roles**;
- frozen role/context interfaces for reviewer/moderation/resolution/admission;
- Luna bounded-governance smoke test;
- Terra Responses/medium/Standard as offline evaluator;
- Terra–human calibration thresholds;
- no fallback inside swarm causal path;
- network disabled;
- canaries.

## Resources

- \(K^{run}_{cap}\);
- \(R_{gate}\);
- project API cap;
- human budget.

## Outcomes

- \(E\);
- \(B\);
- action distribution;
- incomplete;
- ambiguity.

## Evaluation and humans

- Terra evaluation prompt/schema frozen;
- calibration subset selection rule frozen;
- named human calibration role pools;
- conflict declarations;
- prohibited overlap matrix;
- adjudication rule;
- full-human fallback procedure.

Незаполненный critical item:

> **PILOT NOT AUTHORIZED**

---

# 74. Порядок проведения

## Phase D0 — micro-development

5 tasks.

- worker generation;
- L2 circulant;
- Jaccard;
- context;
- evidence-map;
- N baseline;
- token measurement;
- Luna governance smoke tests;
- Terra evaluator dry calibration;
- human calibration timing.

## Phase D1 — full development

Остальные 10 development tasks.

Freeze всех operational decisions.

## Phase P0 — eligibility

Seeded order.

Последовательно оценивать tasks до формирования:

- controlled candidate sequence;
- natural reserve.

## Phase P1 — prospective pool

Для task:

1. build allowlisted sandbox;
2. create parent node;
3. L1;
4. L2 circulant;
5. freeze;
6. graph;
7. exclusions;
8. dedup;
9. require \(m_i\ge15\);
10. author handoffs;
11. \(q_i^0\).

## Phase P2 — controlled cards

1. valid;
2. applicability matrix;
3. seeded bipartite matching;
4. defect injection;
5. validation;
6. edit distance;
7. style test.

## Phase P3 — evidence-map

1. two blind maps;
2. adjudication;
3. strata;
4. exclude strata 4–5;
5. corrected \(X_i\);
6. coverage metrics.

## Phase P4 — natural screening

Frozen order.

Natural defect + strata 1–3.

До 6.

## Phase P5 — experimental calls

Run order globally randomized across:

- condition;
- variant;
- repeat;
- episode,

subject to rate-limit scheduling.

Запрещено запускать все \(f\), затем все \(U\), затем \(N\).

Canaries перед/после batch-session.

## Phase P6 — offline branch verification

После model runs.

Programmatic benchmark verifier → \(Q^{prog}\).

## Phase P7 — offline outcome evaluation

1. удалить condition/process metadata из evaluation packets;
2. сформировать blind Terra packets;
3. до просмотра Terra labels выбрать стратифицированную human calibration subset;
4. Terra оценивает все runs;
5. human domain + methodology experts независимо оценивают calibration subset;
6. human adjudication при расхождении;
7. рассчитать human reliability;
8. рассчитать Terra–human validation metrics;
9. если gates пройдены — freeze Terra labels как основной pilot measurement;
10. если gates не пройдены — выполнить full-human fallback до анализа \(f-U\);
11. freeze outcome labels.

## Phase P8 — component audit

Moderation/resolution packages.

## Phase P9 — analysis

1. gates;
2. actions;
3. \(E/B\);
4. contrasts;
5. ambiguity bounds;
6. coverage;
7. tie;
8. drift/canary;
9. variance/ICC;
10. swarm/evaluator costs;
11. human calibration hours;
12. decision.

---

# 75. Минимальный итоговый отчёт

Обязательно публикуются:

### Corpus

- screened;
- included;
- excluded + reasons;
- domains/types.

### Pools

- \(m_i\);
- survival L1/L2;
- dedup.

### Graph

- audit false/missed edges;
- constructed-edge sensitivity.

### Evidence

- strata;
- \(M^I\);
- corrected \(X_i\);
- truncation loss;
- coverage gate.

### Routing

- \(D\cap U\);
- tie rate;
- \(f^{-pc}\) diagnostic.

### Process

- \(Z\);
- revisions;
- accepted objections;
- moderation errors;
- resolution errors.

### Actions

для каждого \(c,v\):

- execute;
- explore;
- escalate;
- incomplete.

### Outcomes

- \(E_{def}\);
- \(E^{miss}_{def}\);
- \(B_{valid}\);
- new defects on valid.

### Contrasts

- \(f-U\);
- \(f-N\);
- \(U-N\).

Cost claim only for:

- \(f-U\).

### Measurement reliability

- human calibration prevalence;
- human observed agreement;
- human Krippendorff \(\alpha_H\);
- Terra balanced accuracy;
- Terra sensitivity/specificity;
- Terra–human confusion matrices;
- ambiguity;
- adjudication;
- Terra and human condition guessing.

### Drift

- canary before/after;
- model metadata.

### Resources

- Luna swarm actual billed API;
- Luna normalized uncached cost;
- Terra evaluation API cost;
- tokens by stage/turn/model;
- sandbox compute;
- editor hours;
- human calibration hours;
- full-human fallback hours, если fallback был активирован.

### Decision

- continue;
- narrow;
- redesign;
- stop component;
- stop project.

---

# 76. Сохраняющиеся ограничения

Даже v5 не устраняет:

1. одна и та же Luna используется во всех in-system ролях; это целевая конструкция эксперимента, но она сохраняет shared-family blind spots и не позволяет core pilot разложить различия рабочих состояний на эффект role prompt, history и evidence position;
2. artificial producer-consumer structure части L2;
3. author revision над экспериментально отредактированной карточкой;
4. requirement \(m_i\ge15\);
5. benchmark tasks имеют человеческие target solutions;
6. evidence availability не гарантирует use;
7. Terra является более сильным, но всё ещё GPT-family evaluator; human calibration снижает, но не устраняет риск общего representational bias;
8. alias Luna может drift без датированного snapshot;
9. natural defects могут оказаться редкими;
10. ResearchClawBench потенциально контаминирован относительно Luna cutoff;
11. локальный admission outcome не доказывает long-horizon trajectory improvement.

Эти ограничения должны быть перечислены в статье и не устраняются post-hoc анализом.

---

# 77. Не входит в core pilot

Отдельные будущие протоколы:

1. число reviewers \(k\);
2. **role-only control**: свежие Luna без worker history, получающие только описание рабочей позиции;
3. **history-shuffle control**: реальные histories, переставленные между позициями/зависимостями;
4. model-heterogeneity panels;
5. omniscient single reviewer;
6. альтернативный resolution;
7. full execution of admitted tasks;
8. trajectory-level outcomes;
9. confirmatory study;
10. ResearchClawBench replication + contamination analysis.

Особенно важны experiments 2–3, поскольку они позволяют отделить:

\[
\text{эффект role prompting}
\]

от:

\[
\text{эффекта реально накопленной рабочей истории}
\]

и от:

\[
\text{эффекта корректного соответствия history позиции в графе}.
\]

# 78. Краткая конфигурация

\[
\boxed{
\begin{aligned}
&M_S=\text{GPT-6 Luna, medium, Responses API, Standard};\\
&M_{\mathrm{worker}}
=M_{\mathrm{author}}
=M_{\mathrm{reviewer}}
=M_{\mathrm{governance}}
=M_S;\\
&M_E=\text{GPT-6.1 Terra, offline outcome evaluator};\\
&20\ workers/task=10L1+10L2;\\
&L2_j\leftarrow(L1_j,L1_{j+3\bmod10});\\
&m_i\ge15;\\
&k=3;\\
&c\in\{f,U,N\};\\
&r=3;\\
&|\mathcal P_i^{read}|\le2048;\\
&N_C\le30,\quad N_N\le6;\\
&N_{runs}\le594;\\
&N_{core\ swarm\ calls}=2772+N_Z;\\
&N_E\le594;\\
&n_{\mathrm{cal}}=120\ \text{при полном пилоте};\\
&\tau_{coverage}\ge0.40;\\
&\tau_{tie}\le0.25;\\
&E_{def,N}\ge0.50.
\end{aligned}
}
\]

**Главное правило интерпретации:**  
core pilot проверяет, может ли dependency-aware routing \(f\) **лучше равномерной маршрутизации использовать уже существующие различия между проспективно созданными рабочими состояниями экземпляров одной и той же архитектуры**.

Пилот **не устанавливает**, что эти различия причинно возникли именно из накопленной history, именно из task decomposition или именно из позиции в графе. В текущем дизайне role/task instruction, history, evidence access и dependency position изменяются совместно при построении пула.

Следовательно, допустимый положительный вывод core pilot имеет вид:

> при фиксированной архитектуре и данном способе построения пула dependency-aware routing использует различающиеся рабочие состояния лучше равномерного выбора.

Недопустимый вывод:

> накопленная история сама по себе создаёт эпистемическую специализацию.

Для второго утверждения требуются отдельные `role-only` и `history-shuffle` эксперименты, уже вынесенные в §77.

Terra не является частью исследуемой архитектуры и не улучшает решения роя. Она является внешним измерительным инструментом, который может заменить большую часть человеческой outcome-разметки только после успешной калибровки против human experts.

Знак \(f-U\) сам по себе не является критерием успеха.
