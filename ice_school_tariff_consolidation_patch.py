#!/usr/bin/env python3
from __future__ import annotations
import argparse, difflib, hashlib, sys
from pathlib import Path

DOCS = Path('docs')
FILES = {
    'domain': DOCS / 'Domain Model — школа фигурного катания.md',
    'state': DOCS / 'State Machines и Domain Events.md',
    'django': DOCS / 'Django Models and Application Services Specification.md',
    'spec': DOCS / 'Спецификация системы расписания, посещаемости и абонементов школы фигурного катания.md',
    'tariffs': DOCS / 'TARIFFS_AND_ENTITLEMENTS.md',
}
EXPECTED_SHA = {
    # Current main when this consolidation patch was prepared.
    'domain': '70ca6f8cbe97e99a3825c7493d433aeccd544fab',
    'state': '739de390ac9ea0377b0ce1ee95a0d1afac4ff790',
    'django': 'a983b755ce81ec52f1f8317d2c861ef3b21adb9e',
    'spec': 'ce6f7659a10b43019f4b7435cbcdf27d9c75f756',
    'tariffs': 'a2a25635fd943896f714831f156d67c70ac93676',
}

def replace_between(text: str, start: str, end: str, new: str) -> str:
    a = text.find(start)
    if a < 0:
        raise RuntimeError(f'Heading not found: {start!r}')
    b = text.find(end, a + len(start))
    if b < 0:
        raise RuntimeError(f'End heading not found after {start!r}: {end!r}')
    return text[:a] + new.rstrip() + '\n\n---\n\n' + text[b:]

def replace_section(text: str, heading: str, next_heading: str, body: str) -> str:
    return replace_between(text, heading, next_heading, heading + '\n\n' + body.strip())

DOMAIN_32_58 = r'''# 32. Абонементы и категории посещений

В системе существуют две категории посещений, которые расходуют независимые лимиты:

```text
ICE
HALL
```

`ICE` используется для занятий на льду. `HALL` используется для ОФП и хореографии.

Абонемент не обязан относиться только к одной категории. Смешанный тариф является **одним `Subscription`**, внутри которого существуют один или два независимых `SubscriptionAllowance`.

---

# 33. Текущая тарифная матрица

Текущие месячные планы:

| План | ICE | HALL |
|---|---:|---:|
| 4 зала | 0 | 4 |
| 8 льдов | 8 | 0 |
| 8 льдов + 8 залов | 8 | 8 |
| 8 льдов + 12 залов | 8 | 12 |
| 12 льдов | 12 | 0 |
| 12 льдов + 8 залов | 12 | 8 |
| 12 льдов + 12 залов | 12 | 12 |
| 12 льдов + 16 залов | 12 | 16 |
| 16 льдов + 16 залов | 16 | 16 |
| 20 льдов + 16 залов | 20 | 16 |
| 20 льдов + 20 залов | 20 | 20 |
| 20 льдов + 24 зала | 20 | 24 |
| 24 льда + 24 зала | 24 | 24 |

Все текущие планы действуют один месяц.

---

# 34. SubscriptionPlan

`SubscriptionPlan` описывает продаваемый месячный пакет целиком:

```text
SubscriptionPlan
id
code
name
validity_months = 1
is_active
```

Категория и количество занятий находятся в дочерних `SubscriptionPlanAllowance`.

---

# 35. SubscriptionPlanAllowance

```text
SubscriptionPlanAllowance
id
plan_id
category: ICE | HALL
visit_limit
```

Инварианты:

```text
UNIQUE(plan_id, category)
visit_limit > 0
```

---

# 36. Subscription

`Subscription` — конкретно выданный ученику экземпляр плана:

```text
Subscription
id: UUID
student_id
plan_id
plan_code_snapshot
plan_name_snapshot
valid_from
valid_until
cancelled_at nullable
created_at
created_by
```

Один `Subscription` соответствует одному выданному тарифу, даже если тариф смешанный.

---

# 37. SubscriptionAllowance

При выдаче `Subscription` allowances плана копируются в snapshot:

```text
SubscriptionAllowance
id
subscription_id
category: ICE | HALL
visit_limit_snapshot
```

Инварианты:

```text
UNIQUE(subscription_id, category)
visit_limit_snapshot > 0
```

Изменение `SubscriptionPlan` не изменяет ранее выданные allowances.

---

# 38. Срок действия

Все текущие абонементы имеют продолжительность один месяц. Конкретный `Subscription` хранит явные `valid_from` и `valid_until`; именно они являются источником истины.

---

# 39. Смешанный абонемент

```text
Subscription «8 ICE + 12 HALL»
01.09–30.09

├── ICE allowance  = 8
└── HALL allowance = 12
```

Лимиты расходуются независимо.

---

# 40. Несколько абонементов

У Student могут одновременно существовать несколько `Subscription`, включая пересекающиеся по датам планы. Выбор конкретного allowance должен быть детерминированным.

---

# 41. SubscriptionLedgerEntry

Ledger относится к конкретному allowance:

```text
SubscriptionLedgerEntry
id
allowance_id
entry_type: GRANT | CONSUME | RESTORE | ADJUSTMENT
delta
attendance_coverage_id nullable
reason nullable
created_at
created_by_user_id
```

Авторитетный баланс категории:

```text
SUM(delta) WHERE allowance_id = ...
```

---

# 42. Начальная выдача

При выдаче смешанного тарифа создаётся отдельный `GRANT` для каждого allowance:

```text
ICE allowance:  GRANT +8
HALL allowance: GRANT +12
```

Общего числового баланса `Subscription` нет.

---

# 43. OneTimeEntitlement

Разовые, пробные, индивидуальные и мини-групповые посещения не моделируются как абонементы на одно занятие:

```text
OneTimeEntitlement
id
student_id
lesson_id
entitlement_type:
    SINGLE_ICE
    SINGLE_HALL
    INDIVIDUAL_ICE
    MINI_GROUP_ICE
    TRIAL_ICE
category: ICE | HALL
cancelled_at nullable
created_at
created_by
```

`TRIAL_HALL` отсутствует.

---

# 44. AttendanceCoverage

`AttendanceCoverage` хранит, каким правом покрыто конкретное `Attendance=PRESENT`:

```text
AttendanceCoverage
id
attendance_id
subscription_allowance_id nullable
one_time_entitlement_id nullable
makeup_entitlement_id nullable
created_at
created_by
reversed_at nullable
reversed_by nullable
```

Ровно один основной источник покрытия:

```text
subscription_allowance XOR one_time_entitlement
```

`makeup_entitlement` только разрешает исключительное использование исходного allowance.

---

# 45. Зачем нужен AttendanceCoverage

Он позволяет корректно выполнить возврат, отличить месячный абонемент от отдельно оплаченного занятия и сохранить аудит медицинских/административных переносов. Для одного Attendance может существовать не более одного активного coverage.

---

# 46. Выбор покрытия

При `Attendance=PRESENT` порядок такой:

```text
1. OneTimeEntitlement, привязанный к Lesson
2. target-specific MakeupEntitlement
3. другой действующий MakeupEntitlement с ближайшим окончанием
4. обычный SubscriptionAllowance с ближайшим окончанием родительского Subscription
5. UNCOVERED
```

Так отдельно оплаченное разовое/индивидуальное/пробное занятие не расходует месячный allowance.

---

# 47. Категория определяется занятием

Категория берётся из `Lesson.lesson_type.subscription_category`. `ICE` расходует только ICE allowance/entitlement; `PHYSICAL` и `CHOREOGRAPHY` — только HALL.

---

# 48. Проверка срока

Для обычного `SubscriptionAllowance` срок проверяется по `Subscription.valid_from/valid_until` относительно даты Lesson. `MakeupEntitlement` может разрешить использование конкретного исходного allowance за пределами обычного срока.

---

# 49. Выбор из нескольких абонементов

Для нескольких обычных allowances одной категории порядок родительских `Subscription`:

1. ближайший `valid_until`;
2. затем наиболее ранний `valid_from`;
3. затем наиболее ранний `created_at`.

---

# 50. Остаток

Остаток существует по каждому allowance отдельно:

```text
allowance_balance = SUM(SubscriptionLedgerEntry.delta)
```

Полей `remaining_visits` и общего `subscription_balance` как источников истины нет.

---

# 51. Исчерпанный allowance

Если баланс одного allowance равен нулю, только эта категория считается исчерпанной. Другой allowance того же смешанного `Subscription` может оставаться доступным.

---

# 52. Истёкший Subscription

После `valid_until` обычное использование allowances прекращается. Остатки сохраняются исторически и не обнуляются фиктивной операцией `BURNED`.

---

# 53. Посещение без покрытия

Отсутствие подходящего allowance или one-time entitlement не мешает сохранить `Attendance=PRESENT`.

```text
UNCOVERED = Attendance=PRESENT AND active AttendanceCoverage отсутствует
```

---

# 54. Исправление PRESENT → ABSENT

Для allowance-backed coverage создаётся `RESTORE +1` в тот же allowance. Для one-time coverage ledger не меняется: entitlement снова становится доступным после reversal. Использованный `MakeupEntitlement` также освобождается.

---

# 55. Исправление ABSENT → PRESENT

Система повторно выполняет `assign_attendance_coverage()`.

---

# 56. Переназначение покрытия

Перепривязка allowance-backed coverage выполняется компенсирующе:

```text
старый allowance: RESTORE +1
старый AttendanceCoverage: reversed
новый AttendanceCoverage: created
новый allowance: CONSUME -1
```

---

# 57. MakeupEntitlement и allowance

`MakeupEntitlement` ссылается на `source_subscription_allowance_id`, а не на `Subscription` целиком. Медицинский перенос ICE не может использовать HALL-остаток и наоборот.

---

# 58. Derived status Subscription

Статус `Subscription` вычисляется из дат и cancellation. `EXHAUSTED` для смешанного плана означает, что все его allowances исчерпаны. Для UI статус каждого allowance вычисляется отдельно.
'''

