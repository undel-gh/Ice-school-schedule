# Domain Model

**Версия:** 1.0  
**Предметная область:** расписание, планируемое и фактическое посещение, абонементы  
**Технологическая реализация:** Django + PostgreSQL

---

# 1. Основные принципы модели

Система разделяет четыре разных понятия:

1. **UserAccount** — кто вошёл в систему.
2. **Student** — кто фактически занимается.
3. **RSVP** — собирается ли ученик прийти.
4. **Attendance** — пришёл ли ученик фактически.

Ключевой принцип:

> Единственным источником истины о фактическом посещении является отметка тренера.

Ответ ученика или родителя `Буду` / `Не буду` используется только для планирования.

Он не является доказательством посещения и сам по себе не расходует абонемент.

---

# 2. Ученик и пользователь системы — разные сущности

Это критически важно.

Нельзя строить модель:

```text
User = Student
```

потому что существуют как минимум три сценария.

### Взрослый ученик

```text
UserAccount
     │
     │ SELF
     ▼
Student
```

### Маленький ребёнок

```text
UserAccount родителя
     │
     │ GUARDIAN
     ▼
Student ребёнка
```

### Подросток

```text
UserAccount родителя ── GUARDIAN ──┐
                                   │
                                   ▼
                               Student
                                   ▲
                                   │
UserAccount подростка ─── SELF ────┘
```

Таким образом один `Student` может быть доступен из нескольких учётных записей.

---

# 3. UserAccount

`UserAccount` представляет пользователя информационной системы.

Это может быть:

- взрослый ученик;
- родитель;
- подросток;
- тренер;
- администратор.

Пример модели:

```text
UserAccount

id: UUID
is_active
is_staff
created_at
last_login
```

Профильные данные VK или Яндекс здесь хранить не требуется.

---

# 4. ExternalIdentity

Связывает внутреннюю учётную запись с внешним Identity Provider.

```text
ExternalIdentity

id
user_id

provider:
    YANDEX
    VK

provider_subject

created_at
last_used_at
```

Ограничение:

```text
UNIQUE(provider, provider_subject)
```

Один пользователь потенциально может иметь несколько способов входа:

```text
UserAccount
   ├── VK
   └── Yandex
```

При этом `UserAccount` остаётся одной внутренней учётной записью.

---

# 5. Student

`Student` — человек, который фактически занимается.

Минимальная модель:

```text
Student

id: UUID
display_name
is_active

created_at
```

Примеры:

```text
Анна
Маша К.
Александр
```

Для работы расписания не требуется обязательно хранить:

- полный ФИО;
- телефон;
- email;
- дату рождения;
- домашний адрес;
- фотографию.

---

# 6. Нужно ли хранить возраст ребёнка

Для работы самой системы точная дата рождения не требуется.

В частности, не нужно определять доступ таким образом:

```text
если age >= 14 → разрешить login
```

Право подростка самостоятельно работать с расписанием определяется наличием соответствующей связи `StudentAccess`.

То есть:

```text
подростку выдали собственный доступ
               ↓
он может управлять RSVP
```

без необходимости хранить его дату рождения.

---

# 7. StudentAccess

Связывает пользователя с учеником.

```text
StudentAccess

id
user_id
student_id

role:
    SELF
    GUARDIAN

is_active

created_at
```

Уникальность:

```text
UNIQUE(user_id, student_id)
```

---

# 8. SELF

Используется для:

- взрослого ученика;
- подростка с собственной учётной записью.

Например:

```text
Иван / UserAccount

SELF
 ↓

Student «Иван»
```

---

# 9. GUARDIAN

Используется для родителя или иного законного представителя.

```text
Мама / UserAccount

GUARDIAN
   ↓

Student «Маша»
```

Один родитель может управлять несколькими учениками:

```text
UserAccount

 ├── GUARDIAN → Маша
 └── GUARDIAN → Петя
```

---

# 10. Одновременный доступ подростка и родителя

Разрешается следующий сценарий:

