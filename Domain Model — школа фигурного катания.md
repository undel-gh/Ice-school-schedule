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

# 32. Абонементы

В системе существует две **категории** абонементов:

```text
ICE
HALL
```

### ICE

Покрывает занятия:

```text
Лёд
```

### HALL

Покрывает:

```text
ОФП
Хореография
```

---

# 33. SubscriptionPlan

`SubscriptionPlan` — тип продаваемого/выдаваемого абонемента.

```text
SubscriptionPlan

id

name

category:
    ICE
    HALL

visit_limit

duration_months = 1

is_active
```

Например:

```text
8 занятий на льду
category = ICE
visit_limit = 8
duration_months = 1
```

или:

```text
8 занятий в зале
category = HALL
visit_limit = 8
duration_months = 1
```

---

# 34. Почему количество занятий относится к Plan

Даже если сейчас стандарт:

```text
8 занятий
```

лучше не зашивать цифру `8` в код.

Например позднее могут появиться:

```text
4 занятия
8 занятий
12 занятий
```

при той же категории:

```text
ICE
```

---

# 35. Subscription

Конкретный абонемент конкретного ученика.

```text
Subscription

id: UUID

student_id
plan_id

category_snapshot
visit_limit_snapshot

valid_from
valid_until

cancelled_at nullable

created_at
created_by
```

---

# 36. Snapshot параметров тарифа

В Subscription сохраняются:

```text
category_snapshot
visit_limit_snapshot
```

чтобы изменение `SubscriptionPlan` в будущем не изменяло уже выданные абонементы.

Например:

```text
раньше:
8 занятий

позже тариф изменили:
10 занятий
```

старый абонемент должен остаться:

```text
8 занятий
```

---

# 37. Срок действия

Все текущие абонементы имеют продолжительность:

```text
1 месяц
```

При этом в `Subscription` всё равно сохраняются конкретные:

```text
valid_from
valid_until
```

Эти даты являются источником истины.

Это позволит избежать пересчёта старых абонементов при изменении правил в будущем.

---

# 38. Определение месяца

Для domain model важно не вычислять срок действия «на лету».

При создании Subscription система вычисляет период согласно правилу школы и сохраняет результат:

```text
valid_from
valid_until
```

После создания эти даты не зависят от `SubscriptionPlan`.

Таким образом дальнейшее уточнение:

- календарный месяц;
- месяц с момента покупки;
- месяц с первого занятия

не потребует менять структуру базы.

Для MVP бизнес-правило должно быть одно для всей школы.

---

# 39. Одновременно два типа абонемента

У Student могут одновременно существовать:

```text
ICE subscription

и

HALL subscription
```

Например:

```text
Маша

Лёд:
5 из 8 осталось

Зал:
3 из 8 осталось
```

---

# 40. Последовательные абонементы

У одного Student также может быть несколько абонементов одной категории.

Например:

```text
ICE
01.09–30.09

ICE
01.10–31.10
```

Допускается и техническое пересечение периодов.

Поэтому алгоритм выбора абонемента должен быть детерминированным.

---

# 41. SubscriptionLedgerEntry

Количество оставшихся занятий не должно быть вручную изменяемым числом.

Используется immutable ledger.

```text
SubscriptionLedgerEntry

id

subscription_id

type:
    GRANT
    CONSUME
    RESTORE
    ADJUSTMENT

delta

created_at
created_by_user_id

reason nullable
```

Пример:

```text
+8 GRANT
-1 CONSUME
-1 CONSUME
+1 RESTORE
```

Баланс:

```text
7
```

---

# 42. Начальная выдача

При создании:

```text
Subscription
```

автоматически создаётся:

```text
LedgerEntry

GRANT
+8
```

---

# 43. SubscriptionUsage

Необходимо явно хранить связь:

> какое фактическое посещение было оплачено каким абонементом.

Для этого вводится:

```text
SubscriptionUsage

id

attendance_id
subscription_id

consume_ledger_entry_id

created_at

reversed_at nullable
restore_ledger_entry_id nullable
```

---

# 44. Зачем нужен SubscriptionUsage

Без него имеется только:

```text
Subscription -1
```

но сложно ответить:

> за какое именно занятие было сделано списание?

С `SubscriptionUsage` имеется:

```text
Attendance
14 сентября
Лёд
Маша

       │
       ▼

SubscriptionUsage

       │
       ▼

ICE Subscription #123
```

---

# 45. Ограничение одного списания

Для одного Attendance может существовать только одно активное использование абонемента.