DOMAIN_59_62 = r'''# 59. Центральные Aggregate Roots

## Student Aggregate

```text
Student
 ├── StudentAccess
 └── GroupMembership
```

## Lesson Aggregate

```text
Lesson
 ├── LessonResponse
 └── Attendance
```

## Subscription Aggregate

```text
Subscription
 └── SubscriptionAllowance
      └── SubscriptionLedgerEntry
```

`AttendanceCoverage` связывает Attendance с конкретным entitlement и является отдельной связующей сущностью.

---

# 60. Взаимодействие aggregates

```text
Student → TrainingGroup → Lesson → Attendance
                              │
                              ▼
                    AttendanceCoverage
                      /             \\
     SubscriptionAllowance      OneTimeEntitlement
              │
              ▼
            Ledger
```

`MakeupEntitlement` при необходимости модифицирует использование конкретного `SubscriptionAllowance`.

---

# 61. Полная схема отношений

```text
UserAccount → StudentAccess → Student
                              │
                ┌─────────────┴──────────────┐
                ▼                            ▼
        GroupMembership                 Subscription
                │                            │
                ▼                            ▼
        TrainingGroup              SubscriptionAllowance
                │                            │
                ▼                            ▼
             Lesson ──► Attendance ──► AttendanceCoverage
                │                            │
                ▼                 ┌──────────┴───────────┐
           LessonType             ▼                      ▼
                │        SubscriptionAllowance   OneTimeEntitlement
                ▼
      SubscriptionCategory
           ICE / HALL
```

---

# 62. Ключевые domain invariants

1. `UserAccount` и `Student` — разные сущности.
2. SELF/GUARDIAN могут менять RSVP, но не Attendance.
3. Attendance тренера/администратора — источник истины о фактическом посещении.
4. RSVP не расходует entitlement.
5. Только `Attendance=PRESENT` может создать активный `AttendanceCoverage`.
6. На один Attendance допускается не более одного активного coverage.
7. Смешанный Subscription содержит независимые ICE/HALL allowances.
8. Категория Lesson обязана совпадать с категорией выбранного allowance/one-time entitlement.
9. Обычный allowance должен быть действующим на дату Lesson и иметь положительный баланс.
10. `OneTimeEntitlement` имеет приоритет над месячным allowance для привязанного Lesson.
11. `MakeupEntitlement` не увеличивает купленное количество, а только расширяет допустимое использование исходного allowance.
12. Отсутствие покрытия не препятствует `Attendance=PRESENT`.
13. `PRESENT → ABSENT` обращает именно использованное покрытие.
14. Ledger entries immutable; исправления компенсирующие.
15. Изменение plan не меняет snapshot уже выданного Subscription.
16. Изменение ScheduleTemplate не меняет историю уже проведённых Lesson.
'''

DOMAIN_66_69 = r'''# 66. Пример списания льда

У Маши план `8 ICE + 12 HALL`; в ICE allowance осталось 5.

```text
22 сентября / ICE
Trainer → PRESENT
        ↓
AttendanceCoverage
        ↓
SubscriptionAllowance(ICE)
        ↓
CONSUME -1
```

Новый ICE-баланс — 4. HALL-баланс не меняется.

---

# 67. Пример занятия в зале

`CHOREOGRAPHY` и `PHYSICAL` имеют `subscription_category=HALL` и расходуют HALL allowance. ICE allowance смешанного тарифа остаётся неизменным.

Если на конкретный Lesson заранее создан `OneTimeEntitlement(SINGLE_HALL)`, используется он, а месячный HALL allowance не расходуется.

---

# 68. Что не должно находиться в domain model MVP

В MVP не требуется полноценный billing: `Payment`, `Invoice`, банковские данные и `Refund` остаются будущим bounded context. При этом доменная модель уже поддерживает тарифные entitlements, необходимые для разовых, пробных, индивидуальных и мини-групповых занятий.

`SKATE_RENTAL` является add-on услугой и не создаёт entitlement на посещение.

---

# 69. Рекомендуемый набор Django models

```text
User
ExternalIdentity
Student
StudentAccess
CoachProfile
TrainingGroup
GroupMembership
Venue
LessonType
ScheduleTemplate
Lesson
LessonResponse
Attendance
SubscriptionPlan
SubscriptionPlanAllowance
Subscription
SubscriptionAllowance
OneTimeEntitlement
MakeupEntitlement
AttendanceCoverage
SubscriptionLedgerEntry
AuditEvent
```
'''

STATE_17 = r'''# 17. Перенос по инициативе школы и абонемент

Если школа переносит занятие и replacement выходит за `valid_until` исходного `Subscription`, ученик не должен терять возможность использовать уже купленный остаток только из-за решения школы.

Для участника с `RSVP=YES` определяется allowance той же категории, который мог покрыть исходный Lesson. При необходимости создаётся:

```text
MakeupEntitlement
reason = SCHOOL_RESCHEDULE
source_subscription_allowance = исходный allowance
target_lesson = replacement Lesson
```

Entitlement не добавляет `GRANT +1`: он разрешает использовать существующий положительный остаток исходного allowance на replacement Lesson.
'''

