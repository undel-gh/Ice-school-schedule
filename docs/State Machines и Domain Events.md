# State Machines и Domain Events

**Версия:** 1.0  
**Область:** Lesson, Attendance, Subscription, переносы и подтверждённые отсутствия

---

# 1. Основные бизнес-правила

Система различает четыре факта:

```text
RSVP
Планирует ли ученик прийти?

Attendance
Пришёл ли ученик фактически?

Subscription
Есть ли у ученика право посетить занятие?

MakeupEntitlement
Есть ли исключение, позволяющее использовать
оставшееся занятие за пределами обычного срока?
```

Главным источником истины о фактическом посещении является:

```text
Attendance
```

проставленный тренером.

RSVP не влияет непосредственно на количество занятий в абонементе.

---

# 2. Общая схема

```text
                    Lesson
                       │
           ┌───────────┴──────────┐
           │                      │
          RSVP                Attendance
    Буду / Не буду         Пришёл / Не пришёл
                                  │
                                  │ PRESENT
                                  ▼
                       Subscription coverage
                                  │
                      ┌───────────┴───────────┐
                      │                       │
               обычный абонемент      MakeupEntitlement
                      │                       │
                      └───────────┬───────────┘
                                  ▼
                         CONSUME -1
```

---

# 3. Дополнения к Domain Model

Для реализации описанных процессов необходимо добавить или уточнить следующие сущности.

## Lesson

Дополнительные поля:

```text
minimum_attendees

rsvp_deadline
decision_deadline

published_at

attendance_submitted_at
attendance_submitted_by

cancellation_reason

replacement_lesson_id nullable
```

`minimum_attendees` должен быть snapshot конкретного занятия.

Например:

```text
Группа:
10 человек

minimum_attendees:
4
```

Если позднее правило группы изменится на 5, уже созданное занятие сохраняет старое значение 4.

---

# 4. Lesson state machine

Предлагается следующий автомат:

```text
                     ┌─────────────┐
                     │    DRAFT    │
                     └──┬────┬─────┘
                        │    │
                   cancel   publish
                        │    │
                        ▼    ▼
                 CANCELLED  RSVP_OPEN
                        ▲      │  │  │
                        │      │  │  └─ reschedule → CANCELLED + new DRAFT
                        │      │  └──── cancel ──────→ CANCELLED
                        │      └─────── confirm
                        │                 │
                        │                 ▼
                        │             CONFIRMED
                        │              │  │  │
                        │              │  │  └─ reschedule → CANCELLED + new DRAFT
                        │              │  └──── cancel ──────→ CANCELLED
                        │              └─────── complete
                        │                         │
 DRAFT ─ reschedule ────┘                         ▼
                                   ┌───────────┐
                                   │ COMPLETED │
                                   └─────┬─────┘
                                         │ trainer submits
                                         │ attendance
                                         ▼
                                   ┌───────────┐
                                   │  CLOSED   │
                                   └───────────┘
```

Администратор может выполнить:

```text
CLOSED
   │
   │ reopen
   ▼
COMPLETED
```

для исправления фактической посещаемости.

---

# 5. DRAFT

`DRAFT` означает:

> занятие создано, но участники его ещё не видят как актуальное.

Оно может быть создано:

- из `ScheduleTemplate`;
- вручную администратором;
- как замена перенесённому занятию.

В этом состоянии RSVP невозможен.

Администратор может отменить ещё не опубликованное занятие:

```text
DRAFT → CANCELLED
```

Если у DRAFT есть активный `LessonEnrollment` или `OneTimeEntitlement`,
прямая отмена запрещена: нужно использовать перенос, чтобы бронь могла
перейти на replacement lesson.

Для ещё не сгенерированного occurrence шаблона намеренный пропуск фиксируется
отдельной операцией:

```text
ScheduleTemplate occurrence
        │
        │ skip_template_occurrence
        ▼
    CANCELLED
```

Она создаёт template-owned CANCELLED occurrence напрямую, в том числе когда
его слот уже занят занятием другого типа.

---

# 6. RSVP_OPEN

После публикации:

```text
DRAFT
  ↓
RSVP_OPEN
```

генерируется событие:

```text
LessonPublished
```

Занятие появляется в расписании участников.

Ученик или родитель может ответить:

```text
YES
NO
```

До `rsvp_deadline`.

---

# 7. Публикация расписания утром

Текущий процесс школы хорошо переносится в систему.

Например, утром выполняется:

```text
08:00

Сегодня:

17:00 Лёд
18:15 ОФП
19:00 Хореография
```

Все сегодняшние занятия переходят:

```text
DRAFT → RSVP_OPEN
```

и становятся видимыми участникам.

В дальнейшем публикацию можно выполнять автоматически.

---

# 8. RSVP и минимальное количество участников

Количество желающих:

```text
expected_count =
COUNT(LessonResponse.status = YES)
```

Например:

```text
Всего в группе:       10

Будут:                 2
Не будут:              5
Не ответили:           3

Минимум:               4
```

Получаем:

```text
minimum_met = false
```

---

# 9. Minimum attendance не является состоянием Lesson

Не следует создавать:

```text
LOW_ATTENDANCE
ENOUGH_ATTENDANCE
```

как состояния.

Это вычисляемые условия.

Занятие всё ещё находится:

```text
RSVP_OPEN
```

а система просто показывает предупреждение:

> Минимальное количество участников не набрано.

---

# 10. decision_deadline

Для занятия задаётся время, когда организатору необходимо принять решение.

Например:

```text
Занятие:
18:00

decision_deadline:
15:00
```

Конкретное правило должно быть настраиваемым.

На `decision_deadline` система оценивает RSVP.

---

# 11. Если минимум набран

Например:

```text
minimum = 4
YES = 6
```

генерируется:

```text
LessonMinimumReached
```

Занятие может перейти:

```text
RSVP_OPEN
   ↓
CONFIRMED
```

Я бы в первой версии **не делал автоматическую отмену или перенос**.

Система предлагает решение организатору, но последнее слово остаётся за человеком.

В дальнейшем можно добавить:

```text
auto_confirm_if_minimum_met = true
```

---

# 12. Если минимум не набран

Генерируется:

```text
LessonMinimumNotMet
```

Например:

```text
expected = 2
minimum = 4
```

Администратор получает:

> На занятие 18:00 записалось 2 человека при минимуме 4.

Доступные действия:

```text
[ Всё равно провести ]

[ Отменить ]

[ Перенести ]
```

---

# 13. CONFIRMED

Состояние означает:

> школа приняла решение провести занятие.

RSVP всё ещё можно разрешить менять до:

```text
rsvp_deadline
```

или до начала занятия.

Это отдельное правило от состояния Lesson.

---

# 14. Перенос занятия

Я по-прежнему не рекомендую состояние:

```text
RESCHEDULED
```

Перенос является **операцией над двумя Lesson**.

Например:

```text
Lesson #100
14 сентября 17:00

        ↓ reschedule

Lesson #100
CANCELLED

replacement_lesson = #125
```

и:

```text
Lesson #125
16 сентября 18:00
```

---

# 15. RSVP при переносе

Ответы старого занятия сохраняются для истории:

```text
Маша      YES
Петя      YES
Катя      NO
```

но **не переносятся автоматически** на новое время.

Причина проста:

> согласие прийти в понедельник в 17:00 не означает согласие прийти в среду в 18:00.

У нового Lesson первоначально:

```text
NO_RESPONSE
```

для всех участников.

---

# 16. Причины отмены

У `Lesson` рекомендуется хранить:

```text
cancellation_reason
```

Варианты:

```text
LOW_ATTENDANCE

COACH_UNAVAILABLE

VENUE_UNAVAILABLE

ADMINISTRATIVE

OTHER
```

В дальнейшем это позволит анализировать:

> сколько занятий школа отменила из-за низкой посещаемости.

---

# 17. Перенос по инициативе школы и абонемент

Если школа переносит занятие и replacement выходит за `valid_until` исходного `Subscription`, ученик не должен терять возможность использовать уже купленный остаток только из-за решения школы.

Для участника с `RSVP=YES` определяется allowance той же категории, который мог покрыть исходный Lesson. При необходимости создаётся:

```text
MakeupEntitlement
reason = SCHOOL_RESCHEDULE
source_subscription_allowance = исходный allowance
target_lesson = replacement Lesson
```

Entitlement не добавляет `GRANT +1`: он разрешает использовать существующий положительный остаток исходного allowance на replacement Lesson.

---

# 18. COMPLETED

После окончания занятия:

```text
CONFIRMED
    ↓
COMPLETED
```

Занятие считается состоявшимся.

Тренер должен закончить отметку фактически присутствовавших.

---

# 19. CLOSED

Когда тренер нажимает:

```text
[ Завершить учёт посещаемости ]
```

происходит:

```text
COMPLETED
   ↓
CLOSED
```

и генерируется:

```text
LessonAttendanceSubmitted
```

Именно это событие заменяет текущий процесс:

> тренер передал руководителю школы фактическую посещаемость.

---

# 20. Финансовый источник

Для финансовых расчётов руководитель должен использовать только:

```text
Attendance
```

из:

```text
Lesson.status = CLOSED
```

Не:

```text
RSVP
```

и не промежуточные отметки тренера.

Таким образом:

```text
CLOSED
```

означает:

> тренер подтвердил итоговый список.

---

# 21. Исправление после CLOSED

После закрытия тренер самостоятельно редактировать посещение не должен.

Если обнаружена ошибка:

```text
Администратор:

[ Открыть посещаемость повторно ]
```

получаем:

```text
CLOSED → COMPLETED
```

с событием:

```text
LessonAttendanceReopened
```

После исправления:

```text
COMPLETED → CLOSED
```

и снова:

```text
LessonAttendanceSubmitted
```

Все изменения сохраняются в audit trail.

---

# 22. Attendance state machine

Attendance имеет гораздо более простой автомат.

```text
                UNMARKED
                /      \
               /        \
              ▼          ▼
          PRESENT      ABSENT
              │          │
              └────┬─────┘
                   │ correction
                   │
              PRESENT ↔ ABSENT
```

---

# 23. UNMARKED

Физически отдельную запись:

```text
status = UNMARKED
```

хранить необязательно.

Нет `Attendance`:

```text
=
UNMARKED
```

---

# 24. Кто может поставить Attendance

Обычный путь:

```text
Coach
```

Также:

```text
Administrator
```

Никогда:

```text
Student
Parent
Guardian
```

---

# 25. Когда тренер может отмечать Attendance

Рекомендуемое правило:

```text
current_time >= Lesson.starts_at
```

и Lesson находится в:

```text
CONFIRMED
или
COMPLETED
```

До начала занятия кнопки посещения неактивны.

---

# 26. UNMARKED → PRESENT

Тренер нажимает:

```text
[ Пришёл ]
```

создаётся:

```text
Attendance
status = PRESENT
```

и событие:

```text
AttendanceMarkedPresent
```

После этого запускается механизм покрытия посещения абонементом.

---

# 27. UNMARKED → ABSENT

Тренер отмечает:

```text
[ Не пришёл ]
```

генерируется:

```text
AttendanceMarkedAbsent
```

Абонемент не расходуется.

---

# 28. PRESENT → ABSENT

Исправление:

```text
PRESENT
   ↓
ABSENT
```

генерирует:

```text
AttendanceCorrectedToAbsent
```

Если существовало списание:

```text
CONSUME -1
```

автоматически создаётся:

```text
RESTORE +1
```

Исходное списание не удаляется.

---

# 29. ABSENT → PRESENT

Исправление:

```text
ABSENT
   ↓
PRESENT
```

создаёт:

```text
AttendanceCorrectedToPresent
```

после чего выполняется обычный алгоритм подбора покрытия.

---

# 30. RSVP и Attendance могут противоречить друг другу

Это нормальная ситуация.

Например:

```text
RSVP = NO
Attendance = PRESENT
```

значит:

> человек говорил, что не придёт, но пришёл.

Или:

```text
RSVP = YES
Attendance = ABSENT
```

значит:

> собирался прийти, но не пришёл.

Система не должна автоматически исправлять одно на основании другого.

---

# 31. No-show

Удобно вычислять:

```text
RSVP = YES
AND
Attendance = ABSENT
```

как:

```text
NO_SHOW
```

Но это **derived classification**, а не состояние Attendance.

---

# 32. Subscription state machine

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

---

# 42. Компенсация без возврата денег

Если школа решает пойти клиенту навстречу, можно выдать:

```text
MakeupEntitlement
reason = ADMINISTRATIVE
```

Например:

> разрешить использовать одно оставшееся занятие в течение дополнительного периода.

Это лучше, чем вручную изменять:

```text
valid_until
```

старого абонемента.

---

# 43. Обычная отработка без справки

Сейчас действует правило:

> если человек пропустил занятие, он может прийти в другой день к другой группе.

Это прекрасно укладывается в модель **без какого-либо дополнительного кредита**.

Например:

```text
ICE subscription

8 занятий
01.09–30.09

15 сентября
Маша отсутствовала
```

Attendance:

```text
ABSENT
```

никакого списания нет.

Остаток продолжает существовать.

Маша может 18 сентября прийти на лёд с другой группой.

---

# 44. LessonEnrollment для другой группы

Чтобы человек мог планово появиться на занятии другой группы, необходимо добавить:

```text
LessonEnrollment
```

Поля:

```text
student
lesson

reason:
    MAKEUP
    GUEST
    ADMINISTRATIVE

created_by

created_at
```

Это не Attendance.

Это разрешение конкретному ученику участвовать в занятии, которое не входит в его обычную группу.

---

# 45. Обычная отработка

Сценарий:

```text
Маша состоит:
Группа A
```

Пропустила:

```text
ICE
```

Ей разрешают прийти:

```text
Группа B
20 сентября
ICE
```

создаётся:

```text
LessonEnrollment
student = Маша
lesson = занятие группы B
reason = MAKEUP
```

У Маши оно появляется в расписании.

---

# 46. Списание при отработке

Никакой специальной финансовой операции нет.

Тренер группы B отмечает:

```text
PRESENT
```

и обычный:

```text
ICE Subscription
```

уменьшается:

```text
5 → 4
```

Таким образом «отработка» означает:

> возможность использовать своё оставшееся занятие на другом Lesson.

Она не означает выдачу дополнительного занятия.

---

# 47. Главное ограничение обычной отработки

Без медицинского исключения альтернативное занятие должно состояться:

```text
до valid_until
```

абонемента.

После окончания месяца обычная отработка невозможна.

Именно здесь медицинская справка создаёт отдельное исключение.

---

# 48. Медицинский перенос

При подтверждённом медицинском пропуске школа разрешает использовать соответствующее оставшееся занятие позже обычного срока.

Для этого вводится:

```text
AbsenceJustification
```

и:

```text
MakeupEntitlement
```

---

# 49. AbsenceJustification

Предлагаемая модель:

```text
AbsenceJustification

id

student_id
attendance_id

type:
    MEDICAL

status:
    PENDING
    VERIFIED
    REJECTED
    REVOKED

submitted_at
verified_at
verified_by

verification_method
```

---

# 50. Что именно подтверждается

Не нужно записывать:

```text
диагноз
заболевание
назначенное лечение
ФИО врача
медицинскую организацию
```

Для бизнес-процесса школы нужен только факт:

> отсутствие на конкретном занятии признано уважительным и даёт право на перенос.

---

# 51. Рекомендуемый процесс со справкой

Для MVP оптимальной считаю такую схему:

```text
Родитель / ученик:
«Есть медицинская справка»

        ↓

статус:
PENDING

        ↓

показывает бумажную справку
администратору школы

        ↓

администратор:
[ Справка проверена ]

        ↓

VERIFIED
```

В системе сохраняется:

```text
какой пропуск подтверждён

кто проверил

когда проверил
```

Сам документ не сохраняется.

Если после VERIFIED тренер исправляет Attendance с ABSENT на PRESENT:

```text
VERIFIED → REVOKED
```

неиспользованный medical MakeupEntitlement отменяется.

Если затем Attendance снова исправлен на ABSENT, пользователь может повторно
заявить медицинское основание **только если предыдущий отзыв был автоматическим
из-за коррекции attendance**.

