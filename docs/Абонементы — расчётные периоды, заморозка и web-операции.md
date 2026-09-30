# Абонементы: расчётные периоды, заморозка, сохранение места и web-операции

**Статус:** согласованные продуктовые требования и архитектурное направление перед реализацией  
**Область:** subscriptions, scheduling, group membership, manager/trainer web UI  
**Не является:** спецификацией биллинга или онлайн-оплаты

---

# 1. Цель

Система должна поддерживать несколько общешкольных моделей срока действия
абонементов, перенос ограниченного числа неиспользованных занятий на следующий
расчётный период, платное сохранение места в группе на один пропущенный
расчётный период и полноценное управление школой через web-интерфейс.

CLI остаётся техническим интерфейсом для системного администратора,
автоматизации и аварийных операций. Менеджер школы и тренер не должны
использовать management commands как основной способ работы.

---

# 2. Термины

## 2.1. Расчётный период

Расчётный период — интервал, в котором действует конкретный Subscription и
его allowance-лимиты.

Поддерживаются три модели расчётного периода:

1. календарный месяц;
2. 28 дней с даты первого фактически посещённого занятия;
3. 28 дней с фиксированной общей датой начала.

Модель срока действия является свойством плана/выданного Subscription и должна
быть зафиксирована snapshot-данными так, чтобы изменение тарифа в будущем не
пересчитывало историю.

## 2.2. Заморозка и компенсация пропуска

Термин «заморозка» используется школой для одного из вариантов компенсации
**конкретно пропущенного занятия**, а не для произвольного переноса всего
неиспользованного остатка Subscription.

На текущем этапе правила услуги ещё формируются. Поэтому доменная модель не
должна жёстко связывать «заморозку» с единственным вариантом:

```text
unused balance → next period
```

Вместо этого система должна уметь представить компенсацию конкретного
`Attendance=ABSENT` / пропущенного Lesson с различными условиями:

- бесплатная отработка в текущем расчётном периоде;
- бесплатная отработка в другом разрешённом периоде;
- платная отработка/перенос в последующий период;
- перерасчёт стоимости;
- сочетание перерасчёта и бесплатной отработки, если политика школы это
  допускает;
- отсутствие права на компенсацию.

Исходный Subscription, Attendance и ledger не переписываются задним числом.

## 2.3. Сохранение места в группе

Сохранение места — отдельная платная услуга, позволяющая ученику пропустить
один расчётный период и при этом сохранить место в TrainingGroup.

Сохранение места:

- не является Subscription;
- не добавляет ICE/HALL посещения;
- не создаёт AttendanceCoverage;
- не считается заморозкой остатка;
- не должно автоматически расходовать или восстанавливать allowance.

---

# 3. Поддерживаемые модели абонемента

## 3.1. Календарный месяц

Пример:

```text
period policy: CALENDAR_MONTH
valid_from:  2026-10-01
valid_until: 2026-10-31
```

Для всех учеников, купивших соответствующий расчётный период, границы
совпадают с календарным месяцем.

Subscription создаётся сразу с определёнными `valid_from` и `valid_until`.

## 3.2. 28 дней с первого занятия

Пример:

```text
period policy: FIRST_ATTENDANCE_28_DAYS
subscription issued: 2026-10-01
first covered attendance: 2026-10-07

valid_from:  2026-10-07
valid_until: 2026-11-03
```

28 дней считаются включительно: день первого занятия — день 1,
`valid_until = valid_from + 27 days`.

До первого фактического посещения Subscription находится в состоянии ожидания
активации. Его расчётные даты ещё не должны считаться начавшимися.

Активация должна происходить атомарно вместе с назначением покрытия первого
`Attendance=PRESENT`, чтобы конкурентные отметки не могли назначить две разные
даты начала.

RSVP, публикация Lesson и отсутствие ученика не активируют такой Subscription.

## 3.3. 28 дней с фиксированной общей датой начала

Пример:

```text
period policy: FIXED_28_DAYS

school period:
2026-10-05 .. 2026-11-01
```