STATE_32_41 = r'''# 32. Subscription state machine

Lifecycle существует у `Subscription`, но расходуемое состояние отслеживается по каждому `SubscriptionAllowance` отдельно.

```text
Subscription:
UPCOMING → ACTIVE → EXPIRED
                 └→ CANCELLED

Allowance while parent ACTIVE:
AVAILABLE ⇄ EXHAUSTED
```

Смешанный Subscription может одновременно иметь `ICE=EXHAUSTED` и `HALL=AVAILABLE`.

---

# 33. Какие состояния реально хранить

`UPCOMING`, `ACTIVE`, `EXPIRED` и `EXHAUSTED` вычисляются из `valid_from`, `valid_until`, cancellation и ledger. Хранится только явная административная отмена.

---

# 34. ACTIVE

Обычный allowance доступен, если родительский Subscription действует на дату Lesson, не отменён, category совпадает и `allowance_balance > 0`.

---

# 35. Списание

`AttendanceMarkedPresent` запускает общий coverage engine. Для monthly coverage создаются:

```text
AttendanceCoverage(subscription_allowance=...)
SubscriptionLedgerEntry(CONSUME, -1)
```

Если к Lesson привязан `OneTimeEntitlement`, он имеет приоритет и monthly ledger не меняется.

---

# 36. EXHAUSTED

Событие `SubscriptionAllowanceExhausted` возникает, когда баланс конкретного allowance достигает нуля. `SubscriptionExhausted` может быть derived event только когда исчерпаны все allowances.

---

# 37. Возврат посещения

При `PRESENT → ABSENT` coverage обращается. Для allowance-backed coverage создаётся `RESTORE +1` в тот же allowance; one-time entitlement просто освобождается.

---

# 38. EXPIRED

После `valid_until` обычное использование allowances прекращается. Исторические положительные остатки сохраняются в ledger.

---

# 39. Почему не нужно списывать «сгоревший» остаток

Ledger должен показывать фактическую историю: сколько было выдано и сколько реально использовано по ICE и HALL отдельно. Фиктивный `BURNED` не создаётся.

---

# 40. Событие истечения с остатком

При истечении подписки вычисляются остатки по каждому allowance. Событие `SubscriptionExpiredWithUnusedBalance` содержит категориальные остатки, например `{ICE: 2, HALL: 0}`.

---

# 41. Денежный возврат не должен быть состоянием Subscription

Возврат денег относится к будущему billing context (`Payment`/`Refund`) и не переписывает Attendance, Coverage или Ledger. Исключительное продление права посещения оформляется `MakeupEntitlement`, а не изменением исторических операций.
'''

STATE_55_66 = r'''# 55. MakeupEntitlement

`MakeupEntitlement` разрешает исключительное использование существующего `SubscriptionAllowance`.

---

# 56. Структура MakeupEntitlement

```text
MakeupEntitlement
id
student_id
source_lesson_id
source_subscription_allowance_id
category: ICE | HALL
reason: MEDICAL_VERIFIED | SCHOOL_RESCHEDULE | ADMINISTRATIVE
valid_from
valid_until
target_lesson_id nullable
cancelled_at nullable
```

Category должна совпадать с category исходного allowance.

---

# 57. Важный принцип MakeupEntitlement

Он не создаёт `GRANT +1` и не увеличивает купленный объём. Использование разрешено только при положительном балансе исходного allowance.

---

# 58. Пример медицинского переноса

У сентябрьского ICE allowance осталось 2 посещения. Медицинский пропуск создаёт `MakeupEntitlement(source_subscription_allowance=ICE)`. После окончания сентября entitlement разрешает потратить **одно из этих двух** ICE-посещений в дополнительный срок.

---

# 59. Использование медицинского переноса

При новом `Attendance=PRESENT` создаётся `AttendanceCoverage` с `subscription_allowance` и `makeup_entitlement`, затем обычный `CONSUME -1` в source allowance.

---

# 60. Почему медицинский перенос не должен давать +1

Если исходный allowance уже имеет balance=0, entitlement использовать нельзя. Поэтому общее число фактических посещений не может превысить приобретённый лимит за счёт медицинской справки.

---

# 61. Несколько медицинских пропусков

Несколько подтверждённых пропусков могут создать несколько entitlements, но каждый требует положительного остатка соответствующего source allowance при использовании.

---

# 62. Срок медицинского переноса

Срок остаётся конфигурируемой school policy или задаётся `valid_until` при подтверждении основания.

---

# 63. MakeupEntitlement state machine

```text
AVAILABLE → USED
    └────→ EXPIRED
    └────→ CANCELLED
```

`USED` означает наличие активного `AttendanceCoverage`, ссылающегося на entitlement.

---

# 64. Выбор покрытия при PRESENT

```text
1. OneTimeEntitlement, bound to exact Lesson
2. target-specific MakeupEntitlement
3. other MakeupEntitlement, earliest expiry
4. ordinary SubscriptionAllowance, parent Subscription earliest expiry
5. UNCOVERED
```

---

# 65. AttendanceCoverage с переносом

```text
AttendanceCoverage
attendance_id
subscription_allowance_id nullable
one_time_entitlement_id nullable
makeup_entitlement_id nullable
reversed_at nullable
```

Для medical/school/admin makeup primary source остаётся `SubscriptionAllowance`.

---

# 66. Списание остаётся одинаковым

Обычное использование allowance и использование через makeup создают одинаковый `CONSUME -1`. Разница хранится в `AttendanceCoverage.makeup_entitlement`. One-time coverage ledger абонемента не затрагивает.
'''

STATE_69_74 = r'''# 69. Domain Events — Lesson

События Lesson остаются без изменения: `LessonCreated`, `LessonPublished`, `LessonResponseChanged`, `LessonMinimumReached`, `LessonMinimumNotMet`, `LessonConfirmed`, `LessonCancelled`, `LessonRescheduled`, `LessonCompleted`, `LessonAttendanceSubmitted`, `LessonAttendanceReopened`.

---

# 70. Domain Events — Attendance

| Event | Значение |
|---|---|
| `AttendanceMarkedPresent` | Тренер отметил присутствие |
| `AttendanceMarkedAbsent` | Тренер отметил отсутствие |
| `AttendanceCorrectedToPresent` | ABSENT → PRESENT |
| `AttendanceCorrectedToAbsent` | PRESENT → ABSENT |
| `AttendanceCoverageAssigned` | Назначено entitlement-покрытие |
| `AttendanceUncovered` | Подходящего покрытия нет |
| `AttendanceCoverageReversed` | Покрытие обращено |

---

# 71. Domain Events — Subscription

| Event | Значение |
|---|---|
| `SubscriptionIssued` | Выдан Subscription и его allowances |
| `SubscriptionActivated` | Наступила дата начала |
| `SubscriptionAllowanceConsumed` | Из allowance списано занятие |
| `SubscriptionAllowanceRestored` | Занятие возвращено в allowance |
| `SubscriptionAllowanceExhausted` | Баланс allowance достиг нуля |
| `SubscriptionExpired` | Закончился обычный срок |
| `SubscriptionExpiredWithUnusedBalance` | Остались ICE/HALL остатки |
| `SubscriptionCancelled` | Subscription отменён |
| `OneTimeEntitlementGranted` | Создано разовое право |
| `OneTimeEntitlementUsed` | Разовое право использовано |

---

# 72. Domain Events — справки и переносы

Сохраняются `AbsenceJustificationDeclared/Verified/Rejected`, `MakeupEntitlementGranted/Used/Expired/Cancelled`.

---

# 73. Что происходит при AttendanceMarkedPresent

```text
AttendanceMarkedPresent
        ↓
FindCoverage
        ↓
OneTimeEntitlement?
        ↓ no
MakeupEntitlement + source allowance?
        ↓ no
ordinary SubscriptionAllowance?
        ↓
Create AttendanceCoverage
        ↓
если allowance-backed: CONSUME -1
        ↓
AttendanceCoverageAssigned
```

Если покрытия нет, Attendance всё равно остаётся `PRESENT`, генерируется `AttendanceUncovered`.

---

# 74. Что происходит при PRESENT → ABSENT

```text
AttendanceCorrectedToAbsent
        ↓
Find active AttendanceCoverage
        ↓
allowance-backed? → RESTORE +1
one-time?          → release entitlement
makeup?            → entitlement снова AVAILABLE
        ↓
mark coverage reversed
        ↓
AttendanceCoverageReversed
```
'''