При этом старая REVOKED justification остаётся неизменной, а создаётся новая
PENDING justification с новым UUID. Аналогично, новая верификация создаёт новый
MakeupEntitlement; отменённый entitlement не реактивируется и сохраняет прежние
сроки как историческая запись.

Административно REVOKED justification считается терминальной для автоматического
redeclare и должна требовать отдельного административного решения.

Статус REJECTED в MVP также терминальный: обычный пользователь не может
повторно заявить ту же медицинскую justification после отказа. Если школа
решит поддерживать пересмотр ошибочного отказа, это будет отдельная
административная операция с новым audit event, а не неявный переход обратно
в PENDING.

---

# 52. Почему я не рекомендую загружать справки в MVP

Справка практически неизбежно содержит сведения о состоянии здоровья, а такие сведения относятся к специальным категориям персональных данных. Закон устанавливает для их обработки отдельный режим и специальные основания.

Кроме того, общий принцип 152-ФЗ требует не собирать избыточные данные относительно цели обработки.

Для цели:

> предоставить перенос одного занятия

школе обычно не требуется хранить:

- скан справки;
- диагноз;
- назначение врача;
- историю заболевания.

Поэтому архитектурно безопаснее проверить документ лично и сохранить только минимальный результат проверки.

---

# 53. Даже отметку VERIFIED следует считать чувствительной

Запись:

```text
medical absence verified
```

сама по себе позволяет сделать вывод о медицинском основании отсутствия.

Поэтому доступ к таким данным должен быть существенно уже, чем доступ к обычному расписанию.

Например:

```text
Trainer:
не видит медицинскую справку
не видит причину

Administrator:
видит «перенос подтверждён»

Privacy/Admin role:
может видеть основание MEDICAL
```

Для тренера достаточно:

> ученику разрешена отработка до 15 октября.

---

# 54. Если в будущем понадобится загрузка справки

Это лучше делать отдельным модулем.

Примерный процесс:

```text
upload
  ↓
restricted document storage
  ↓
administrator verifies
  ↓
document deleted after
defined retention period
```

Не следует хранить файл как обычный Django `FileField` рядом с публичными media.

Перед введением такого процесса отдельно нужно определить правовое основание обработки медицинских данных и режим доступа; ст. 10 152-ФЗ устанавливает специальные условия для сведений о состоянии здоровья.

---

# 55. MakeupEntitlement

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

---

# 67. Школьный перенос

Та же сущность может покрывать:

```text
reason = SCHOOL_RESCHEDULE
```

Например:

```text
30 сентября
занятие отменено из-за низкой посещаемости

Маша хотела прийти
RSVP = YES

replacement:
2 октября
```

Система создаёт право:

```text
Маша может использовать
сентябрьский остаток
на replacement Lesson.
```

Здесь желательно:

```text
target_lesson_id = replacement_lesson
```

чтобы это право нельзя было произвольно потратить на другое занятие.

---

# 68. Кто получает такое право при школьном переносе

Рекомендую автоматически создавать его для учеников, у которых одновременно:

```text
RSVP = YES

AND

на дату исходного занятия
существовал подходящий Subscription
```

То есть школа защищает тех, кто реально планировал воспользоваться занятием.

---

# 69. Domain Events — Lesson

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

---

# 75. Финансовый отчёт руководителя

Текущий ручной процесс можно заменить экраном:

```text
Занятие:
Лёд
14 сентября
17:00

Статус:
CLOSED

Тренер:
Анна

────────────────────

Маша        PRESENT
Катя         PRESENT
Петя         ABSENT
Ира          PRESENT

────────────────────

Фактически:
3 человека
```

Руководитель получает данные сразу после:

```text
LessonAttendanceSubmitted
```

---

# 76. Отчёт по абонементам

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

---

# 79. Какие данные справки видит тренер

Тренеру показывается только функциональная информация.

Например:

```text
Маша

Дополнительное занятие:
разрешено

Категория:
ICE

Использовать до:
15 октября
```

Не показывается:

```text
«болела»
«справка №...»
диагноз
медицинская организация
```

---

# 80. Финальные domain invariants

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

---

# 81. Итоговая модель процесса

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
