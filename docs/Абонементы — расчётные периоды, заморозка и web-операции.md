# Абонементы: расчётные периоды, заморозка, сохранение места и web-операции

**Статус:** period foundation, GroupPlaceHold, manager web/reporting и фактическая capacity/membership semantics реализованы  
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

# 4. Реализованная модель расчётного периода

Введена общешкольная конфигурация:

```text
SubscriptionPeriodScheme
    code
    name
    mode:
        CALENDAR_MONTH
        ROLLING_28_FROM_FIRST_LESSON
        FIXED_28_DAYS
    fixed_anchor_date
    is_active
```

`SubscriptionPlan.period_scheme` связывает тариф с моделью периода.
Поле пока nullable для совместимости с историческими тарифами; новые
manager-driven выдачи требуют активный period scheme.

Для каждой переведённой на новую модель выдачи создаётся:

```text
SubscriptionPeriod
    subscription (1:1)
    scheme
    mode_snapshot
    fixed_anchor_snapshot
    state: PENDING | ACTIVE
    starts_on
    ends_on
    activation_lesson
    activated_at
```

Правила:

- `CALENDAR_MONTH` сразу создаётся ACTIVE с границами календарного месяца;
- `FIXED_28_DAYS` сразу создаётся ACTIVE по общей 28-дневной сетке от
  `fixed_anchor_date`;
- `ROLLING_28_FROM_FIRST_LESSON` создаётся PENDING без искусственных
  `valid_from/valid_until`.

Для rolling-модели `Subscription.valid_from/valid_until` допускают NULL до
первого обычного покрытия. При первом `Attendance=PRESENT`, дошедшем до
обычного SubscriptionAllowance после one-time и makeup приоритетов, в одной
транзакции:

```text
PENDING SubscriptionPeriod
    → starts_on = lesson_date
    → ends_on = lesson_date + 27 days
    → ACTIVE

Subscription
    → valid_from / valid_until = те же даты

AttendanceCoverage
    → CONSUME -1
```

Такое посещение становится одновременно точкой активации и первым расходом.
RSVP, ABSENT и само наличие Lesson период не активируют.

Для rolling-периода сохраняется `reference_date` выдачи. Она является нижней
границей активации: занятие с `lesson_date < reference_date` не может
активировать абонемент. Это защищает от позднего исправления старых ведомостей.

Кроме того, pending rolling Subscription активируется только после того, как
обычное покрытие уже не нашло действующий allowance той же категории. Поэтому
заранее купленный следующий абонемент не начинает свои 28 дней, пока текущее
занятие может быть покрыто уже действующим абонементом.

Если coverage, который активировал rolling-период, позднее reverse из-за
исправления Attendance или административной операции, активация может быть
откачена. Откат выполняется только если после reversal у этого Subscription
не осталось других active AttendanceCoverage **и** записей, чья семантика уже
зависит от дат активированного периода. К таким зависимостям относятся:

- active MakeupEntitlement, использующий allowance этого Subscription как источник;
- OPEN/MATERIALIZED AbsenceCompensationCase с source allowance этого Subscription;
- unreversed AbsenceCompensationActionGrant, связанный с таким case.

Только при отсутствии всех этих зависимостей:

```text
SubscriptionPeriod ACTIVE
    → PENDING
    → starts_on / ends_on = NULL
    → activation_lesson / activated_at = NULL

Subscription
    → valid_from / valid_until = NULL
```

Создаётся audit-событие `SubscriptionPeriodActivationReverted`. Если после
активации уже существует другое active coverage этого Subscription, период
не сдвигается и не сбрасывается; записывается
`SubscriptionPeriodActivationRevertSkipped` с причиной
`active_coverages_remain`. Если откат блокируют зависимые права/case/grant,
используется причина `dependent_rights_exist`, а payload содержит флаги
конкретных типов зависимостей. Это позволяет manager UI объяснить, почему
исправление Attendance не вернуло rolling Subscription в PENDING.