```text
Student «Маша»

← SELF ← аккаунт Маши

← GUARDIAN ← аккаунт мамы

← GUARDIAN ← аккаунт папы
```

Все три пользователя могут видеть расписание Маши и отвечать:

```text
Буду
Не буду
```

---

# 11. Конфликт между ответами подростка и родителя

Для RSVP используется правило:

> последнее изменение является актуальным.

Например:

```text
15:30 Маша:
Буду

18:20 мама:
Не буду
```

Итоговый RSVP:

```text
НЕ БУДУ
```

При этом сохраняются:

```text
updated_at
updated_by
```

поэтому система знает, кем было сделано последнее изменение.

Для MVP отдельную полную историю RSVP хранить необязательно.

---

# 12. Права StudentAccess

В первой версии роли `SELF` и `GUARDIAN` имеют одинаковые права в отношении расписания:

- видеть расписание;
- ставить RSVP;
- менять RSVP;
- видеть остаток абонемента;
- видеть историю посещений.

Они **не могут изменять Attendance**.

---

# 13. Coach

Тренер также имеет `UserAccount`.

Дополнительная сущность:

```text
CoachProfile

id
user_id
display_name
is_active
```

Связь:

```text
UserAccount
     │
     ▼
CoachProfile
```

---

# 14. TrainingGroup

Учебная группа.

```text
TrainingGroup

id
name
is_active
```

Например:

```text
Начальная 1
Начальная 2
Спортивная
Взрослые
```

---

# 15. GroupMembership

Членство ученика в группе обязательно должно быть историческим.

```text
GroupMembership

id
student_id
group_id

starts_on
ends_on

created_at
```

`ends_on = NULL` означает действующее членство.

Это лучше, чем простое:

```text
Student.group_id
```

поскольку ученик со временем может переходить между группами.

---

# 16. Несколько групп

Domain model допускает, что один Student одновременно состоит в нескольких группах.

Например:

```text
Маша

├── Спортивная группа
└── Дополнительная хореография
```

---

# 17. LessonType

Физический тип занятия:

```text
LessonType

id
code
name
subscription_category
```

На старте:

```text
ICE
Лёд

PHYSICAL
ОФП

CHOREOGRAPHY
Хореография
```

---

# 18. SubscriptionCategory

Отдельно от типа занятия вводится категория списания абонемента:

```text
ICE
HALL
```

Связь:

```text
ICE
    → ICE

PHYSICAL
    → HALL

CHOREOGRAPHY
    → HALL
```

То есть ОФП и хореография могут быть разными видами занятий с точки зрения расписания, но используют один тип абонемента.

Это важное разделение.

Не следует писать бизнес-логику вида:

```python
if lesson.type in ["PHYSICAL", "CHOREOGRAPHY"]:
    ...
```

Вместо этого используется:

```text
lesson.lesson_type.subscription_category
```

---

# 19. Venue

Место проведения занятия.

```text
Venue

id
name
address_optional
is_active
```

Например:

```text
Каток №1
Зал №2
```

Для domain logic адрес не является обязательным.

---

# 20. ScheduleTemplate

Представляет повторяющееся расписание.

```text
ScheduleTemplate

id

group_id
lesson_type_id
coach_id
venue_id

weekday
start_time
duration_minutes

valid_from
valid_until

is_active
```

Пример:

```text
Начальная 1
Лёд
Анна
Вторник
18:00
60 минут
```

---

# 21. Lesson

`Lesson` — конкретное занятие.

Это одна из центральных сущностей системы.

```text
Lesson

id: UUID

source_template_id nullable

group_id
lesson_type_id
coach_id
venue_id

starts_at
ends_at

status

created_at
```

---

# 22. Lesson является snapshot

После создания конкретного занятия его параметры считаются самостоятельными.

Например, шаблон:

```text
Вторник 18:00
Тренер Анна
```

создал:

```text
22 сентября 18:00
Анна
```

После этого администратор изменил шаблон:

```text
Тренер Ольга
```

занятие 22 сентября не должно автоматически превратиться в занятие Ольги.

Изменения шаблона применяются только согласно логике генерации будущих занятий.