STATE_76_78 = r'''# 76. Отчёт по абонементам

Отчёт показывает один Subscription и независимые остатки его allowances:

```text
12 ICE + 16 HALL
01.09–30.09
ICE:  used 10 / remaining 2
HALL: used 12 / remaining 4
Medical makeups: 1
```

---

# 77. Конфликт «занятия сгорели»

Для разбора конфликта система показывает исходный plan snapshot, фактические посещения и неиспользованные остатки **по каждой категории отдельно**, а также созданные/использованные MakeupEntitlement. Денежное решение оформляется отдельно и не переписывает эти данные.

---

# 78. Что система не должна делать при конфликте

Нельзя задним числом менять `valid_until`, удалять ledger entries или создавать необъяснимый `GRANT`. Если школа разрешает дополнительное использование остатка — создаётся `MakeupEntitlement(reason=ADMINISTRATIVE)` с actor/reason/audit.
'''

STATE_80 = r'''# 80. Финальные domain invariants

### LESSON
RSVP и решение о проведении занятия не являются финансовым списанием; перенос создаёт новый Lesson.

### ATTENDANCE
Фактическую посещаемость определяет тренер/администратор. Один Attendance имеет не более одного активного `AttendanceCoverage`. `PRESENT` допустим без покрытия.

### SUBSCRIPTION
Смешанный Subscription содержит независимые `SubscriptionAllowance`. Списание и возврат выполняются по конкретному allowance. Обычная отработка использует обычный остаток; `MakeupEntitlement` не создаёт дополнительные посещения. Истёкшие остатки не обнуляются в ledger.

### ONE-TIME
Привязанный к Lesson `OneTimeEntitlement` имеет приоритет над месячным allowance. Пробный entitlement существует только для ICE. Прокат коньков entitlement не создаёт.

### MEDICAL
Медицинское основание не меняет `Attendance=ABSENT`; VERIFIED может создать MakeupEntitlement на конкретный source allowance. В MVP сам медицинский документ не хранится.
'''

DJANGO_20_24 = r'''# 20. SubscriptionPlan и SubscriptionPlanAllowance

```python
class SubscriptionPlan(models.Model):
    id = UUIDField(...)
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    validity_months = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class SubscriptionPlanAllowance(models.Model):
    id = UUIDField(...)
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.CASCADE, related_name="allowances")
    category = models.CharField(max_length=16, choices=SubscriptionCategory.choices)
    visit_limit = models.PositiveSmallIntegerField()
```

Constraints:

```python
UniqueConstraint(fields=["plan", "category"], name="subplan_allowance_category_uniq")
CheckConstraint(condition=Q(visit_limit__gt=0), name="subplan_allowance_limit_gt_0")
CheckConstraint(condition=Q(validity_months=1), name="subscriptionplan_one_month")
```

---

# 21. Subscription и SubscriptionAllowance

```python
class Subscription(models.Model):
    id = UUIDField(...)
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    plan_code_snapshot = models.CharField(max_length=64)
    plan_name_snapshot = models.CharField(max_length=128)
    valid_from = models.DateField()
    valid_until = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

class SubscriptionAllowance(models.Model):
    id = UUIDField(...)
    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="allowances")
    category = models.CharField(max_length=16, choices=SubscriptionCategory.choices)
    visit_limit_snapshot = models.PositiveSmallIntegerField()
```

Constraints/indexes:

```python
UniqueConstraint(fields=["subscription", "category"], name="suballow_subscription_category_uniq")
CheckConstraint(condition=Q(visit_limit_snapshot__gt=0), name="suballow_limit_gt_0")
Index(fields=["subscription", "category"], name="suballow_subscription_category_idx")
Index(fields=["student", "valid_from"], name="subscription_student_history_idx")
```

`valid_until >= valid_from` и cancellation consistency остаются constraints `Subscription`.

---

# 22. MakeupEntitlement

`MakeupEntitlement.source_subscription_allowance` — FK на конкретный `SubscriptionAllowance`.

```python
source_subscription_allowance = models.ForeignKey(
    SubscriptionAllowance,
    on_delete=models.PROTECT,
    related_name="makeup_entitlements",
)
```

Остальные поля/constraints остаются как раньше; service layer проверяет совпадение student/category с source allowance.

---

# 23. OneTimeEntitlement и AttendanceCoverage

```python
class OneTimeEntitlement(models.Model):
    class Type(models.TextChoices):
        SINGLE_ICE = "single_ice", "Single ICE"
        SINGLE_HALL = "single_hall", "Single HALL"
        INDIVIDUAL_ICE = "individual_ice", "Individual ICE"
        MINI_GROUP_ICE = "mini_group_ice", "Mini-group ICE"
        TRIAL_ICE = "trial_ice", "Trial ICE"

    id = UUIDField(...)
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="one_time_entitlements")
    lesson = models.ForeignKey(Lesson, on_delete=models.PROTECT, related_name="one_time_entitlements")
    entitlement_type = models.CharField(max_length=24, choices=Type.choices)
    category = models.CharField(max_length=16, choices=SubscriptionCategory.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    cancelled_at = models.DateTimeField(null=True, blank=True)

class AttendanceCoverage(models.Model):
    id = UUIDField(...)
    attendance = models.ForeignKey(Attendance, on_delete=models.PROTECT, related_name="coverages")
    subscription_allowance = models.ForeignKey(SubscriptionAllowance, null=True, blank=True, on_delete=models.PROTECT, related_name="coverages")
    one_time_entitlement = models.ForeignKey(OneTimeEntitlement, null=True, blank=True, on_delete=models.PROTECT, related_name="coverages")
    makeup_entitlement = models.ForeignKey(MakeupEntitlement, null=True, blank=True, on_delete=models.PROTECT, related_name="coverages")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
```

Required constraints:

```python
UniqueConstraint(fields=["attendance"], condition=Q(reversed_at__isnull=True), name="coverage_one_active_per_attendance")
UniqueConstraint(fields=["one_time_entitlement"], condition=Q(one_time_entitlement__isnull=False, reversed_at__isnull=True), name="coverage_one_active_per_one_time")
UniqueConstraint(fields=["makeup_entitlement"], condition=Q(makeup_entitlement__isnull=False, reversed_at__isnull=True), name="coverage_one_active_per_makeup")
CheckConstraint(condition=(Q(subscription_allowance__isnull=False, one_time_entitlement__isnull=True) | Q(subscription_allowance__isnull=True, one_time_entitlement__isnull=False)), name="coverage_exactly_one_primary_source")
CheckConstraint(condition=(Q(makeup_entitlement__isnull=True) | Q(subscription_allowance__isnull=False)), name="coverage_makeup_requires_allowance")
```

---

# 24. SubscriptionLedgerEntry

```python
class SubscriptionLedgerEntry(models.Model):
    class EntryType(models.TextChoices):
        GRANT = "grant", "Grant"
        CONSUME = "consume", "Consume"
        RESTORE = "restore", "Restore"
        ADJUSTMENT = "adjustment", "Adjustment"

    id = UUIDField(...)
    allowance = models.ForeignKey(SubscriptionAllowance, on_delete=models.PROTECT, related_name="ledger_entries")
    coverage = models.ForeignKey(AttendanceCoverage, null=True, blank=True, on_delete=models.PROTECT, related_name="ledger_entries")
    entry_type = models.CharField(max_length=16, choices=EntryType.choices)
    delta = models.SmallIntegerField()
    reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
```

`GRANT/ADJUSTMENT` не имеют coverage; `CONSUME/RESTORE` обязаны иметь coverage. Один `GRANT` допускается на allowance; для одного coverage допускается не более одного `CONSUME` и одного `RESTORE`. Balance = `SUM(delta)` по allowance.
'''