Для уменьшения риска взаимной блокировки activation и reversal используют
совместимый порядок блокировок критических сущностей: SubscriptionPeriod
блокируется до SubscriptionAllowance.

Service API:

```python
resolve_subscription_period_window(...)
issue_subscription_for_period(...)
attach_subscription_period(...)
activate_rolling_subscription_period(...)
```

Исторические даты и mode/anchor snapshots после создания периода не должны
пересчитываться из-за последующих изменений тарифа или scheme.

## 4.1. Реализованный GroupPlaceHold

Платное сохранение места представлено отдельной сущностью и не является
Subscription, ICE/HALL allowance или AttendanceCoverage.

```text
GroupPlaceHold
    student
    group
    period_scheme
    period_from / period_until
    status:
        PENDING_PAYMENT
        ACTIVE
        RESTORED
        CANCELLED
        EXPIRED
    seat_reservation
    suspended_membership
    restored_membership
    fee confirmation metadata
    restore metadata
    cancellation metadata
```

Hold обязан покрывать ровно один полный расчётный период выбранного scheme.
Дата возврата определяется как:

```text
return_on = period_until + 1 day
```

До подтверждения оплаты `PENDING_PAYMENT` не занимает место и не меняет
`GroupMembership`.

### Вместимость группы

`TrainingGroup.capacity` задаёт hard cap группы. Для исторических групп поле
может быть `NULL`: тогда система показывает occupancy, но не блокирует
зачисление по лимиту.

Единое определение занятого места для read/write path:

```text
occupied students =
    GroupMembership активных Student
    UNION
    non-cancelled GroupSeatReservation
```

Union выполняется по Student. Если в дату возврата одновременно существуют
reservation и новый membership одного ученика, они занимают **одно** место.

Membership деактивированного Student не занимает capacity: деактивация ученика
сохраняет исторические membership как записи, но снимает их текущую/будущую
seat-семантику. Явная оплаченная GroupSeatReservation остаётся отдельным
обязательством школы и продолжает занимать место до cancel/expiry даже для
inactive Student.

Все операции, способные изменить occupancy, сериализуются блокировкой строки
`TrainingGroup`. Это относится к обычному admission/update membership,
активации hold, restore и изменению hard cap. Два конкурентных запроса не могут
одновременно занять последнее свободное место.

### Активация hold

Создать hold можно только для ученика, у которого membership той же группы
активен на `period_from` и начался до даты hold.

Подтвердить оплату можно только пока `period_from > school_today`. Hold нельзя
активировать задним числом и нельзя начинать в текущий календарный день:
дневная гранулярность не позволяет безопасно отделить уже прошедшие занятия от
будущих в том же дне.

Независимо от этой проверки roster entry занятия, для которого уже существует
Attendance, никогда не деактивируется hold-механизмом. Attendance и возможность
последующей коррекции тренером имеют приоритет над изменением membership.

При `confirm_group_place_hold_fee(...)` в одной транзакции:

```text
старый GroupMembership
    ends_on = period_from - 1 day

GroupSeatReservation
    starts_on = period_from
    ends_on   = return_on

GroupPlaceHold
    PENDING_PAYMENT -> ACTIVE
    suspended_membership = старый membership
    suspended_membership_ends_on_snapshot = исходный ends_on
    seat_reservation = reservation
```

Именно reservation, а не закрытый membership, удерживает место во время
отсутствия и на защищённую дату возврата.

Если будущие Lessons уже опубликованы, GROUP-derived roster rows начиная с
`period_from` деактивируются. Явные активные `LessonEnrollment` не
подавляются: административно добавленный ученик может участвовать в конкретном
Lesson даже во время hold.

### Возврат ученика

Обычный `create_group_membership(...)` не может создать membership,
пересекающий собственную действующую reservation ученика. Возврат должен идти
через `restore_group_place_hold(...)`.

Restore создаёт новый membership:

```text
new GroupMembership.starts_on = return_on
new GroupMembership.ends_on   = исходный ends_on
                                или NULL для исходно бессрочного membership

GroupPlaceHold
    ACTIVE -> RESTORED
    restored_membership = new membership
    restored_at / restored_by
```

Capacity-check допускает этот переход даже когда группа формально заполнена:
reservation уже принадлежит тому же Student, поэтому reservation + membership
дедуплицируются в одно занятое место. При этом другой ученик получить это место
не может.

Если исходный membership имел конечный `ends_on`, эта дата сохраняется в
`suspended_membership_ends_on_snapshot` и переносится в восстановленный
membership. Restore не превращает сезонное/срочное членство в бессрочное. Если
исходный срок закончился раньше `return_on`, автоматический restore
отклоняется.

Если Lessons на дату возврата и позже уже опубликованы, GROUP-derived roster
для нового membership материализуется/восстанавливается сразу. Уроки периода
hold остаются исключёнными.

### Отмена и истечение

Отмена `PENDING_PAYMENT` просто отменяет услугу.

Отмена `ACTIVE` hold отменяет seat reservation и **не восстанавливает**
membership автоматически. Место становится свободным.

ACTIVE hold защищает место также на `return_on`. Если restore не выполнен,
`process_subscription_lifecycle(...)` переводит hold в `EXPIRED` только
после завершения этой защищённой даты, отменяет reservation и освобождает место.
EXPIRED hold не создаёт membership автоматически.

RESTORED hold считается использованным: отменить его обычным cancel-flow нельзя.

Деактивация `TrainingGroup` запрещена, пока существует non-cancelled текущая
или будущая `GroupSeatReservation`.

Для одного ученика и группы одновременно запрещены пересекающиеся
non-CANCELLED hold. CANCELLED запись остаётся историей и не мешает создать
новый hold на тот же период.

Основные services:

```python
create_group_place_hold(...)
confirm_group_place_hold_fee(...)
restore_group_place_hold(...)
cancel_group_place_hold(...)
process_subscription_lifecycle(...)
```


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
ICE/HALL category и не быть отменён.

Для pending rolling target допускается отсутствие `valid_from/valid_until`:
authorization проверяет его сохранённую `billing_period.reference_date`,
которая должна быть позже окончания source Subscription. Сам PAID_MAKEUP
entitlement до активации target rolling period не создаётся. После первого
обычного занятия target Subscription получает реальные даты, и paid grant
можно активировать с этими `valid_from/valid_until`.

Для уже активного target Subscription по-прежнему требуется начало после
окончания source Subscription.

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
                          или, для PENDING rolling target,
                          конец 28-дневного окна от billing_period.reference_date