То есть:

```text
Attendance #A
```

не может одновременно списать:

```text
Subscription #1
и
Subscription #2
```

---

# 46. Списание

Списание происходит только когда:

```text
Attendance.status = PRESENT
```

Алгоритм:

```text
Trainer marks PRESENT
        ↓
Determine Lesson subscription category
        ↓
Find eligible Subscription
        ↓
Create SubscriptionUsage
        ↓
Create LedgerEntry CONSUME -1
```

---

# 47. Категория определяется занятием

Например:

```text
LessonType = ICE

subscription_category = ICE
```

значит можно использовать только:

```text
Subscription.category = ICE
```

Для:

```text
PHYSICAL
CHOREOGRAPHY
```

ищется:

```text
HALL
```

---

# 48. Проверка срока

Абонемент должен быть действующим **на дату занятия**, а не на момент, когда тренер поставил отметку.

Например:

```text
Lesson:
30 сентября

Subscription:
до 30 сентября

Тренер отметил:
1 октября
```

абонемент подходит.

---

# 49. Выбор из нескольких абонементов

Если существует несколько подходящих абонементов, используется порядок:

1. абонемент с ближайшим окончанием срока;
2. затем с наиболее ранней датой начала;
3. затем наиболее ранний созданный.

То есть система расходует первым тот абонемент, который раньше закончится.

---

# 50. Остаток

Авторитетный остаток:

```text
SUM(SubscriptionLedgerEntry.delta)
```

Например:

```text
+8
-1
-1
-1
=
5
```

Поле:

```text
remaining_visits
```

не является источником истины.

---

# 51. Исчерпанный абонемент

Если:

```text
balance = 0
```

абонемент больше нельзя использовать.

Однако сама запись Subscription сохраняется в истории.

---

# 52. Истёкший абонемент

Если срок действия закончился, неиспользованные занятия не удаляются из исторического баланса.

Например:

```text
выдано 8
использовано 6
остаток 2
```

История должна показывать:

```text
использовано 6 из 8
```

даже после окончания действия.

Просто эти два оставшихся посещения больше нельзя использовать.

---

# 53. Посещение без абонемента

Отсутствие подходящего абонемента никогда не мешает тренеру отметить:

```text
PRESENT
```

Поскольку Attendance описывает реальность.

Сценарий:

```text
Маша действительно пришла
        ↓
Trainer → PRESENT
        ↓
подходящего Subscription нет
        ↓
Attendance сохраняется
        ↓
SubscriptionUsage не создаётся
```

Такое посещение считается:

```text
UNCOVERED
```

---

# 54. Uncovered Attendance

Это не отдельный статус Attendance.

Он вычисляется:

```text
Attendance = PRESENT
AND
active SubscriptionUsage отсутствует
```

Такой список показывается администратору.

---

# 55. Исправление PRESENT → ABSENT

Было:

```text
Attendance = PRESENT

SubscriptionUsage
Subscription #123

CONSUME -1
```

Тренер исправляет:

```text
Attendance = ABSENT
```

Система создаёт:

```text
RESTORE +1
```

в тот же Subscription.

`SubscriptionUsage` помечается как reversed.

Исходный `CONSUME -1` не удаляется.

---

# 56. Исправление ABSENT → PRESENT

При изменении:

```text
ABSENT
    ↓
PRESENT
```

система снова выполняет обычный алгоритм подбора действующего абонемента.

Важно:

подбор производится относительно даты Lesson.

---

# 57. Переназначение списания

Администратору в будущем можно позволить операцию:

```text
Перенести посещение
с Subscription A
на Subscription B
```

Она должна выглядеть как:

```text
Subscription A:
RESTORE +1

Subscription B:
CONSUME -1
```

Исходные операции не удаляются.

Для MVP специальный UI для этого необязателен.

---

# 58. Derived status Subscription

Необязательно постоянно хранить:

```text
ACTIVE
EXPIRED
EXHAUSTED
```

Эти состояния можно вычислять.

### UPCOMING

```text
lesson_date < valid_from
```

### ACTIVE

```text
date в периоде
AND balance > 0
AND not cancelled
```

### EXHAUSTED

```text
balance <= 0
```

### EXPIRED

```text
date > valid_until
```

### CANCELLED

```text
cancelled_at IS NOT NULL
```

---

# 59. Центральные Aggregate Roots

Domain можно логически разделить на следующие aggregates.

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
 ├── SubscriptionLedgerEntry
 └── SubscriptionUsage