DJANGO_26 = r'''# 26. Что PostgreSQL не может корректно проверить обычным CheckConstraint

Service layer обязан проверять межтабличные invariants:

- actor имеет право менять RSVP/Attendance;
- Attendance.student соответствует entitlement.student;
- category Lesson совпадает с `SubscriptionAllowance.category` или `OneTimeEntitlement.category`;
- родительский Subscription allowance действует на дату Lesson и не отменён;
- `MakeupEntitlement.source_subscription_allowance` совпадает с allowance в Coverage;
- one-time entitlement привязан к этому Lesson;
- `TRIAL_ICE` имеет category=ICE; trial HALL не существует;
- баланс allowance положителен до `CONSUME`;
- CLOSED Lesson нельзя менять без reopen;
- все активные roster entries имеют Attendance перед close.

Эти правила не распределяются по `model.save()` и signals.
'''

DJANGO_37 = r'''# 37. Entitlements при переносе школы

Если replacement Lesson выходит за обычный срок, для участников с `RSVP=YES` определяется `SubscriptionAllowance` той же категории, который мог покрыть исходный Lesson. При необходимости создаётся `MakeupEntitlement(source_subscription_allowance=..., target_lesson=replacement)`.

Никакого `GRANT` при этом не создаётся.
'''

DJANGO_49_63 = r'''# 49. subscriptions.selectors.allowance_balance()

```python
allowance_balance(allowance_id: UUID) -> int
```

Возвращает `COALESCE(SUM(delta), 0)` по `SubscriptionLedgerEntry.allowance_id`.

Дополнительно:

```python
subscription_balances(subscription_id) -> dict[SubscriptionCategory, int]
```

---

# 50. subscriptions.services.issue_subscription()

В одной транзакции сервис создаёт `Subscription`, копирует каждый `SubscriptionPlanAllowance` в `SubscriptionAllowance` и создаёт отдельный `GRANT` на каждый allowance.

---

# 51. Правило «один месяц»

Business policy вычисляет `valid_from/valid_until`; конкретные даты сохраняются в `Subscription` и не пересчитываются задним числом.

---

# 52. subscriptions.services.assign_attendance_coverage()

```python
assign_attendance_coverage(*, attendance_id: UUID, actor: User | None) -> AttendanceCoverage | None
```

Идемпотентная критическая транзакция coverage engine.

---

# 53. Preconditions

`Attendance=PRESENT`, активного Coverage ещё нет. Повторный вызов возвращает существующий Coverage.

---

# 54. Определение категории

Категория берётся из `Attendance.lesson.lesson_type.subscription_category`.

---

# 55. Сначала ищется OneTimeEntitlement

Ищется активный entitlement для `student + exact lesson + category`. Он имеет приоритет над monthly allowance.

---

# 56. Затем MakeupEntitlement

Ищется entitlement той же категории, допустимый для Lesson и ещё не использованный. Блокируется его `source_subscription_allowance`, после чего проверяется положительный balance.

---

# 57. Затем обычный SubscriptionAllowance

Ищутся allowances нужной category, чьи parent Subscription действуют на дату Lesson и не отменены. Порядок: `valid_until`, `valid_from`, `created_at`. Candidate allowance блокируется через `select_for_update()` перед пересчётом balance.

---

# 58. Создание coverage

Для one-time создаётся только `AttendanceCoverage(one_time_entitlement=...)`.

Для monthly/makeup создаются:

```text
AttendanceCoverage(subscription_allowance=..., makeup_entitlement=optional)
SubscriptionLedgerEntry(CONSUME, -1, allowance=..., coverage=...)
```

---

# 59. Если покрытия нет

Attendance остаётся `PRESENT`; coverage отсутствует; генерируется `AttendanceUncovered`.

---

# 60. Почему необходимо блокировать SubscriptionAllowance row

Все writers ledger сначала блокируют **allowance**, а не родительский Subscription. Это позволяет параллельно и безопасно расходовать независимые ICE/HALL balances, не допуская отрицательного остатка одной категории.

---

# 61. subscriptions.services.reverse_attendance_coverage()

Для active Coverage:

- allowance-backed: lock allowance, создать `RESTORE +1`, затем reversed;
- one-time: ledger не меняется, Coverage становится reversed;
- makeup автоматически снова доступен после reversal.

---

# 62. subscriptions.services.adjust_allowance()

```python
adjust_allowance(*, allowance_id: UUID, delta: int, reason: str, actor: User)
```

Lock allowance, проверить `new_balance >= 0`, создать `ADJUSTMENT`. Ручного изменения числового остатка нет.

---

# 63. subscriptions.services.cancel_subscription()

Cancellation относится к Subscription целиком и запрещает создание новых Coverage по всем его allowances. Исторические ledger entries не меняются.
'''

DJANGO_65_69 = r'''# 65. attendance.services.verify_medical_absence()

При VERIFIED определяется конкретный `SubscriptionAllowance` категории исходного Lesson, который мог покрыть пропуск. Если allowance найден, создаётся MakeupEntitlement на него. Если подходящего allowance нет, justification остаётся VERIFIED, но entitlement не создаётся.

---

# 66. Создание medical MakeupEntitlement

```text
source_subscription_allowance = найденный allowance
category = allowance.category
valid_from / valid_until = medical makeup policy
```

`GRANT +1` не создаётся.

---

# 67. attendance.services.reject_medical_absence()

Логика остаётся без изменений: `PENDING → REJECTED` с audit event.

---

# 68. subscriptions.services.grant_administrative_makeup()

Service принимает `source_subscription_allowance_id`; entitlement не увеличивает allowance balance и обязательно фиксирует actor/reason.

---

# 69. subscriptions.services.rebind_attendance_coverage()

Service перепривязывает Coverage к другому допустимому entitlement. Для allowance→allowance: старый allowance `RESTORE +1`, старый Coverage reversed, новый Coverage + `CONSUME -1`. Для one-time веток ledger не создаётся/не восстанавливается без необходимости.
'''