EXPLICIT_TARGET_WINDOW  → target_until
```

Для pending rolling target deadline вычисляется детерминированно до его
фактической активации: `reference_date` считается возможным первым днём
28-дневного окна, поэтому deadline равен его 28-му дню
(`reference_date + 27 days`). Это не позволяет неоплаченной authorization
бессрочно занимать eligibility slot и блокировать отмену связанных
Subscription, даже если ученик так и не пришёл на первое занятие.

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


## 8.4. Производительность отчёта менеджера

Manager subscription report является read-model и не должен выполнять запросы
на каждый Subscription/Allowance отдельно.

Текущая реализация пакетно загружает:

- Subscription + billing period;
- allowances с агрегированным ledger balance;
- active AttendanceCoverage;
- MakeupEntitlement с признаком использования;
- GroupPlaceHold по ученикам.

Число запросов остаётся ограниченным при росте количества строк отчёта.
Диапазон отчёта через web ограничен 366 днями, как и пользовательский schedule.

Pending Subscription с `valid_from = NULL` сортируются явно первыми через
`NULLS FIRST`, чтобы порядок не зависел от PostgreSQL/SQLite.


# 12. Реализованный manager operations UI

Manager-facing операции собраны вокруг единой точки входа:

```text
/manager/operations/
```

После входа пользователь с manager-доступом попадает на этот dashboard вместо
частного subscription report. Presentation layer не содержит отдельной
доменной логики: POST actions вызывают те же application services, что CLI,
automation и ранее реализованные web endpoints.

## 12.1. Schedule templates и generation conflicts

Доступны:

```text
/manager/scheduling/templates/
/manager/scheduling/conflicts/
/manager/scheduling/lessons/
```

Web поддерживает:

- создание ScheduleTemplate через `create_schedule_template(...)`;
- versioning через `version_schedule_template(...)`;
- просмотр `LessonGenerationConflict` из audit;
- переход к конфликтующему Lesson;
- explicit `skip_template_occurrence(...)` с подтверждением;
- просмотр Lesson;
- publish / confirm / cancel;
- reschedule через `reschedule_lesson_with_entitlements(...)`, а не через
  low-level `reschedule_lesson(...)`.

Статус resolution generation conflict определяется по наличию
template-owned CANCELLED occurrence. Проверка выполняется пакетно, без
N+1-запроса на каждый audit event.

## 12.2. Compensation и entitlement operations

Manager UI поддерживает:

- список и карточку AbsenceCompensationCase;
- создание case из Attendance=ABSENT;
- FREE_MAKEUP materialization;
- PAID_MAKEUP authorization;
- выбор target Subscription;
- подтверждение оплаты;
- activation paid grant;
- explicit reversal с обязательной фиксацией refund decision, когда этого
  требует service;
- cancellation OPEN case;
- выдачу и отмену OneTimeEntitlement;
- выдачу административного MakeupEntitlement через
  `grant_administrative_makeup(...)`.

UI не повторяет eligibility, fee, target-period, balance или
double-compensation rules. Все эти проверки остаются в service layer.

## 12.3. Medical review

Раздел:

```text
/manager/attendance/medical/
```

показывает MEDICAL AbsenceJustification и позволяет менеджеру выполнять:

- `verify_medical_absence(...)`;
- `reject_medical_absence(...)`;
- `revoke_medical_absence(...)`.

Использованная medical makeup по-прежнему блокирует revoke на уровне service;
UI показывает бизнес-ошибку менеджеру.

## 12.4. Audit trail

Раздел:

```text
/manager/audit/
```

поддерживает фильтры по:

- event_type;
- aggregate_type;
- aggregate_id;
- correlation_id.

Карточки ScheduleTemplate, Lesson, compensation case/grant,
OneTimeEntitlement, medical justification и SubscriptionPeriod содержат
прямые ссылки на релевантный audit trail.

В частности, карточка Subscription показывает audit его
`SubscriptionPeriod`, поэтому
`SubscriptionPeriodActivationRevertSkipped` с причинами
`active_coverages_remain` и `dependent_rights_exist` виден менеджеру без
ручного поиска в Django Admin.



## 12.5. Ограничения manager choice fields и часовой пояс

Поля выбора занятия в manager operations используют школьный часовой пояс
`SCHOOL_TIME_ZONE`, а не timezone хранения в БД. Подписи Attendance и Lesson
форматируются через общий `format_school_datetime(...)`.

Чтобы формы не деградировали при накоплении истории, выбор Lesson в операциях
one-time entitlement и administrative makeup ограничен окном ±60 школьных
дней от текущей даты. Список Attendance для создания compensation case
ограничен тем же окном и дополнительно исключает пропуски, для которых уже
существует `OPEN` или `MATERIALIZED AbsenceCompensationCase`.

Следующий UX-этап при росте объёма данных — searchable/autocomplete выбор с
фильтром по ученику; текущий bounded queryset является защитой MVP от списков
на тысячи строк.

`datetime-local` в форме переноса Lesson разбирается непосредственно в
`SCHOOL_TIME_ZONE`. Поэтому корректность не зависит от совпадения
`TIME_ZONE` и `SCHOOL_TIME_ZONE`.

## 12.6. Общая presentation-инфраструктура

Набор permissions, дающих доступ к manager operations, определён один раз в
`core.permissions`. Home redirect и context processor используют один и тот
же helper, а базовый шаблон получает готовый
`manager_operations_available`.

Форматирование `ValidationError` для web messages вынесено в общий
presentation helper. POST endpoints сначала проверяют требуемые permissions и
только затем выполняют lookup объекта, поэтому существование UUID не меняет
403 на 404 для пользователя без соответствующего права.


# 13. Реализованный school administration UI

Штатное администрирование доменных сущностей школы доступно через manager web.
Создание authentication User пока остаётся отдельной identity-операцией:
существующий manager UI связывает уже созданные User с StudentAccess и
CoachProfile. До реализации invitation/external-auth workflow новый User
создаётся через Django Admin или другой административный identity-механизм.

Реализованы:

- Student: список, поиск, создание, изменение display name и активности;
- StudentAccess: привязка существующего User к Student с ролью SELF/GUARDIAN,
  отключение и повторная активация через изменение существующей записи;
- CoachProfile: создание профиля для существующего User, изменение имени и
  активности;
- TrainingGroup: список, поиск, создание и изменение code/name,
  default minimum attendees и активности;
- GroupMembership: общий список текущих/будущих/исторических интервалов,
  создание membership и изменение его starts_on/ends_on.

StudentAccess и CoachProfile не перепривязываются к другому User после
создания. GroupMembership не меняет Student/TrainingGroup после создания:
перевод ученика в другую группу моделируется завершением старого интервала и
созданием нового. Исторические записи не удаляются.

Все записи выполняются через application services и создают AuditEvent.
Существующие проверки пересечения GroupMembership остаются источником истины
для web и CLI.

## 13.1. Семантика активности

`Student.is_active = false` сохраняет историю и существующие StudentAccess,
но запрещает новые GroupMembership, не добавляет ученика в новые roster при
публикации и запрещает новые/изменённые RSVP. Родитель или SELF-пользователь
может продолжать видеть исторические данные.

`TrainingGroup.is_active = false` разрешается только после завершения либо
версионирования активных текущих/будущих ScheduleTemplate и после отмены или
переноса всех будущих неотменённых Lesson. Новые GroupMembership и новые
активные ScheduleTemplate для неактивной группы запрещены.

`CoachProfile.is_active = false` разрешается только после переназначения или
завершения активных текущих/будущих ScheduleTemplate и после
переназначения/отмены будущих неотменённых Lesson. Неактивный CoachProfile
нельзя использовать в новом активном ScheduleTemplate.

Генерация и публикация дополнительно проверяют активность group/coach, чтобы
legacy или ручные неконсистентные данные не создавали новые занятия.

Для DRAFT, RSVP_OPEN и CONFIRMED Lesson менеджер может выполнить
`reassign_lesson_coach(...)` без отмены или переноса самого занятия.
Операция требует активного свободного CoachProfile, обязательную причину и
создаёт `LessonCoachReassigned`. Lesson, RSVP, roster и entitlement links
сохраняют прежние идентификаторы.

При деактивации CoachProfile конечная старая версия ScheduleTemplate не
блокирует операцию, если все её оставшиеся будущие occurrences уже
материализованы. Каждый будущий неотменённый Lesson старого тренера при этом
по-прежнему блокирует деактивацию, пока занятие не переназначено, не отменено
или не завершено. Это позволяет новой версии шаблона работать с новым
тренером, а опубликованному занятию старой версии — получить явную подмену.
Повторная генерация такой конечной старой версии идемпотентно возвращает уже
материализованный Lesson и не создаёт занятие с неактивным тренером.

## 13.2. Списки и identity choices

Списки Student, CoachProfile, TrainingGroup и GroupMembership пагинируются по
50 строк и показывают общий размер выборки; поиск и membership-state filter
сохраняются при переходе между страницами.

User dropdown в StudentAccess/CoachProfile не показывает email. Полнотекстовый
поиск/autocomplete пользователей и отдельный invitation workflow остаются
следующим этапом identity UI.


# 14. Catalog & policy administration UI

Manager web содержит отдельный раздел «Каталог и правила» для конфигурации
будущих абонементов и компенсационных правил.

## 14.1. Модели расчётного периода

Через web доступны создание и изменение `SubscriptionPeriodScheme`:

- календарный месяц;
- rolling 28 дней от первого занятия;
- общий fixed-28 цикл с `fixed_anchor_date`;
- активация/деактивация схемы.

Для fixed-28 anchor обязателен; для остальных режимов anchor запрещён.

Поля, определяющие семантику периода — `mode` и `fixed_anchor_date` —
можно менять только пока scheme ни разу не использована. После появления
ссылки из любого `SubscriptionPlan` или `SubscriptionPeriod` они
неизменяемы. Для нового правила создаётся новая scheme, после чего будущие
тарифы или существующий тариф явно переключаются на неё.

Scheme нельзя деактивировать, пока на неё ссылается хотя бы один активный
`SubscriptionPlan`. Это сохраняет инвариант «активный тариф → активная
модель периода» и не позволяет незаметно сломать выдачу действующего тарифа.

Уже созданные `SubscriptionPeriod` дополнительно сохраняют
`mode_snapshot` и `fixed_anchor_snapshot`; исторический период не зависит
от последующих изменений имени, кода или активности каталожной scheme.

## 14.2. Тарифы и allowances

`SubscriptionPlan` редактируется одной атомарной операцией вместе с лимитами
ICE/HALL. У активного тарифа должна быть активная модель расчётного периода и
хотя бы один положительный allowance.

Изменение названия, period scheme или ICE/HALL limits применяется только к
будущим выдачам. Уже выданный `Subscription` хранит plan name/code snapshot,
а `SubscriptionAllowance` — `visit_limit_snapshot`; manager catalog не
изменяет эти исторические значения.

## 14.3. Политики компенсаций

Manager web поддерживает:

- создание `AbsenceCompensationPolicy`;
- настройку `AbsenceCompensationPolicyAction`;
- сезонные `AbsenceCompensationPolicyWindow`;
- изменение ещё не использованной версии;
- создание новой версии использованной политики.

После появления хотя бы одного `AbsenceCompensationCase` policy version,
её actions и windows считаются неизменяемыми. Добавление новых children к уже
использованной версии также запрещено на уровне модели и application service.

Versioning выполняется атомарно. Новая версия:

- сохраняет тот же `code` и `absence_reason`;
- получает следующий `version`;
- должна начинаться позже текущего школьного дня;
- копирует actions и windows исходной версии;
- закрывает исходную версию днём перед `effective_from` новой версии, если
  исходная версия до этого пересекала новый интервал.

Закрытие `effective_until` использованной исходной версии выполняется только
в специальных lifecycle services: versioning либо явное завершение policy.
Операция «Завершить с даты» не создаёт новую версию: она делает предыдущий
календарный день последним днём действия текущей последней версии. Это
предназначено для случая, когда компенсации по данной причине нужно прекратить,
а не заменить новым правилом.

Создание, изменение, versioning и завершение policies сериализуются по
`absence_reason` транзакционным PostgreSQL advisory lock. Поэтому две
параллельные операции с разными `code`, но одной причиной пропуска, не могут
одновременно пройти проверку пересечения effective intervals. Создание case
по-прежнему блокирует выбранную policy row и перепроверяет selector перед
снапшотом actions/windows.

## 14.4. Права и аудит

Catalog UI использует стандартные Django permissions `view/add/change` для:

- `SubscriptionPeriodScheme`;
- `SubscriptionPlan`;
- `AbsenceCompensationPolicy`;
- `AbsenceCompensationPolicyAction`;
- `AbsenceCompensationPolicyWindow`.

Все изменения проходят через application services и создают `AuditEvent`.
Удаление из manager UI не используется: исторические конфигурации сохраняются,
а для ещё не использованных сущностей применяется `is_active=false`.