---

# 23. Участники Lesson

Основной список потенциальных участников определяется через:

```text
Lesson.group
       +
GroupMembership
```

на дату занятия.

Однако `Attendance` не должен технически требовать действующего `GroupMembership`.

Это позволит в будущем поддерживать:

- гостевое посещение;
- пробное занятие;
- временный переход в другую группу;
- отработку занятия с другой группой.

---

# 24. LessonResponse

Предварительное намерение посетить занятие.

```text
LessonResponse

id

lesson_id
student_id

status:
    YES
    NO

updated_at
updated_by_user_id
```

Ограничение:

```text
UNIQUE(lesson_id, student_id)
```

Если записи нет:

```text
NO_RESPONSE
```

---

# 25. RSVP не является посещением

Ни один из вариантов:

```text
YES
NO
NO_RESPONSE
```

не влияет непосредственно на абонемент.

Например:

```text
RSVP = YES
```

не означает:

```text
Attendance = PRESENT
```

и не вызывает:

```text
-1 занятие
```

---

# 26. Attendance

Фактическое посещение.

```text
Attendance

id: UUID

lesson_id
student_id

status:
    PRESENT
    ABSENT

marked_at
marked_by_user_id

updated_at
updated_by_user_id
```

Ограничение:

```text
UNIQUE(lesson_id, student_id)
```

---

# 27. UNMARKED

Отдельное состояние `UNMARKED` в БД хранить необязательно.

Отсутствие `Attendance` означает:

```text
UNMARKED
```

Таким образом:

```text
нет записи       → не отмечено
PRESENT          → пришёл
ABSENT           → отсутствовал
```

---

# 28. Источник истины посещения

При расхождении:

```text
RSVP = NO
Attendance = PRESENT
```

правильный ответ:

> ученик присутствовал.

При:

```text
RSVP = YES
Attendance = ABSENT
```

правильный ответ:

> ученик отсутствовал.

Для любой статистики фактической посещаемости используется только `Attendance`.

---

# 29. Кто имеет право менять Attendance

В обычном сценарии:

```text
назначенный тренер
```

Также это может делать администратор.

Student, SELF и GUARDIAN никогда не могут изменять Attendance.

---

# 30. Исправление тренером

Тренер может исправить ошибку:

```text
PRESENT
   ↓
ABSENT
```

или:

```text
ABSENT
   ↓
PRESENT
```

Такая операция должна автоматически синхронизировать списание абонемента.

---

# 31. AuditEvent

Изменения фактического посещения желательно дополнительно отражать в immutable audit log.

```text
AuditEvent

id
actor_user_id
event_type
object_type
object_id

created_at

metadata
```

Например:

```text
ATTENDANCE_MARKED_PRESENT
ATTENDANCE_CHANGED_TO_ABSENT
ATTENDANCE_CHANGED_TO_PRESENT
```

---

# 32. Абонементы и категории посещений

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

---

# 59. Центральные Aggregate Roots

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

---

# 63. Пример взрослого ученика

```text
UserAccount #10
        │
       SELF
        │
        ▼
Student «Алексей»

Group:
Взрослые

Subscriptions:

ICE
5/8

HALL
7/8
```

Алексей самостоятельно отвечает за своё расписание.

---

# 64. Пример маленького ребёнка

```text
UserAccount мамы
        │
     GUARDIAN
        │
        ▼
Student «Маша»
```

У Маши собственного UserAccount нет.

Мама:

- видит расписание;
- ставит `Буду/Не буду`;
- видит остатки абонементов.

Тренер отмечает фактическое присутствие.

---

# 65. Пример подростка

```text
                Student «Катя»
                 ▲         ▲
                 │         │
              SELF      GUARDIAN
                 │         │
                 │         │
        аккаунт Кати    аккаунт мамы
```

Обе учётные записи могут менять RSVP.

Последнее изменение является актуальным.

При этом ни Катя, ни мама не определяют фактическое посещение.

Его определяет тренер.

---

# 66. Пример списания льда

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

---

# 70. Главный принцип модели

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