DJANGO_71_74 = r'''# 71. Uncovered attendance report

`Attendance.status=PRESENT` и нет active `AttendanceCoverage`.

---

# 72. Expired unused report

Отчёт агрегирует positive balances по `SubscriptionAllowance` истёкших Subscription и показывает ICE/HALL отдельно, плюс доступные MakeupEntitlement.

---

# 73. Admin protection

Read-only через обычный Admin: `Attendance`, `AttendanceCoverage`, `SubscriptionLedgerEntry`, lifecycle fields justification/lesson. Изменения выполняются application services.

---

# 74. Django Admin — SubscriptionLedgerEntry

`list/view` разрешены, `add/change/delete` запрещены. Записи создают только `issue_subscription()`, `adjust_allowance()`, `assign_attendance_coverage()` и `reverse_attendance_coverage()`.
'''

DJANGO_76_89 = r'''# 76. Domain events

Помимо Lesson/Attendance events используются:

```text
SubscriptionIssued
SubscriptionActivated
SubscriptionAllowanceConsumed
SubscriptionAllowanceRestored
SubscriptionAllowanceExhausted
SubscriptionExpired
SubscriptionExpiredWithUnusedBalance
SubscriptionCancelled
OneTimeEntitlementGranted
OneTimeEntitlementUsed
MakeupEntitlementGranted
MakeupEntitlementUsed
MakeupEntitlementCancelled
MakeupEntitlementExpired
```

---

# 77. Event payload

Payload содержит идентификаторы `subscription_id`, `allowance_id`, `coverage_id`, category и delta по необходимости, но не OAuth/session/medical contents.

---

# 78. correlation_id

Все события одной операции `Attendance → Coverage → Ledger` используют общий correlation_id.

---

# 79. Transaction boundary

В одной `transaction.atomic()` фиксируются Attendance, AttendanceCoverage, Ledger и Audit. Ошибка ledger откатывает allowance-backed Coverage и Attendance transition.

---

# 80. External side effects

Уведомления выполняются через `transaction.on_commit()`.

---

# 81. Никаких Django signals для core business logic

Signals не создают Coverage, CONSUME/RESTORE, MakeupEntitlement и не меняют Lesson lifecycle.

---

# 82. Selectors

Основные subscription selectors: `allowance_balance()`, `subscription_balances()`, `get_eligible_allowances()`, `get_available_one_time_entitlements()`, `get_available_makeups()`.

---

# 83. Основной student schedule query

Без изменений; дополнительно может prefetch entitlement/coverage summary для UI.

---

# 84. Coach schedule

Без изменений.

---

# 85. Обязательные transaction tests

Обязательно проверяются параллельные списания последнего visit одного allowance, независимые параллельные ICE/HALL allowances, one-time priority и идемпотентность Coverage.

---

# 86. Критические DB invariants

PostgreSQL гарантирует уникальность plan/category и subscription/category allowances, один active Coverage на Attendance/OneTime/Makeup, один GRANT на allowance, один CONSUME/RESTORE на Coverage и корректный знак delta.

---

# 87. Критические service invariants

Service layer гарантирует category match, entitlement ownership, valid date, positive allowance balance, one-time priority, отсутствие TRIAL_HALL, отсутствие отрицательных balances и запрет изменения CLOSED attendance без reopen.

---

# 88. Итоговая схема

```text
SubscriptionPlan
 └── SubscriptionPlanAllowance
          │ snapshot
          ▼
Subscription
 └── SubscriptionAllowance ──► SubscriptionLedgerEntry
                ▲
                │
Attendance ──► AttendanceCoverage
                │
                ├── SubscriptionAllowance (+ optional MakeupEntitlement)
                └── OneTimeEntitlement
```

---

# 89. Источники истины

```text
Факт посещения: Attendance
Планируемое посещение: LessonResponse
Состав занятия: LessonRosterEntry
Состав тарифа: SubscriptionPlanAllowance
Выданные лимиты: SubscriptionAllowance
Остаток ICE/HALL: SUM(Ledger.delta) по allowance
Покрытие посещения: active AttendanceCoverage
Разовое/пробное/индивидуальное право: OneTimeEntitlement
Исключительное продление: MakeupEntitlement
```
'''

SPEC_16_29 = r'''# 16. Абонементы и тарифные права

Месячный `Subscription` представляет один купленный план и может содержать независимые лимиты ICE/HALL. Разовые, пробные, индивидуальные и мини-групповые занятия представлены отдельными one-time entitlements.

---

# 17. Текущая тарифная матрица

| План | ICE | HALL |
|---|---:|---:|
| 4 зала | 0 | 4 |
| 8 льдов | 8 | 0 |
| 8 льдов + 8 залов | 8 | 8 |
| 8 льдов + 12 залов | 8 | 12 |
| 12 льдов | 12 | 0 |
| 12 льдов + 8 залов | 12 | 8 |
| 12 льдов + 12 залов | 12 | 12 |
| 12 льдов + 16 залов | 12 | 16 |
| 16 льдов + 16 залов | 16 | 16 |
| 20 льдов + 16 залов | 20 | 16 |
| 20 льдов + 20 залов | 20 | 20 |
| 20 льдов + 24 зала | 20 | 24 |
| 24 льда + 24 зала | 24 | 24 |

Все текущие планы действуют один месяц.

---

# 18. SubscriptionPlan и allowances

`SubscriptionPlan` содержит 1–2 `SubscriptionPlanAllowance(category, visit_limit)`. При выдаче они snapshot-копируются в `SubscriptionAllowance` конкретного Subscription.

---

# 19. Срок действия

Конкретный Subscription хранит явные `valid_from/valid_until`. Business policy определяет, как рассчитывается один месяц; исторические даты не пересчитываются.

---

# 20. Категории занятий

`ICE` покрывает лёд. `HALL` покрывает ОФП и хореографию. Смешанный план хранит оба лимита независимо.

---

# 21. Остаток

Остаток считается по каждому `SubscriptionAllowance` как сумма immutable ledger entries. Общего остатка смешанного Subscription нет.

---

# 22. Журнал операций

Ledger types: `GRANT`, `CONSUME`, `RESTORE`, `ADJUSTMENT`; каждая запись относится к конкретному allowance.

---

# 23. OneTimeEntitlement

Поддерживаются `SINGLE_ICE`, `SINGLE_HALL`, `INDIVIDUAL_ICE`, `MINI_GROUP_ICE`, `TRIAL_ICE`. Пробного HALL нет. One-time entitlement привязан к конкретному Lesson и имеет приоритет над monthly allowance.

---

# 24. AttendanceCoverage

Coverage связывает `Attendance=PRESENT` с `SubscriptionAllowance` либо `OneTimeEntitlement`; при медицинском/административном переносе дополнительно указывает `MakeupEntitlement`.

---

# 25. Выбор покрытия

Порядок: exact one-time → target makeup → other makeup → ordinary allowance (earliest expiry) → uncovered.

---

# 26. Важное правило даты

Обычный allowance проверяется относительно даты Lesson. Makeup может расширить допустимый срок конкретного source allowance.

---

# 27. Отсутствие покрытия

Отсутствие entitlement не мешает тренеру отметить реальное `PRESENT`; запись попадает в uncovered report.

---

# 28. Исправление ошибки тренера

`PRESENT → ABSENT` обращает именно активный Coverage: allowance-backed создаёт `RESTORE +1`, one-time освобождает entitlement, makeup снова становится доступным.

---

# 29. Защита от двойного списания

Один Attendance имеет не более одного активного Coverage. Ledger writer блокирует конкретный `SubscriptionAllowance`, поэтому баланс категории не может уйти ниже нуля при конкурентных отметках.
'''