Все ученики, относящиеся к этому расчётному периоду, получают одинаковые
`valid_from` и `valid_until`.

Дата не должна вводиться независимо в каждом Subscription. Для этой модели
нужна общая сущность/запись расчётного периода, которую выбирает менеджер при
выдаче абонемента. Это предотвращает расхождение дат между учениками.

---

# 4. Архитектурное направление для периода

Текущий `SubscriptionPlan.validity_months=1` недостаточен и должен быть
заменён явной политикой периода.

Предлагаемое направление:

```text
SubscriptionPlan
    period_policy:
        CALENDAR_MONTH
        FIRST_ATTENDANCE_28_DAYS
        FIXED_28_DAYS
```

Для общих фиксированных периодов вводится отдельная сущность, рабочее имя:

```text
SubscriptionPeriod
    id
    policy
    starts_on
    ends_on
    label
```

`Subscription.period` обязателен для `FIXED_28_DAYS` и может использоваться
для календарных периодов как read/admin convenience.

Для `FIRST_ATTENDANCE_28_DAYS` Subscription может быть создан до активации.
Следовательно, текущий инвариант обязательных `valid_from/valid_until`
потребует изменения. После активации даты становятся неизменяемым snapshot.

Точная схема полей и constraints фиксируется перед миграцией, но следующие
инварианты обязательны:

- исторические даты периода не пересчитываются;
- один Subscription использует ровно одну period policy;
- `FIXED_28_DAYS` не допускает индивидуально отличающиеся даты внутри одного
  общего периода;
- `FIRST_ATTENDANCE_28_DAYS` активируется только один раз;
- активация и первое списание выполняются в одной транзакции.

---

# 5. Компенсация пропусков и заморозка

## 5.1. Текущая рабочая политика школы

Следующие правила считаются **предварительными** и должны храниться как
изменяемая/versioned policy, а не как неизменяемые константы приложения.

### Болезнь со справкой

При подтверждённом медицинском пропуске школа может предоставить:

```text
перерасчёт
и/или
бесплатную отработку
```

Точный набор вариантов и сочетаний ещё уточняется.

### Пропуск без уважительной причины

Для такого пропуска сейчас предполагаются два варианта:

```text
бесплатная отработка в текущем расчётном периоде
или
платная заморозка/отработка в последующем периоде
```

Платная заморозка по текущему правилу оплачивается одновременно с
Subscription следующего периода.

### Лимит

При пропуске более четырёх занятий без уважительной причины:

```text
1..4-й пропуск → может дать право на отработку
5-й и последующие → права на отработку не дают
```

Лимит должен быть policy-параметром, а не числом, зашитым в service layer.

### Сезонные исключения

Текущий известный пример:

```text
пропуски мая
    → можно отработать в июне
    → покупка июньского Subscription не обязательна

пропуски июня
    → можно отработать в августе
    → требуется оплаченный Subscription августа
```

Эти правила нельзя кодировать как специальные проверки `month == 5` или
`month == 6`. Они должны представляться как versioned policy/exception с
явным source period, target period и требованиями к оплате/Subscription.

## 5.2. Отсутствие как источник компенсации

Компенсация должна иметь трассируемый источник:

```text
Student
  + source Lesson
  + Attendance=ABSENT
  + причина/основание пропуска
  + source SubscriptionAllowance (если применимо)
        ↓
compensation case
        ↓
0..N разрешённых действий/прав
```

Это важнее, чем перенос абстрактного остатка allowance: система должна уметь
объяснить, **какое именно пропущенное занятие** породило право на отработку или
перерасчёт.

Для существующего medical flow источником причины остаётся
`AbsenceJustification`.

## 5.3. Архитектурное направление

Предпочтительное направление — ввести обобщённый concept, рабочее имя:

```text
AbsenceCompensationCase
```

Он фиксирует:

```text
student
source_lesson
attendance
absence_reason
source_subscription_allowance (optional)
policy_version / policy_snapshot
status
created_at
resolved_at
```

Из одного case политика может разрешить один или несколько результатов:

```text
MAKEUP entitlement
PAID_FREEZE / deferred makeup entitlement
BILLING recalculation reference
NO_COMPENSATION
```

Это позволяет не смешивать финансовый перерасчёт с entitlement accounting и
одновременно поддержать формулировку «перерасчёт и/или бесплатная отработка».

Текущий `MakeupEntitlement` может быть либо расширен, либо позднее
мигрирован в более общий механизм. Конкретное решение принимается перед
реализацией миграций; на этом этапе важно сохранить существующий medical flow.

## 5.4. Policy должна описывать условия, а не сценарий в коде

Минимально политика компенсации должна уметь выразить:

```text
reason / justification requirement
maximum eligible missed lessons
compensation kind
target-period rule
fee requirement
target Subscription requirement
validity window
seasonal exception
effective_from / effective_until
priority
```

Полезные значения target-period rule:

```text
CURRENT_PERIOD
NEXT_STUDENT_PERIOD
EXPLICIT_TARGET_PERIOD
```

Полезные значения requirements:

```text
NO_FEE
FEE_REQUIRED
TARGET_SUBSCRIPTION_REQUIRED
FEE_AND_TARGET_SUBSCRIPTION_REQUIRED
```

Это не означает необходимость строить универсальный rule engine. Можно
реализовать ограниченный набор типизированных policy-полей и стратегий, но
правила школы не должны требовать миграции БД и изменения Python-кода при
каждом изменении лимита или сезонного окна.

## 5.5. Лимит количества пропусков

Лимит должен считаться в определённом policy scope. Для уже реализованного
case-layer расчётный период идентифицируется по исходному Subscription, а не
только по совпадающему диапазону дат. Это важно при перекрывающихся
календарных/28-дневных абонементах.

До материализации компенсационного права eligibility OPEN case может
пересчитываться при появлении более раннего пропуска или отмене другого case.

**Граница ретроактивности:** как только из case фактически выдано первое право
на отработку, платную заморозку или финансовый перерасчёт, использованная
eligibility должна быть атомарно зафиксирована вместе с этим действием.
Последующие backdated case не должны автоматически отзывать или переписывать
уже предоставленное право.

Для изменения самого основания пропуска explicit reversal уже является частью
lifecycle: при ABSENT→PRESENT или отзыве связанной VERIFIED medical справки
неиспользованный compensation makeup отменяется, grant и case переходят в
REVERSED. Если makeup уже использован активным AttendanceCoverage, исправление
основания блокируется до reverse/rebind этого coverage.

До миграции старого medical-flow на case запрещена двойная компенсация одного
и того же пропуска одновременно через MEDICAL_VERIFIED и
ABSENCE_COMPENSATION.

Инвариант действует в обе стороны: если сначала выдана compensation-отработка,
а затем подтверждается медицинская справка, неиспользованное compensation-право
автоматически переводится в REVERSED с причиной `superseded_by_medical`, после
чего выдаётся medical makeup. Если compensation-право уже использовано активным
AttendanceCoverage, верификация справки блокируется до reverse/rebind coverage.

На уровне БД действует partial UniqueConstraint: для одной пары
`student + source_lesson` одновременно может существовать не более одного
активного makeup с причиной MEDICAL_VERIFIED или ABSENCE_COMPENSATION.

Для ошибочно выданного права существует явный административный application
service `reverse_absence_compensation_case(...)`. Он использует тот же
reversal lifecycle и не обходит проверку уже использованного makeup.

Если Attendance исправлен из ABSENT в PRESENT либо отозвано VERIFIED medical
основание, OPEN case до материализации автоматически отменяется и лимит
пересчитывается.

Лимит должен считаться в определённом policy scope, например:

```text
student + расчётный период + absence_reason
```

Для текущего правила:

```text
UNEXCUSED
max_eligible_absences = 4
```

Пятый и последующие пропуски остаются исторически видимыми, но получают
результат `NO_COMPENSATION`.

Если школа позднее изменит лимит, уже обработанные периоды не должны
пересчитываться автоматически: case хранит policy version/snapshot.