```

---

# 60. Взаимодействие aggregates

Основная цепочка:

```text
Student
   │
   │ membership
   ▼
TrainingGroup
   │
   ▼
Lesson
   │
   ├──── RSVP
   │
   ▼
Attendance
   │
   │ PRESENT
   ▼
SubscriptionUsage
   │
   ▼
Subscription
   │
   ▼
Ledger
```

---

# 61. Полная схема отношений

```text
                       UserAccount
                       /    |    \
                      /     |     \
                     ▼      ▼      ▼
           ExternalIdentity │   CoachProfile
                            │
                            ▼
                      StudentAccess
                            │
                            ▼
                          Student
                         /       \
                        /         \
                       ▼           ▼
              GroupMembership   Subscription
                       │         /       \
                       ▼        ▼         ▼
                TrainingGroup Ledger    Usage
                       │                  ▲
                       ▼                  │
                     Lesson ───────► Attendance
                    /      \
                   ▼        ▼
                RSVP      LessonType
                              │
                              ▼
                   SubscriptionCategory
                       ICE / HALL
```

---

# 62. Ключевые domain invariants

## INV-01

`UserAccount` и `Student` являются разными сущностями.

## INV-02

Один Student может иметь несколько `StudentAccess`.

## INV-03

SELF и GUARDIAN могут менять RSVP, но никогда Attendance.

## INV-04

Attendance может изменять только авторизованный тренер или администратор.

## INV-05

Attendance является источником истины о фактическом посещении.

## INV-06

RSVP не влияет непосредственно на баланс абонемента.

## INV-07

Только `Attendance=PRESENT` может породить `SubscriptionUsage`.

## INV-08

Одно фактическое посещение не может одновременно расходовать два абонемента.

## INV-09

ICE Lesson может расходовать только ICE Subscription.

## INV-10

PHYSICAL и CHOREOGRAPHY расходуют HALL Subscription.

## INV-11

Абонемент должен быть действующим на дату занятия.

## INV-12

Абонемент с нулевым балансом нельзя использовать.

## INV-13

Отсутствие абонемента не препятствует сохранению Attendance.

## INV-14

Изменение PRESENT → ABSENT возвращает ранее списанное занятие.

## INV-15

Ledger entries после создания не редактируются и не удаляются.

## INV-16

Корректировки выполняются компенсирующими операциями.

## INV-17

Изменение SubscriptionPlan не изменяет ранее созданные Subscription.

## INV-18

Изменение ScheduleTemplate не изменяет историю уже проведённых Lesson.

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

У Маши:

```text
ICE Subscription
01.09–30.09
8 занятий

остаток = 5
```

Занятие:

```text
22 сентября
ICE
```

Тренер ставит:

```text
PRESENT
```

Система создаёт:

```text
Attendance
        │
        ▼
SubscriptionUsage
        │
        ▼
ICE Subscription
        │
        ▼
CONSUME -1
```

Новый баланс:

```text
4
```

---

# 67. Пример занятия в зале

Занятие:

```text
CHOREOGRAPHY
```

имеет:

```text
subscription_category = HALL
```

Поэтому расходуется:

```text
HALL Subscription
```

Точно такой же абонемент используется для:

```text
PHYSICAL
```

---

# 68. Что не должно находиться в domain model MVP

На этом этапе я бы не добавлял:

- Payment;
- Invoice;
- банковские данные;
- бонусные счета;
- заморозку;
- перенос занятий;
- no-show штрафы;
- автоматическое продление;
- семейный баланс;
- отдельные медицинские сведения;
- фотографии учеников;
- точные даты рождения.

Это можно добавить позже, не ломая описанную модель.

---

# 69. Рекомендуемый набор Django models

Первая реализация должна содержать примерно следующие модели:

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
Subscription
SubscriptionUsage
SubscriptionLedgerEntry

AuditEvent
```

Всего около 16 основных моделей.

Это всё ещё небольшой domain.

---

# 70. Главный принцип модели

Система должна уметь независимо отвечать на четыре вопроса:

### Кто может управлять расписанием ученика?

```text
StudentAccess
```

### Собирается ли ученик прийти?

```text
LessonResponse
```

### Был ли ученик фактически на занятии?

```text
Attendance
```

### За счёт какого абонемента было оплачено это посещение?

```text
SubscriptionUsage
```

Именно такое разделение позволяет одинаково корректно работать со взрослыми, маленькими детьми и подростками, не смешивая авторизацию, планирование, фактическое посещение и учёт абонементов.