SPEC_33 = r'''# 33. Экран абонемента пользователя

Для смешанного плана UI показывает независимые остатки:

```text
12 ICE + 16 HALL
Действует: 01.09–30.09

Лёд:  использовано 8, осталось 4
Зал:  использовано 10, осталось 6
```

Отдельно могут отображаться доступные one-time и makeup entitlements.
'''

SPEC_38_41 = r'''# 38. Django Admin

Регистрируются также `SubscriptionPlanAllowance`, `SubscriptionAllowance`, `OneTimeEntitlement`, `AttendanceCoverage`, `MakeupEntitlement` и ledger entries.

---

# 39. Работа с абонементами в Admin

Администратор выдаёт Subscription по plan, видит ICE/HALL allowances отдельно и выполняет корректировки только через `ADJUSTMENT` конкретного allowance. AttendanceCoverage и Ledger read-only для прямого редактирования.

---

# 40. Статусы абонемента

`UPCOMING/ACTIVE/EXPIRED/CANCELLED` относятся к Subscription. `AVAILABLE/EXHAUSTED` вычисляются отдельно для каждого allowance. Смешанный Subscription считается полностью exhausted только когда исчерпаны все allowances.

---

# 41. Основные Django-модели

К базовому набору относятся:

```text
SubscriptionPlan
SubscriptionPlanAllowance
Subscription
SubscriptionAllowance
OneTimeEntitlement
MakeupEntitlement
AttendanceCoverage
SubscriptionLedgerEntry
```
'''

SPEC_62_64 = r'''# 62. Денежные данные

В MVP payment transaction может отсутствовать, но entitlement-модель уже отражает реальные услуги. Attendance не является платёжной записью.

---

# 63. Будущая интеграция оплаты

Будущий billing context содержит catalog/order/payment/refund. Продукт может создать Subscription, OneTimeEntitlement либо add-on charge без entitlement.

Индивидуальное занятие тарифицируется как `INDIVIDUAL_ICE + STUDENT_ICE_ADMISSION + COACH_ICE_ADMISSION`. Мини-группа — по текущему правилу те же компоненты на каждого участника. Только lesson component создаёт entitlement.

Пробный ICE стоит 50% обычного разового ICE; пробного HALL нет. `SKATE_RENTAL` является add-on и не влияет на Attendance/allowance.

---

# 64. Возможное будущее развитие

Можно добавить online payment/refund, заморозку, перенос остатка, no-show policy, семейные продукты и pricing rules без изменения источника истины Attendance и allowance ledger.
'''

SPEC_66_71 = r'''# 66. Критические бизнес-инварианты

1. Один Attendance на Student+Lesson.
2. Один active AttendanceCoverage на Attendance.
3. RSVP не расходует entitlement.
4. ICE и HALL balances независимы.
5. One-time entitlement имеет приоритет над monthly allowance для своего Lesson.
6. `PRESENT → ABSENT` обращает именно использованное покрытие.
7. Attendance может быть PRESENT без покрытия.
8. Ledger immutable; ручная корректировка — `ADJUSTMENT` allowance.
9. Makeup не увеличивает купленный лимит.
10. Отмена Lesson сама по себе не создаёт CONSUME.

---

# 67. Транзакционность списания

```text
BEGIN
set Attendance=PRESENT
find exact one-time / makeup / ordinary allowance
if allowance-backed: LOCK SubscriptionAllowance, verify balance, CONSUME -1
create AttendanceCoverage
write AuditEvent
COMMIT
```

При ошибке вся операция rollback.

---

# 68. Расчёт баланса

```text
allowance_balance = SUM(SubscriptionLedgerEntry.delta WHERE allowance_id=...)
```

Для смешанного тарифа UI агрегирует словарь `{ICE, HALL}`, а не одно число.

---

# 69. Работа с просроченным остатком

После `valid_until` positive balance остаётся исторически видимым, но обычное использование запрещено. Makeup может разрешить использовать конкретный source allowance позже.

---

# 70. Первичный сценарий создания абонемента

Выдача плана `8 ICE + 12 HALL` создаёт один Subscription, два SubscriptionAllowance и два GRANT: `ICE +8`, `HALL +12`.

---

# 71. Пример полного жизненного цикла

Посещение ICE создаёт `CONSUME -1` только в ICE allowance; HALL остаётся неизменным. Исправление создаёт `RESTORE +1` в тот же allowance. Разовое/пробное посещение с OneTimeEntitlement monthly ledger не меняет.
'''

SPEC_76 = r'''# 76. Тестирование

Обязательны tests для mixed plans, независимых ICE/HALL balances, category mismatch, one-time priority, отсутствия TRIAL_HALL, medical source allowance, reversal, concurrent last-visit consumption и plan snapshot immutability.
'''

SPEC_78_80 = r'''# 78. Предлагаемая последовательность разработки

После scheduling/attendance реализуются allowance-based subscriptions, immutable ledger, coverage engine, one-time entitlements и makeup flow; затем Admin/UI и будущий billing integration boundary.

---

# 79. Главный архитектурный принцип

```text
LessonResponse = намерение
Attendance = фактический приход
AttendanceCoverage = чем покрыт факт
SubscriptionAllowance / OneTimeEntitlement = право посещения
Billing = деньги (отдельный контекст)
```

Эти понятия не подменяют друг друга.

---

# 80. Итоговая рекомендуемая архитектура

```text
Django
├── Scheduling / RSVP
├── Attendance
├── Entitlements
│   ├── SubscriptionPlanAllowance
│   ├── SubscriptionAllowance + Ledger
│   ├── OneTimeEntitlement
│   ├── MakeupEntitlement
│   └── AttendanceCoverage
├── Django Admin
└── future Billing

PostgreSQL = source of truth
```
'''


DOMAIN_70 = r'''# 70. Главный принцип модели

Система независимо отвечает на вопросы:

```text
Кто управляет расписанием?          StudentAccess
Кто планирует прийти?               LessonResponse
Кто фактически пришёл?              Attendance
Каким правом покрыто посещение?      AttendanceCoverage
Каков остаток ICE/HALL?              SubscriptionAllowance + Ledger
Есть ли разовое право?               OneTimeEntitlement
Есть ли исключительное продление?   MakeupEntitlement
```

Разделение авторизации, планирования, факта посещения, entitlement и денег сохраняется при дальнейшем развитии системы.
'''

STATE_81_83 = r'''# 81. Итоговая модель процесса

```text
утром: LessonPublished → RSVP
решение школы: CONFIRMED / CANCELLED / replacement
занятие: Coach → Attendance
Attendance=PRESENT → FindCoverage
    → OneTimeEntitlement
    → либо SubscriptionAllowance (+ optional MakeupEntitlement)
    → либо UNCOVERED
после занятия: LessonAttendanceSubmitted → CLOSED
```

---

# 82. Реализация domain events

Event Sourcing не требуется. Application service в одной транзакции меняет Attendance, создаёт AttendanceCoverage, при необходимости LedgerEntry и AuditEvent. Core business logic не размещается в Django signals.

---

# 83. Архитектурное решение

Предметная область разделена на Scheduling, Attendance, Entitlements и будущий Billing. Entitlements включают allowance-based subscriptions, one-time rights и makeup exceptions; Billing отвечает только за деньги/возвраты.
'''