## 5.6. Бесплатная отработка в текущем периоде

Для неуважительного пропуска система может создать entitlement, допустимый
только до конца текущего расчётного периода.

Если такое право использовано, тот же пропуск не должен затем автоматически
породить ещё одну платную отработку следующего периода, если policy явно не
разрешает несколько результатов.

Идемпотентность и защита от двойной компенсации обязательны.

## 5.7. Платная заморозка

Платная заморозка реализована как двухфазный PAID_MAKEUP grant для конкретного
допустимого пропуска.

```text
OPEN + ELIGIBLE case
        ↓ authorize
MATERIALIZED case + pending PAID_MAKEUP grant
        ↓
оплата подтверждена
и, если требует policy, выбран target Subscription
        ↓ activate
MakeupEntitlement становится usable
```

Authorization уже замораживает eligibility и резервирует место в лимите, но
до activation не создаёт MakeupEntitlement. Поэтому поздний backdated case не
отзывает принятое решение, а неоплаченная/неактивированная заморозка ещё не
может использоваться для AttendanceCoverage.

Поддерживаются требования:

```text
FEE_REQUIRED
FEE_AND_TARGET_SUBSCRIPTION_REQUIRED
```

PAID_MAKEUP без требования оплаты считается ошибкой конфигурации.

Для CURRENT_PERIOD используется период source Subscription.
Для EXPLICIT_TARGET_WINDOW используется сохранённое seasonal window; если
нужен target Subscription, usable window ограничивается пересечением его дат
с seasonal window.

Для NEXT_STUDENT_PERIOD до появления общего period resolver менеджер обязан
выбрать target Subscription **до authorization**. Пока следующего абонемента
нет, case остаётся OPEN и не занимает лимит как MATERIALIZED paid grant.
Target Subscription должен принадлежать тому же ученику, содержать нужную
ICE/HALL category, не быть отменён и начинаться после окончания source
Subscription. Entitlement получает его `valid_from/valid_until`.

Пока полноценного Billing нет, оплату подтверждает менеджер через
`confirm_paid_makeup_fee(...)`. В будущем Billing должен заменить это
подтверждение, не меняя grant/entitlement semantics.

После подтверждения оплаты никакой автоматический процесс не может молча
отменить PAID_MAKEUP. Исправление Attendance, medical supersession/revocation
и другие автоматические invalidation-paths должны остановиться с
`ValidationError` и потребовать явный
`reverse_absence_compensation_case(...)`. Ошибка содержит отдельный ключ
`manager_action_required`, чтобы будущий интерфейс тренера показывал
«требуется решение менеджера», а не обычную техническую ошибку.

Для оплаченного grant ручной reversal обязан явно зафиксировать:
`refund_required=True` или `False`. Решение сохраняется в grant и audit.
При `True` дополнительно создаётся событие `PaidFreezeRefundRequired`.
Selector `get_reversed_paid_makeups(...)` даёт отчёт «оплачено, но
отменено», включая фильтр по необходимости возврата.

Неоплаченная и неактивированная authorization не занимает слот лимита
бессрочно. `process_subscription_lifecycle(...)` автоматически переводит её
в REVERSED с причиной `authorization_expired` после deadline:

```text
CURRENT_PERIOD          → source Subscription.valid_until
NEXT_STUDENT_PERIOD     → target Subscription.valid_until
EXPLICIT_TARGET_WINDOW  → target_until
```

Поэтому заморозка «на следующий период» не истекает в первый день этого
периода: неоплаченная authorization остаётся действующей до конца выбранного
target Subscription. Уже оплаченная pending authorization автоматически не
истекает. При lifecycle-проверке состояние оплаты повторно проверяется после
получения row locks; если оплата успела подтвердиться конкурентно, grant тихо
пропускается и весь lifecycle batch продолжает работу.