DJANGO_27 = r'''# 27. Запрещённая архитектура

Нельзя выполнять финансово-учётную цепочку внутри `Attendance.save()` или Django signals.

Критическая операция должна быть явно видна в application service:

```text
Attendance
→ AttendanceCoverage
→ optional SubscriptionLedgerEntry
→ AuditEvent
```

One-time coverage не создаёт ledger entry; allowance-backed coverage создаёт `CONSUME/RESTORE` только через service layer.
'''


DJANGO_90 = r'''# 90. Рекомендуемый следующий этап реализации

После утверждения консолидированной модели реализация идёт в порядке: accounts/scheduling → Attendance → allowance-based subscriptions + ledger → AttendanceCoverage + concurrency tests → OneTimeEntitlement → Makeup flow → Admin/UI → future Billing.

Критическую цепочку `Attendance → AttendanceCoverage → optional Ledger` необходимо покрыть transaction tests до разработки финансового UI.
'''

REPLACEMENTS = {
    'domain': [
        ('# 32. Абонементы', '# 59. Центральные Aggregate Roots', DOMAIN_32_58),
        ('# 59. Центральные Aggregate Roots', '# 63. Пример взрослого ученика', DOMAIN_59_62),
        ('# 66. Пример списания льда', '# 70. Главный принцип модели', DOMAIN_66_69),
        ('# 70. Главный принцип модели', '', DOMAIN_70),
    ],
    'state': [
        ('# 17. Перенос по инициативе школы и абонемент', '# 18. COMPLETED', STATE_17),
        ('# 32. Subscription state machine', '# 42. Компенсация без возврата денег', STATE_32_41),
        ('# 55. MakeupEntitlement', '# 67. Школьный перенос', STATE_55_66),
        ('# 69. Domain Events — Lesson', '# 75. Финансовый отчёт руководителя', STATE_69_74),
        ('# 76. Отчёт по абонементам', '# 79. Какие данные справки видит тренер', STATE_76_78),
        ('# 80. Финальные domain invariants', '# 81. Итоговая модель процесса', STATE_80),
        ('# 81. Итоговая модель процесса', '', STATE_81_83),
    ],
    'django': [
        ('# 20. SubscriptionPlan', '# 25. AuditEvent', DJANGO_20_24),
        ('# 26. Что PostgreSQL не может корректно проверить обычным CheckConstraint', '# 27. Запрещённая архитектура', DJANGO_26),
        ('# 27. Запрещённая архитектура', '# 28. Общий принцип Application Services', DJANGO_27),
        ('# 37. Entitlements при переносе школы', '# 38. scheduling.services.add_lesson_enrollment()', DJANGO_37),
        ('# 49. subscriptions.selectors.subscription_balance()', '# 64. attendance.services.declare_medical_absence()', DJANGO_49_63),
        ('# 65. attendance.services.verify_medical_absence()', '# 70. financial/selectors.py', DJANGO_65_69),
        ('# 71. Uncovered attendance report', '# 75. Django Admin — Attendance', DJANGO_71_74),
        ('# 76. Domain events', '# 90. Рекомендуемый следующий этап реализации', DJANGO_76_89),
        ('# 90. Рекомендуемый следующий этап реализации', '', DJANGO_90),
    ],
    'spec': [
        ('# 16. Абонементы', '# 30. Отмена занятия', SPEC_16_29),
        ('# 33. Экран абонемента пользователя', '# 34. Переключение между детьми', SPEC_33),
        ('# 38. Django Admin', '# 42. Идентификаторы', SPEC_38_41),
        ('# 62. Денежные данные', '# 65. Что сознательно не входит в MVP', SPEC_62_64),
        ('# 66. Критические бизнес-инварианты', '# 72. Основные URL', SPEC_66_71),
        ('# 76. Тестирование', '# 77. Acceptance Criteria MVP', SPEC_76),
        ('# 78. Предлагаемая последовательность разработки', '', SPEC_78_80),
    ],
}

def apply_replacements(key: str, text: str) -> str:
    for start, end, new in REPLACEMENTS[key]:
        if end:
            text = replace_between(text, start, end, new)
        else:
            a = text.find(start)
            if a < 0:
                raise RuntimeError(f'Heading not found: {start!r}')
            text = text[:a] + new.rstrip() + '\n'
    return text

FORBIDDEN = [
    'SubscriptionUsage',
    'category_snapshot',
    'visit_limit_snapshot',
    'source_subscription_id',
    'source_subscription =',
    'subscriptions.selectors.subscription_balance()',
    'subscriptions.services.adjust_subscription()',
]

def check_consolidated(contents: dict[str,str]) -> list[str]:
    errors=[]
    for key in ('domain','state','django','spec'):
        for term in FORBIDDEN:
            if term in contents[key]: errors.append(f'{key}: obsolete term remains: {term}')
    required = ['SubscriptionPlanAllowance','SubscriptionAllowance','AttendanceCoverage','OneTimeEntitlement']
    for key in ('domain','state','django','spec'):
        for term in required:
            if term not in contents[key]: errors.append(f'{key}: required term missing: {term}')
    return errors

def main():
    ap=argparse.ArgumentParser(description='Build/apply tariff-model consolidation patch for Ice-school-schedule docs.')
    ap.add_argument('--apply', action='store_true', help='Write consolidated documents in place. Default: print unified diff.')
    ap.add_argument('--force', action='store_true', help='Allow source files whose Git blob SHA differs from the current-main baseline.')
    ap.add_argument('--output', help='Write unified diff to this file instead of stdout.')
    args=ap.parse_args()

    missing=[str(p) for p in FILES.values() if not p.exists()]
    if missing:
        raise SystemExit('Run from repository root. Missing: '+', '.join(missing))

    raw={k: p.read_bytes() for k,p in FILES.items()}
    def git_blob_sha(data: bytes) -> str:
        import hashlib
        return hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
    mismatches=[]
    for key,data in raw.items():
        actual=git_blob_sha(data)
        expected=EXPECTED_SHA[key]
        if actual != expected:
            mismatches.append(f'{FILES[key]}: expected {expected}, got {actual}')
    if mismatches and not args.force:
        raise SystemExit('Source documentation differs from the main-branch baseline used for this patch.\n'
                         + '\n'.join(mismatches)
                         + '\nRebase/review the consolidation first, or rerun with --force after manual verification.')

    original={k: raw[k].decode('utf-8') for k in FILES}
    changed=original.copy()
    for key in ('domain','state','django','spec'):
        changed[key]=apply_replacements(key, original[key])

    errors=check_consolidated(changed)
    if errors:
        raise SystemExit('Consolidation validation failed:\n- '+'\n- '.join(errors))

    diffs=[]
    for key in ('domain','state','django','spec'):
        path=str(FILES[key]).replace('\\','/')
        d=difflib.unified_diff(original[key].splitlines(True), changed[key].splitlines(True), fromfile='a/'+path, tofile='b/'+path, n=3)
        diff=''.join(d)
        if diff:
            diffs.append('diff --git a/{0} b/{0}\n'.format(path)+diff)
    patch=''.join(diffs)

    if args.output:
        Path(args.output).write_text(patch, encoding='utf-8')
    elif not args.apply:
        sys.stdout.write(patch)

    if args.apply:
        for key in ('domain','state','django','spec'):
            FILES[key].write_text(changed[key], encoding='utf-8')
        print('Consolidation applied to 4 docs.')
        if args.output:
            print(f'Unified diff written to {args.output}')

if __name__ == '__main__':
    main()