Target Subscription валидируется уже при authorization, а не только при
activation. Защита source/target Subscription действует только пока зависимое
право ещё операционно значимо. Pending paid grant блокирует cancellation.
После activation cancellation блокирует лишь makeup, который ещё можно
использовать: он не отменён, не истёк на дату cancellation и не покрыт active
AttendanceCoverage. Использованный или истёкший makeup остаётся в истории, но
не делает исходный абонемент «неотменяемым навсегда».

Если target Subscription выбран уже при authorization, activation не может
молча заменить его другим. Пока unreversed PAID_MAKEUP ссылается на target
Subscription, штатная отмена этого Subscription блокируется; сначала менеджер
должен сделать explicit reversal paid-grant.

Pending или activated PAID_MAKEUP использует тот же explicit reversal
workflow, что FREE_MAKEUP. Уже использованное entitlement нельзя отменить,
пока соответствующий AttendanceCoverage не reverse/rebind.

## 5.8. Сезонные переходы между периодами

Policy должна поддерживать не только «следующий период», но и явный target.

Для действия `EXPLICIT_TARGET_WINDOW` отсутствие подходящего активного окна
означает отсутствие такого действия для данного пропуска; право с пустым
target period создаваться не должно.


Пример текущего правила:

```text
MAY period → JUNE period
require_target_subscription = false

JUNE period → AUGUST period
require_target_subscription = true
```

Июль в данном примере просто не является target period. Это свойство policy,
а не особый статус месяца в коде.

Для моделей периода, не совпадающих с календарными месяцами, применяется та
же идея: source/target задаются через расчётные периоды или strategy resolver,
а не через номера месяцев.

## 5.9. Ledger semantics

Компенсация не должна:

- менять задним числом `valid_until` исходного Subscription;
- удалять `CONSUME`/Attendance history;
- увеличивать исходный `visit_limit_snapshot`;
- создавать необъяснимый `ADJUSTMENT`.

Makeup/deferred entitlement хранит ссылку на источник и используется
`AttendanceCoverage` явно.

Перерасчёт стоимости относится к будущему Billing и должен ссылаться на тот же
compensation case/correlation ID, не изменяя entitlement ledger.

## 5.10. Смешанные ICE/HALL планы

Компенсация наследует category **конкретного пропущенного Lesson**:

```text
ICE absence  → ICE compensation
HALL absence → HALL compensation
```

Поэтому лимит «не более N пропусков» может в будущем иметь один из scopes:

```text
на весь Subscription
по каждой category отдельно
по конкретному типу занятия
```

Текущие вводные этого не определяют. Схема должна позволять выбрать scope
policy без перепроектирования entitlement model.

---

# 6. Сохранение места в группе

Для платного пропуска одного расчётного периода вводится отдельная доменная
сущность, рабочее имя:

```text
GroupPlaceHold
```

Минимально она должна связывать:

```text
student
training_group
billing/subscription period
status
created_at / created_by
cancelled_at / cancelled_by
```

Назначение:

- ученик не посещает обычные занятия этого периода по абонементу;
- место в группе считается зарезервированным;
- историческая GroupMembership не удаляется;
- менеджер видит, почему ученик остаётся закреплён за группой без активного
  Subscription.

`GroupPlaceHold` не должен участвовать в `FindCoverage`.

В MVP факт оплаты может подтверждаться административной операцией без хранения
платёжных реквизитов. Полноценный платёж относится к будущему Billing.

Открытый бизнес-вопрос перед реализацией: допускаются ли два последовательных
периода сохранения места или услуга ограничена одним периодом подряд.

---

# 7. Web-first operational policy

## 7.1. Общий принцип

Все действия, которые выполняет менеджер школы или тренер в обычном рабочем
процессе, должны быть доступны через аутентифицированный web-интерфейс.

Наличие Django management command не считается пользовательским интерфейсом.

CLI может использоваться:

- системным администратором;
- cron/systemd/CI;
- для диагностики;
- для аварийной ручной операции.

## 7.2. Менеджер школы

Через web должны быть доступны как минимум:

- управление участниками и доступами;
- группы и GroupMembership;
- создание/версионирование ScheduleTemplate;
- просмотр generation conflicts;
- `skip_template_occurrence`;
- отмена и перенос Lesson;
- публикация/подтверждение занятий, когда это ручная операция;
- тарифы и allowances;
- выдача/отмена Subscription;
- выбор модели расчётного периода;
- управление общими fixed 28-day periods;
- заморозка/carry-over;
- сохранение места в группе;
- one-time entitlements;
- административные makeup;
- проверка/отклонение/отзыв медицинского основания;
- отчёты uncovered/expired/unused;
- просмотр audit trail релевантной сущности.

## 7.3. Тренер

Через web тренер должен выполнять весь свой обычный workflow:

- расписание;
- просмотр roster и RSVP;
- Attendance PRESENT/ABSENT;
- массовые attendance actions;
- исправление Attendance в разрешённых состояниях;
- закрытие ведомости.

Тренер не должен использовать Django Admin или CLI для штатной работы.

## 7.4. Automation-only операции

Чисто фоновые операции могут оставаться management commands/cron:

- генерация будущих Lesson;
- плановая публикация;
- lifecycle processing;
- технические maintenance jobs.

При этом ошибки и конфликты таких процессов должны быть видимы менеджеру в web
и иметь понятное действие для разрешения.

---

# 8. Web UI для schedule generation conflict

`skip_template_occurrence` должен иметь manager-facing web action.

Минимальный сценарий:

```text
Конфликт расписания

Регулярное занятие:
HALL · 29.10 · 19:30

Конфликтует с:
ICE · 29.10 · 19:00

[ Открыть конфликтующее занятие ]
[ Пропустить это регулярное занятие ]
```

Перед skip требуется подтверждение.

UI вызывает тот же application service, что и management command.

Полная идемпотентность должна сохраняться и для web, и для CLI:
если template-owned CANCELLED occurrence уже существует, повторный вызов
возвращает его даже если дата к этому моменту стала прошлой.

---

# 9. Audit requirements

Новые операции должны иметь audit events.

Минимальный набор:

```text
SubscriptionActivated

AbsenceCompensationCaseCreated
AbsenceCompensationEvaluated
AbsenceCompensationDenied
MakeupEntitlementGranted
MakeupEntitlementUsed
PaidFreezeAuthorized
PaidFreezeCancelled
BillingRecalculationRequested

GroupPlaceHoldCreated
GroupPlaceHoldCancelled
GroupPlaceHoldExpired
```

Конкретные event names могут быть уточнены при реализации, но audit должен
позволять пройти от пропущенного Lesson до выданного права, его использования
и, при наличии, финансового перерасчёта.

События, относящиеся к одной бизнес-операции, используют общий
`correlation_id`, как уже сделано для template occurrence skip.

---

# 10. Что не следует смешивать

Следующие механизмы различны:

```text
medical absence compensation
unexcused absence compensation
school reschedule makeup
administrative makeup
paid freeze / deferred makeup
billing recalculation
group place hold
```

Они могут использовать общий compensation case и entitlement primitives, но
имеют разные основания, требования к оплате, target periods и audit semantics.

Платная заморозка не должна выглядеть как произвольное увеличение остатка
Subscription, а сохранение места в группе не должно создавать посещения или
subscription balance.

---

# 11. Порядок реализации

Рекомендуемая последовательность новой feature-ветки:

1. довести `skip_template_occurrence` до полной идемпотентности;
2. добавить manager web UI для generation conflicts и skip;
3. заменить `validity_months=1` на period policy и добавить period snapshots;
4. реализовать три модели расчётного периода;
5. реализовать versioned absence-compensation policy и compensation cases;
6. адаптировать medical makeup к общему механизму без потери текущей истории;
7. реализовать бесплатную/платную отработку и seasonal target-period rules;
8. реализовать `GroupPlaceHold`;
9. добавить manager web UI для subscriptions/periods/compensation/place hold;
10. обновить selectors/reports;
11. добавить migrations, domain events и regression/concurrency tests;
12. оставить CLI как secondary/sysadmin interface поверх тех же services.

Каждый этап должен сохранять правило:

> web, CLI и automation вызывают один и тот же application-service layer.
