# Django Models and Application Services Specification

**Версия:** 1.0  
**Backend:** Django + PostgreSQL  
**Архитектурный подход:** thin models + application services + database constraints  
**Источники истины:** Attendance — факт посещения; SubscriptionLedgerEntry — баланс абонемента.

---

# 1. Предлагаемое разбиение Django apps

```text
accounts/
    models.py
    services.py

scheduling/
    models.py
    services.py
    selectors.py

attendance/
    models.py
    services.py

subscriptions/
    models.py
    services.py
    selectors.py

audit/
    models.py
    services.py
```

Зависимости:

```text
accounts
   ↓
scheduling
   ↓
attendance
   ↓
subscriptions

audit ← все приложения
```

Циклических импортов между service-модулями следует избегать.

---

# 2. Базовые технические правила

Все значимые domain entities используют UUID:

```python
id = models.UUIDField(
    primary_key=True,
    default=uuid.uuid4,
    editable=False,
)
```

Все timestamps:

```python
DateTimeField
```

timezone-aware.

Все бизнес-даты:

```python
DateField
```

Domain history по возможности не удаляется.

Используется:

```text
is_active
cancelled_at
reversed_at
```

вместо физического DELETE.

FK на исторические domain entities обычно:

```python
on_delete=models.PROTECT
```

FK на actor (`created_by`, `updated_by`) обычно:

```python
on_delete=models.SET_NULL
```

чтобы удаление/анонимизация учётной записи не уничтожала бизнес-историю.

---

# 3. User

Рекомендуется собственная модель:

```python
class User(AbstractUser):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    email = models.EmailField(blank=True)
```

`username` остаётся техническим идентификатором Django.

Для OAuth-only пользователей он может генерироваться автоматически:

```text
u_f75d42c8...
```

и не показываться пользователю.

Не заполняются без необходимости:

```text
first_name
last_name
email
```

Для staff/admin можно использовать отдельные username/password + MFA.

---

# 4. ExternalIdentity

```python
class ExternalIdentity(models.Model):
    class Provider(models.TextChoices):
        YANDEX = "yandex", "Yandex"
        VK = "vk", "VK"

    id = UUIDField(...)

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="external_identities",
    )

    provider = models.CharField(
        max_length=16,
        choices=Provider.choices,
    )

    provider_subject = models.CharField(
        max_length=255,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
```

## Constraints

```python
models.UniqueConstraint(
    fields=["provider", "provider_subject"],
    name="extidentity_provider_subject_uniq",
)
```

## Indexes

```python
models.Index(
    fields=["user", "provider"],
    name="extidentity_user_provider_idx",
)
```

---

# 5. Student

```python
class Student(models.Model):
    id = UUIDField(...)

    display_name = models.CharField(max_length=100)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

`display_name` не уникален.

Допустимы:

```text
Маша
Маша К.
Алексей
```

Точное ФИО для расписания не требуется.

## Index

```python
models.Index(
    fields=["is_active", "display_name"],
    name="student_active_name_idx",
)
```

---

# 6. StudentAccess

Определяет, кто может работать с расписанием Student.

```python
class StudentAccess(models.Model):
    class Role(models.TextChoices):
        SELF = "self", "Self"
        GUARDIAN = "guardian", "Guardian"

    id = UUIDField(...)

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="student_accesses",
    )

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="accesses",
    )

    role = models.CharField(
        max_length=16,
        choices=Role.choices,
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
```

## UniqueConstraint

```python
models.UniqueConstraint(
    fields=["user", "student"],
    name="studentaccess_user_student_uniq",
)
```

## Indexes

```python
models.Index(
    fields=["user", "is_active"],
    name="studentaccess_user_active_idx",
),

models.Index(
    fields=["student", "is_active"],
    name="studentaccess_student_active_idx",
)
```

---

# 7. CoachProfile

```python
class CoachProfile(models.Model):
    id = UUIDField(...)

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="coach_profile",
    )

    display_name = models.CharField(max_length=100)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
```

Пользователь теоретически может одновременно иметь:

```text
CoachProfile
+
StudentAccess(SELF)
```

если тренер также занимается.

---

# 8. TrainingGroup

```python
class TrainingGroup(models.Model):
    id = UUIDField(...)

    code = models.SlugField(
        max_length=64,
        unique=True,
    )

    name = models.CharField(max_length=128)

    default_minimum_attendees = models.PositiveSmallIntegerField(
        default=1,
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

## CheckConstraint

```python
models.CheckConstraint(
    condition=models.Q(default_minimum_attendees__gte=1),
    name="group_min_attendees_gte_1",
)
```

## Index

```python
models.Index(
    fields=["is_active", "name"],
    name="group_active_name_idx",
)
```

---

# 9. GroupMembership

Историческое членство Student в группе.

```python
class GroupMembership(models.Model):
    id = UUIDField(...)

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="group_memberships",
    )

    group = models.ForeignKey(
        TrainingGroup,
        on_delete=models.PROTECT,
        related_name="memberships",
    )

    starts_on = models.DateField()

    ends_on = models.DateField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
```

## Constraints

```python
models.CheckConstraint(
    condition=(
        models.Q(ends_on__isnull=True)
        | models.Q(ends_on__gte=models.F("starts_on"))
    ),
    name="membership_end_gte_start",
)
```

Не более одного открытого membership:

```python
models.UniqueConstraint(
    fields=["student", "group"],
    condition=models.Q(ends_on__isnull=True),
    name="membership_one_open_per_group",
)
```

Дополнительная защита:

```python
models.UniqueConstraint(
    fields=["student", "group", "starts_on"],
    name="membership_student_group_start_uniq",
)
```

## Indexes

```python
models.Index(
    fields=["group", "starts_on", "ends_on"],
    name="membership_group_dates_idx",
),

models.Index(
    fields=["student", "starts_on", "ends_on"],
    name="membership_student_dates_idx",
)
```

## Service-level invariant

Перекрывающиеся закрытые интервалы:

```text
01.01–30.06
15.06–31.08
```

обычный `CheckConstraint` между разными строками не предотвращает.

Проверяется:

```text
GroupMembershipService
```

перед созданием/изменением membership.

---

# 10. Venue

```python
class Venue(models.Model):
    id = UUIDField(...)

    code = models.SlugField(
        max_length=64,
        unique=True,
    )

    name = models.CharField(max_length=128)

    address = models.CharField(
        max_length=255,
        blank=True,
    )

    is_active = models.BooleanField(default=True)
```

---

# 11. SubscriptionCategory

Не отдельная таблица.

```python
class SubscriptionCategory(models.TextChoices):
    ICE = "ice", "Ice"
    HALL = "hall", "Hall"
```

---

# 12. LessonType

```python
class LessonType(models.Model):
    id = UUIDField(...)

    code = models.SlugField(
        max_length=64,
        unique=True,
    )

    name = models.CharField(max_length=100)

    subscription_category = models.CharField(
        max_length=16,
        choices=SubscriptionCategory.choices,
    )

    is_active = models.BooleanField(default=True)
```

Исходные записи:

```text
ICE
    subscription_category = ICE

PHYSICAL
    subscription_category = HALL

CHOREOGRAPHY
    subscription_category = HALL
```

---

# 13. ScheduleTemplate

```python
class ScheduleTemplate(models.Model):
    id = UUIDField(...)

    group = models.ForeignKey(
        TrainingGroup,
        on_delete=models.PROTECT,
        related_name="schedule_templates",
    )

    lesson_type = models.ForeignKey(
        LessonType,
        on_delete=models.PROTECT,
        related_name="schedule_templates",
    )

    coach = models.ForeignKey(
        CoachProfile,
        on_delete=models.PROTECT,
        related_name="schedule_templates",
    )

    venue = models.ForeignKey(
        Venue,
        on_delete=models.PROTECT,
        related_name="schedule_templates",
    )

    weekday = models.PositiveSmallIntegerField()

    start_time = models.TimeField()

    duration_minutes = models.PositiveSmallIntegerField()

    valid_from = models.DateField()

    valid_until = models.DateField(
        null=True,
        blank=True,
    )

    minimum_attendees_override = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

`weekday`:

```text
0 = Monday
...
6 = Sunday
```

## CheckConstraints

```python
models.CheckConstraint(
    condition=models.Q(weekday__gte=0, weekday__lte=6),
    name="schedule_weekday_0_6",
)
```

```python
models.CheckConstraint(
    condition=models.Q(duration_minutes__gt=0),
    name="schedule_duration_gt_0",
)
```

```python
models.CheckConstraint(
    condition=(
        models.Q(valid_until__isnull=True)
        | models.Q(valid_until__gte=models.F("valid_from"))
    ),
    name="schedule_valid_until_gte_from",
)
```

```python
models.CheckConstraint(
    condition=(
        models.Q(minimum_attendees_override__isnull=True)
        | models.Q(minimum_attendees_override__gte=1)
    ),
    name="schedule_min_attendees_valid",
)
```

## Indexes

```python
models.Index(
    fields=["is_active", "weekday"],
    name="schedule_active_weekday_idx",
),

models.Index(
    fields=["group", "is_active"],
    name="schedule_group_active_idx",
)
```

---

# 14. Lesson

Центральная сущность Scheduling.

```python
class Lesson(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RSVP_OPEN = "rsvp_open", "RSVP open"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    class CancellationReason(models.TextChoices):
        LOW_ATTENDANCE = "low_attendance", "Low attendance"
        COACH_UNAVAILABLE = "coach_unavailable", "Coach unavailable"
        VENUE_UNAVAILABLE = "venue_unavailable", "Venue unavailable"
        ADMINISTRATIVE = "administrative", "Administrative"
        OTHER = "other", "Other"

    id = UUIDField(...)

    source_template = models.ForeignKey(
        ScheduleTemplate,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="generated_lessons",
    )

    group = models.ForeignKey(
        TrainingGroup,
        on_delete=models.PROTECT,
        related_name="lessons",
    )

    lesson_type = models.ForeignKey(
        LessonType,
        on_delete=models.PROTECT,
        related_name="lessons",
    )

    coach = models.ForeignKey(
        CoachProfile,
        on_delete=models.PROTECT,
        related_name="lessons",
    )

    venue = models.ForeignKey(
        Venue,
        on_delete=models.PROTECT,
        related_name="lessons",
    )

    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()

    minimum_attendees = models.PositiveSmallIntegerField()

    rsvp_deadline = models.DateTimeField()
    decision_deadline = models.DateTimeField()

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    published_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    confirmed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    decision_evaluated_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    decision_yes_count = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )

    decision_no_count = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )

    decision_no_response_count = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    attendance_submitted_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    attendance_submitted_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    cancellation_reason = models.CharField(
        max_length=32,
        choices=CancellationReason.choices,
        null=True,
        blank=True,
    )

    replacement_lesson = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="replaced_lesson",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

## CheckConstraints

Время:

```python
models.CheckConstraint(
    condition=models.Q(ends_at__gt=models.F("starts_at")),
    name="lesson_end_gt_start",
)
```

```python
models.CheckConstraint(
    condition=models.Q(rsvp_deadline__lte=models.F("starts_at")),
    name="lesson_rsvp_before_start",
)
```

```python
models.CheckConstraint(
    condition=models.Q(decision_deadline__lte=models.F("starts_at")),
    name="lesson_decision_before_start",
)
```

```python
models.CheckConstraint(
    condition=models.Q(minimum_attendees__gte=1),
    name="lesson_min_attendees_gte_1",
)
```

Отмена:

```python
models.CheckConstraint(
    condition=(
        models.Q(
            status=Status.CANCELLED,
            cancelled_at__isnull=False,
            cancellation_reason__isnull=False,
        )
        |
        ~models.Q(status=Status.CANCELLED)
    ),
    name="lesson_cancelled_requires_metadata",
)
```

Replacement имеет смысл только у отменённого занятия:

```python
models.CheckConstraint(
    condition=(
        models.Q(replacement_lesson__isnull=True)
        | models.Q(status=Status.CANCELLED)
    ),
    name="lesson_replacement_only_cancelled",
)
```

## Idempotent lesson generation

```python
models.UniqueConstraint(
    fields=["source_template", "starts_at"],
    condition=models.Q(source_template__isnull=False),
    name="lesson_template_start_uniq",
)
```

Повторный `generate_lessons()` не создаст второй Lesson.

## Indexes

Основной календарь:

```python
models.Index(
    fields=["status", "starts_at"],
    name="lesson_status_start_idx",
)
```

Группа:

```python
models.Index(
    fields=["group", "starts_at"],
    name="lesson_group_start_idx",
)
```

Тренер:

```python
models.Index(
    fields=["coach", "starts_at"],
    name="lesson_coach_start_idx",
)
```

Decision worker:

```python
models.Index(
    fields=["status", "decision_deadline"],
    name="lesson_status_decision_idx",
)
```

---

# 15. LessonRosterEntry

При публикации Lesson формируется snapshot приглашённых учеников.

Это защищает историю от последующего изменения GroupMembership.

```python
class LessonRosterEntry(models.Model):
    class Source(models.TextChoices):
        GROUP = "group", "Group membership"
        ENROLLMENT = "enrollment", "Lesson enrollment"
        MANUAL = "manual", "Manual"

    id = UUIDField(...)

    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.PROTECT,
        related_name="roster_entries",
    )

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="lesson_roster_entries",
    )

    source = models.CharField(
        max_length=16,
        choices=Source.choices,
    )

    group_membership = models.ForeignKey(
        GroupMembership,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    lesson_enrollment = models.ForeignKey(
        "LessonEnrollment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    is_active = models.BooleanField(default=True)

    added_at = models.DateTimeField(auto_now_add=True)

    added_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    deactivated_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    deactivated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
```

## UniqueConstraint

```python
models.UniqueConstraint(
    fields=["lesson", "student"],
    name="lessonroster_lesson_student_uniq",
)
```

## CheckConstraint

```python
models.CheckConstraint(
    condition=(
        models.Q(
            is_active=True,
            deactivated_at__isnull=True,
        )
        |
        models.Q(
            is_active=False,
            deactivated_at__isnull=False,
        )
    ),
    name="lessonroster_active_consistency",
)
```

## Indexes

```python
models.Index(
    fields=["lesson", "is_active"],
    name="lessonroster_lesson_active_idx",
),

models.Index(
    fields=["student", "is_active"],
    name="lessonroster_student_active_idx",
)
```

---

# 16. LessonEnrollment

Разрешение посещать занятие вне основной группы.

```python
class LessonEnrollment(models.Model):
    class Reason(models.TextChoices):
        MAKEUP = "makeup", "Make-up"
        GUEST = "guest", "Guest"
        ADMINISTRATIVE = "administrative", "Administrative"

    id = UUIDField(...)

    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.PROTECT,
        related_name="enrollments",
    )

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="lesson_enrollments",
    )

    reason = models.CharField(
        max_length=24,
        choices=Reason.choices,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    created_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
```

## Unique active enrollment

```python
models.UniqueConstraint(
    fields=["lesson", "student"],
    condition=models.Q(cancelled_at__isnull=True),
    name="lessonenrollment_one_active",
)
```

## CheckConstraint

```python
models.CheckConstraint(
    condition=(
        models.Q(
            cancelled_at__isnull=True,
            cancelled_by__isnull=True,
        )
        |
        models.Q(cancelled_at__isnull=False)
    ),
    name="lessonenrollment_cancel_consistency",
)
```

## Indexes

```python
models.Index(
    fields=["lesson", "cancelled_at"],
    name="lessonenroll_lesson_cancel_idx",
),

models.Index(
    fields=["student", "cancelled_at"],
    name="lessonenroll_student_cancel_idx",
)
```

---

# 17. LessonResponse

```python
class LessonResponse(models.Model):
    class Status(models.TextChoices):
        YES = "yes", "Will attend"
        NO = "no", "Will not attend"

    id = UUIDField(...)

    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.PROTECT,
        related_name="responses",
    )

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="lesson_responses",
    )

    status = models.CharField(
        max_length=8,
        choices=Status.choices,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    updated_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
```

## UniqueConstraint

```python
models.UniqueConstraint(
    fields=["student", "lesson"],
    name="lessonresponse_student_lesson_uniq",
)
```

## Index

```python
models.Index(
    fields=["lesson", "status"],
    name="lessonresponse_lesson_status_idx",
)
```

---

# 18. Attendance

Источник истины о фактическом посещении.

```python
class Attendance(models.Model):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"

    id = UUIDField(...)

    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.PROTECT,
        related_name="attendance_records",
    )

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="attendance_records",
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
    )

    marked_at = models.DateTimeField()

    marked_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    updated_at = models.DateTimeField(auto_now=True)

    updated_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
```

## UniqueConstraint

```python
models.UniqueConstraint(
    fields=["student", "lesson"],
    name="attendance_student_lesson_uniq",
)
```

## Indexes

```python
models.Index(
    fields=["lesson", "status"],
    name="attendance_lesson_status_idx",
),

models.Index(
    fields=["student", "marked_at"],
    name="attendance_student_marked_idx",
)
```

Нет записи Attendance:

```text
UNMARKED
```

---

# 19. AbsenceJustification

В MVP хранится факт проверки медицинского основания, а не медицинский документ.

```python
class AbsenceJustification(models.Model):
    class Type(models.TextChoices):
        MEDICAL = "medical", "Medical"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"
        REVOKED = "revoked", "Revoked"

    class VerificationMethod(models.TextChoices):
        IN_PERSON = "in_person", "Document checked in person"

    id = UUIDField(...)

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="absence_justifications",
    )

    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.PROTECT,
        related_name="absence_justifications",
    )

    type = models.CharField(
        max_length=16,
        choices=Type.choices,
        default=Type.MEDICAL,
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )

    verification_method = models.CharField(
        max_length=24,
        choices=VerificationMethod.choices,
        default=VerificationMethod.IN_PERSON,
    )

    declared_at = models.DateTimeField(auto_now_add=True)

    declared_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    revoked_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
```

Никаких полей:

```text
diagnosis
doctor
clinic
medical_comment
file
```

в MVP нет.

## UniqueConstraint

```python
models.UniqueConstraint(
    fields=["student", "lesson", "type"],
    name="absencejust_student_lesson_type_uniq",
)
```

## State consistency

```python
models.CheckConstraint(
    condition=(
        models.Q(
            status=Status.PENDING,
            reviewed_at__isnull=True,
            reviewed_by__isnull=True,
            revoked_at__isnull=True,
            revoked_by__isnull=True,
        )
        |
        models.Q(
            status__in=[Status.VERIFIED, Status.REJECTED],
            reviewed_at__isnull=False,
            reviewed_by__isnull=False,
            revoked_at__isnull=True,
            revoked_by__isnull=True,
        )
        |
        models.Q(
            status=Status.REVOKED,
            reviewed_at__isnull=False,
            revoked_at__isnull=False,
        )
    ),
    name="absencejust_status_metadata_consistency",
)
```

## Index

```python
models.Index(
    fields=["status", "declared_at"],
    name="absencejust_status_declared_idx",
)
```

---

# 20. SubscriptionPlan

```python
class SubscriptionPlan(models.Model):
    id = UUIDField(...)

    code = models.SlugField(
        max_length=64,
        unique=True,
    )

    name = models.CharField(max_length=128)

    category = models.CharField(
        max_length=16,
        choices=SubscriptionCategory.choices,
    )

    visit_limit = models.PositiveSmallIntegerField()

    validity_months = models.PositiveSmallIntegerField(
        default=1,
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

## Current business rule

```python
models.CheckConstraint(
    condition=models.Q(validity_months=1),
    name="subscriptionplan_one_month",
)
```

Если позже появятся абонементы на 2–3 месяца, constraint снимается миграцией.

## Other Check

```python
models.CheckConstraint(
    condition=models.Q(visit_limit__gt=0),
    name="subscriptionplan_visit_limit_gt_0",
)
```

## Index

```python
models.Index(
    fields=["category", "is_active"],
    name="subplan_category_active_idx",
)
```

---

# 21. Subscription

```python
class Subscription(models.Model):
    id = UUIDField(...)

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )

    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )

    plan_name_snapshot = models.CharField(
        max_length=128,
    )

    category_snapshot = models.CharField(
        max_length=16,
        choices=SubscriptionCategory.choices,
    )

    visit_limit_snapshot = models.PositiveSmallIntegerField()

    valid_from = models.DateField()
    valid_until = models.DateField()

    created_at = models.DateTimeField(auto_now_add=True)

    created_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
```

## CheckConstraints

```python
models.CheckConstraint(
    condition=models.Q(valid_until__gte=models.F("valid_from")),
    name="subscription_until_gte_from",
)
```

```python
models.CheckConstraint(
    condition=models.Q(visit_limit_snapshot__gt=0),
    name="subscription_visit_limit_gt_0",
)
```

```python
models.CheckConstraint(
    condition=(
        models.Q(
            cancelled_at__isnull=True,
            cancelled_by__isnull=True,
        )
        |
        models.Q(cancelled_at__isnull=False)
    ),
    name="subscription_cancel_consistency",
)
```

## Coverage index

```python
models.Index(
    fields=[
        "student",
        "category_snapshot",
        "valid_from",
        "valid_until",
    ],
    condition=models.Q(cancelled_at__isnull=True),
    name="subscription_coverage_idx",
)
```

## History

```python
models.Index(
    fields=["student", "-valid_from"],
    name="subscription_student_history_idx",
)
```

---

# 22. MakeupEntitlement

Разрешение использовать существующий остаток за пределами обычного правила.

```python
class MakeupEntitlement(models.Model):
    class Reason(models.TextChoices):
        MEDICAL_VERIFIED = "medical", "Verified medical absence"
        SCHOOL_RESCHEDULE = "school_reschedule", "School reschedule"
        ADMINISTRATIVE = "administrative", "Administrative"

    id = UUIDField(...)

    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name="makeup_entitlements",
    )

    source_lesson = models.ForeignKey(
        Lesson,
        on_delete=models.PROTECT,
        related_name="generated_makeup_entitlements",
    )

    source_subscription = models.ForeignKey(
        Subscription,
        on_delete=models.PROTECT,
        related_name="makeup_entitlements",
    )

    source_justification = models.ForeignKey(
        AbsenceJustification,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="makeup_entitlements",
    )

    category = models.CharField(
        max_length=16,
        choices=SubscriptionCategory.choices,
    )

    reason = models.CharField(
        max_length=24,
        choices=Reason.choices,
    )

    valid_from = models.DateField()
    valid_until = models.DateField()

    target_lesson = models.ForeignKey(
        Lesson,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="targeted_makeup_entitlements",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    created_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
```

## Constraints

```python
models.CheckConstraint(
    condition=models.Q(valid_until__gte=models.F("valid_from")),
    name="makeup_until_gte_from",
)
```

Один entitlement одного типа за один source Lesson:

```python
models.UniqueConstraint(
    fields=["student", "source_lesson", "reason"],
    name="makeup_student_source_reason_uniq",
)
```

Medical требует justification:

```python
models.CheckConstraint(
    condition=(
        ~models.Q(reason=Reason.MEDICAL_VERIFIED)
        | models.Q(source_justification__isnull=False)
    ),
    name="makeup_medical_requires_justification",
)
```

School reschedule требует target:

```python
models.CheckConstraint(
    condition=(
        ~models.Q(reason=Reason.SCHOOL_RESCHEDULE)
        | models.Q(target_lesson__isnull=False)
    ),
    name="makeup_reschedule_requires_target",
)
```

Cancellation:

```python
models.CheckConstraint(
    condition=(
        models.Q(
            cancelled_at__isnull=True,
            cancelled_by__isnull=True,
        )
        |
        models.Q(cancelled_at__isnull=False)
    ),
    name="makeup_cancel_consistency",
)
```

## Index

```python
models.Index(
    fields=["student", "category", "valid_until"],
    condition=models.Q(cancelled_at__isnull=True),
    name="makeup_available_lookup_idx",
)
```

---

# 23. SubscriptionUsage

Связывает:

```text
Attendance
      ↓
Subscription
```

и при необходимости:

```text
MakeupEntitlement
```

```python
class SubscriptionUsage(models.Model):
    id = UUIDField(...)

    attendance = models.ForeignKey(
        Attendance,
        on_delete=models.PROTECT,
        related_name="subscription_usages",
    )

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.PROTECT,
        related_name="usages",
    )

    makeup_entitlement = models.ForeignKey(
        MakeupEntitlement,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="usages",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    created_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    reversed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    reversed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
```

## Один активный Usage на Attendance

```python
models.UniqueConstraint(
    fields=["attendance"],
    condition=models.Q(reversed_at__isnull=True),
    name="usage_one_active_per_attendance",
)
```

Исторические reversed Usage при этом сохраняются.

## Один MakeupEntitlement нельзя использовать дважды одновременно

```python
models.UniqueConstraint(
    fields=["makeup_entitlement"],
    condition=(
        models.Q(
            makeup_entitlement__isnull=False,
            reversed_at__isnull=True,
        )
    ),
    name="usage_one_active_per_makeup",
)
```

## Reverse consistency

```python
models.CheckConstraint(
    condition=(
        models.Q(
            reversed_at__isnull=True,
            reversed_by__isnull=True,
        )
        |
        models.Q(reversed_at__isnull=False)
    ),
    name="usage_reverse_consistency",
)
```

## Index

```python
models.Index(
    fields=["subscription", "reversed_at"],
    name="usage_subscription_reverse_idx",
)
```

---

# 24. SubscriptionLedgerEntry

Единственный источник истины о количестве занятий.

```python
class SubscriptionLedgerEntry(models.Model):
    class EntryType(models.TextChoices):
        GRANT = "grant", "Grant"
        CONSUME = "consume", "Consume"
        RESTORE = "restore", "Restore"
        ADJUSTMENT = "adjustment", "Adjustment"

    id = UUIDField(...)

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )

    usage = models.ForeignKey(
        SubscriptionUsage,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )

    entry_type = models.CharField(
        max_length=16,
        choices=EntryType.choices,
    )

    delta = models.SmallIntegerField()

    reason = models.CharField(
        max_length=255,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    created_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
```

## Delta semantics

```python
models.CheckConstraint(
    condition=(
        models.Q(
            entry_type=EntryType.GRANT,
            delta__gt=0,
        )
        |
        models.Q(
            entry_type=EntryType.CONSUME,
            delta=-1,
        )
        |
        models.Q(
            entry_type=EntryType.RESTORE,
            delta=1,
        )
        |
        (
            models.Q(entry_type=EntryType.ADJUSTMENT)
            & ~models.Q(delta=0)
        )
    ),
    name="ledger_delta_matches_type",
)
```

## Usage requirement

`CONSUME` и `RESTORE` обязательно относятся к Usage:

```python
models.CheckConstraint(
    condition=(
        (
            models.Q(
                entry_type__in=[
                    EntryType.CONSUME,
                    EntryType.RESTORE,
                ],
            )
            & models.Q(usage__isnull=False)
        )
        |
        (
            models.Q(
                entry_type__in=[
                    EntryType.GRANT,
                    EntryType.ADJUSTMENT,
                ],
            )
            & models.Q(usage__isnull=True)
        )
    ),
    name="ledger_usage_requirement",
)
```

## Только один GRANT

```python
models.UniqueConstraint(
    fields=["subscription"],
    condition=models.Q(entry_type=EntryType.GRANT),
    name="ledger_one_grant_per_subscription",
)
```

## Один CONSUME/RESTORE каждого типа на Usage

```python
models.UniqueConstraint(
    fields=["usage", "entry_type"],
    condition=models.Q(usage__isnull=False),
    name="ledger_usage_type_uniq",
)
```

## Indexes

```python
models.Index(
    fields=["subscription", "created_at"],
    name="ledger_subscription_time_idx",
),

models.Index(
    fields=["subscription", "entry_type"],
    name="ledger_subscription_type_idx",
)
```

Баланс:

```python
SUM(delta)
```

Никакого поля:

```text
remaining_visits
```

в `Subscription` нет.

---

# 25. AuditEvent

```python
class AuditEvent(models.Model):
    id = UUIDField(...)

    event_type = models.CharField(max_length=80)

    actor = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    aggregate_type = models.CharField(max_length=64)

    aggregate_id = models.UUIDField()

    correlation_id = models.UUIDField(
        default=uuid.uuid4,
    )

    payload = models.JSONField(default=dict)

    occurred_at = models.DateTimeField(auto_now_add=True)
```

## Indexes

```python
models.Index(
    fields=[
        "aggregate_type",
        "aggregate_id",
        "occurred_at",
    ],
    name="audit_aggregate_time_idx",
),

models.Index(
    fields=["event_type", "occurred_at"],
    name="audit_event_time_idx",
),

models.Index(
    fields=["correlation_id"],
    name="audit_correlation_idx",
)
```

`AuditEvent` immutable.

В Django Admin:

```text
ADD = forbidden
CHANGE = forbidden
DELETE = forbidden
```

---

# 26. Что PostgreSQL не может корректно проверить обычным CheckConstraint

Следующие invariants являются межтабличными и должны проверяться application services.

### RESPONSE-01

Student входит в активный LessonRosterEntry.

### RESPONSE-02

Lesson находится в:

```text
RSVP_OPEN
CONFIRMED
```

### RESPONSE-03

Сейчас не позже `rsvp_deadline`.

### ATT-01

Student существует в roster занятия или явно добавлен администратором.

### ATT-02

Actor — назначенный Coach или Administrator.

### ATT-03

Attendance нельзя менять после CLOSED без reopen.

### COVERAGE-01

Attendance.student == Subscription.student.

### COVERAGE-02

Lesson category == Subscription.category_snapshot.

### COVERAGE-03

Обычный Subscription действует на дату Lesson.

### COVERAGE-04

MakeupEntitlement.student == Attendance.student.

### COVERAGE-05

MakeupEntitlement.category совпадает с Lesson.

### COVERAGE-06

MakeupEntitlement.source_subscription == Usage.subscription.

### CLOSE-01

Все активные roster participants имеют Attendance.

### MEDICAL-01

Verified medical justification относится к фактическому ABSENT.

Эти правила нельзя безопасно распределять по `model.save()`.

Они реализуются application services.

---

# 27. Запрещённая архитектура

Не следует реализовывать:

```python
Attendance.save()
    -> автоматически списать Subscription
```

или:

```python
@receiver(post_save, sender=Attendance)
def ...
```

Критическая цепочка:

```text
Attendance
→ SubscriptionUsage
→ LedgerEntry
```

должна быть явно видна в application service.

---

# 28. Общий принцип Application Services

Сигнатура:

```python
service(
    *,
    actor: User | None,
    ...
)
```

Каждая изменяющая несколько aggregates операция:

```python
@transaction.atomic
```

Domain objects блокируются:

```python
.select_for_update()
```

до изменения.

AuditEvent создаётся в той же транзакции.

---

# 29. scheduling.services.generate_lessons()

```python
generate_lessons(
    *,
    template_id: UUID,
    from_date: date,
    until_date: date,
    actor: User | None = None,
) -> list[Lesson]
```

Алгоритм:

```text
1. Получить ScheduleTemplate.
2. Проверить active и period.
3. Найти подходящие даты weekday.
4. Рассчитать starts_at/ends_at.
5. Рассчитать minimum_attendees:
      template override
      или group default.
6. Рассчитать RSVP/decision deadlines.
7. Создать Lesson(DRAFT).
8. Полагаться также на
   UNIQUE(source_template, starts_at).
```

Повторный запуск идемпотентен.

---

# 30. scheduling.services.publish_lesson()

```python
publish_lesson(
    *,
    lesson_id: UUID,
    actor: User | None,
    now: datetime,
) -> Lesson
```

Транзакция:

```text
LOCK Lesson

require:
    status == DRAFT

получить GroupMembership,
действующие на lesson date

получить active LessonEnrollment

создать LessonRosterEntry snapshot

status:
    DRAFT → RSVP_OPEN

published_at = now

AuditEvent:
    LessonPublished
```

Повторная публикация запрещена.

---

# 31. scheduling.services.publish_daily_schedule()

```python
publish_daily_schedule(
    *,
    school_date: date,
    now: datetime,
) -> list[Lesson]
```

Используется scheduler/management command.

Находит:

```text
DRAFT Lesson
starts_at.date = school_date
```

и вызывает `publish_lesson()`.

---

# 32. scheduling.services.set_lesson_response()

```python
set_lesson_response(
    *,
    actor: User,
    student_id: UUID,
    lesson_id: UUID,
    status: YES | NO,
    now: datetime,
) -> LessonResponse
```

Проверяет:

```text
StudentAccess(actor, student)
active

Lesson status:
RSVP_OPEN или CONFIRMED

now <= rsvp_deadline

active LessonRosterEntry существует
```

Далее:

```text
update_or_create LessonResponse
updated_by = actor
```

Domain event:

```text
LessonResponseChanged
```

Последний ответ становится актуальным.

---

# 33. scheduling.services.evaluate_lesson_viability()

```python
evaluate_lesson_viability(
    *,
    lesson_id: UUID,
    now: datetime,
) -> LessonViabilityResult
```

`LessonViabilityResult`:

```text
yes_count
no_count
no_response_count
minimum_attendees
minimum_met
```

При официальном decision evaluation:

```text
decision_evaluated_at
decision_yes_count
decision_no_count
decision_no_response_count
```

фиксируются в Lesson.

Если:

```text
yes_count >= minimum
```

event:

```text
LessonMinimumReached
```

иначе:

```text
LessonMinimumNotMet
```

Самостоятельного CANCEL здесь нет.

---

# 34. scheduling.services.confirm_lesson()

```python
confirm_lesson(
    *,
    lesson_id: UUID,
    actor: User,
    now: datetime,
) -> Lesson
```

Допустимые состояния:

```text
RSVP_OPEN
```

Переход:

```text
RSVP_OPEN → CONFIRMED
```

Заполняются:

```text
confirmed_at
confirmed_by
```

Event:

```text
LessonConfirmed
```

Минимум участников не является жёстким запретом.

Администратор может подтвердить занятие даже при:

```text
YES = 2
minimum = 4
```

---

# 35. scheduling.services.cancel_lesson()

```python
cancel_lesson(
    *,
    lesson_id: UUID,
    actor: User,
    reason: CancellationReason,
    now: datetime,
) -> Lesson
```

Допустимые исходные состояния:

```text
RSVP_OPEN
CONFIRMED
```

Переход:

```text
→ CANCELLED
```

Заполняются:

```text
cancelled_at
cancelled_by
cancellation_reason
```

Event:

```text
LessonCancelled
```

Attendance для отменённого занятия создавать нельзя.

---

# 36. scheduling.services.reschedule_lesson()

```python
reschedule_lesson(
    *,
    lesson_id: UUID,
    new_starts_at: datetime,
    new_ends_at: datetime,
    actor: User,
    reason: CancellationReason,
) -> Lesson
```

В одной транзакции:

```text
LOCK source Lesson

source:
    → CANCELLED

создать replacement Lesson
status = DRAFT

source.replacement_lesson = replacement

Event:
    LessonRescheduled
```

RSVP не копируются.

После этого replacement публикуется отдельно.

---

# 37. Entitlements при переносе школы

В `reschedule_lesson()` дополнительный этап выполняется, если новое занятие выходит за срок старого Subscription.

Для каждого:

```text
source LessonResponse = YES
```

ищется Subscription, который мог покрыть исходное занятие.

Если:

```text
replacement.date > subscription.valid_until
```

создаётся:

```text
MakeupEntitlement

reason = SCHOOL_RESCHEDULE
target_lesson = replacement
source_subscription = найденный Subscription
```

Это не создаёт GRANT.

---

# 38. scheduling.services.add_lesson_enrollment()

```python
add_lesson_enrollment(
    *,
    lesson_id: UUID,
    student_id: UUID,
    reason: EnrollmentReason,
    actor: User,
) -> LessonEnrollment
```

Создаёт Enrollment.

Если Lesson уже опубликован:

```text
создаётся/активируется LessonRosterEntry
source = ENROLLMENT
```

Таким образом отработка с другой группой появляется в персональном расписании ученика.

---

# 39. scheduling.services.complete_lesson()

```python
complete_lesson(
    *,
    lesson_id: UUID,
    now: datetime,
) -> Lesson
```

Требует:

```text
status = CONFIRMED
now >= ends_at
```

Переход:

```text
CONFIRMED → COMPLETED
```

Заполняется:

```text
completed_at
```

Event:

```text
LessonCompleted
```

Может запускаться автоматически.

---

# 40. attendance.services.set_attendance()

Основной service фактического посещения.

```python
set_attendance(
    *,
    lesson_id: UUID,
    student_id: UUID,
    status: PRESENT | ABSENT,
    actor: User,
    now: datetime,
) -> Attendance
```

Допустимый Lesson:

```text
CONFIRMED
COMPLETED
```

и:

```text
now >= starts_at
```

Actor:

```text
lesson.coach.user
или
administrator
```

---

# 41. Locking set_attendance()

В транзакции:

```text
LOCK LessonRosterEntry(student, lesson)

LOCK Attendance, если существует
```

`LessonRosterEntry` используется как стабильный lock anchor.

Это предотвращает два одновременных действия:

```text
PRESENT
ABSENT
```

для одного ученика.

---

# 42. UNMARKED → PRESENT

Service:

```text
создаёт Attendance(PRESENT)

↓

assign_attendance_coverage()
```

Events:

```text
AttendanceMarkedPresent

AttendanceCoverageAssigned
```

или:

```text
AttendanceUncovered
```

---

# 43. UNMARKED → ABSENT

```text
создать Attendance(ABSENT)
```

Event:

```text
AttendanceMarkedAbsent
```

Абонемент не изменяется.

---

# 44. ABSENT → PRESENT

```text
Attendance.status = PRESENT

↓

assign_attendance_coverage()
```

Event:

```text
AttendanceCorrectedToPresent
```

---

# 45. PRESENT → ABSENT

Перед изменением:

```text
reverse_attendance_coverage()
```

после чего:

```text
Attendance.status = ABSENT
```

Events:

```text
AttendanceCoverageReversed
AttendanceCorrectedToAbsent
```

---

# 46. attendance.services.mark_remaining_absent()

```python
mark_remaining_absent(
    *,
    lesson_id: UUID,
    actor: User,
) -> int
```

Для всех:

```text
active LessonRosterEntry
```

без Attendance создаёт:

```text
Attendance = ABSENT
```

Нужно для удобного закрытия занятия.

---

# 47. attendance.services.submit_attendance()

```python
submit_attendance(
    *,
    lesson_id: UUID,
    actor: User,
    now: datetime,
) -> Lesson
```

Требует:

```text
Lesson.status = COMPLETED
```

Для каждого активного roster participant должна существовать Attendance.

Если остаются UNMARKED:

```text
ValidationError
```

Frontend предлагает:

```text
[ Отметить оставшихся отсутствующими ]
```

После успешной проверки:

```text
COMPLETED → CLOSED
```

Заполняются:

```text
attendance_submitted_at
attendance_submitted_by
```

Event:

```text
LessonAttendanceSubmitted
```

Это точка, после которой данные используются руководителем для финансовых расчётов.

---

# 48. attendance.services.reopen_attendance()

Только administrator.

```python
reopen_attendance(
    *,
    lesson_id: UUID,
    actor: User,
    reason: str,
) -> Lesson
```

Переход:

```text
CLOSED → COMPLETED
```

Сбрасываются:

```text
attendance_submitted_at
attendance_submitted_by
```

Event:

```text
LessonAttendanceReopened
```

Audit содержит административную причину.

---

# 49. subscriptions.selectors.subscription_balance()

```python
subscription_balance(
    subscription_id: UUID,
) -> int
```

SQL conceptually:

```sql
SELECT COALESCE(SUM(delta), 0)
FROM subscription_ledger_entry
WHERE subscription_id = ...
```

Именно это число является остатком.

---

# 50. subscriptions.services.issue_subscription()

```python
issue_subscription(
    *,
    student_id: UUID,
    plan_id: UUID,
    valid_from: date,
    valid_until: date,
    actor: User,
) -> Subscription
```

Создаётся snapshot:

```text
plan_name_snapshot
category_snapshot
visit_limit_snapshot
```

и в той же транзакции:

```text
LedgerEntry:

type = GRANT
delta = visit_limit_snapshot
```

Event:

```text
SubscriptionIssued
```

---

# 51. Правило «один месяц»

Application policy дополнительно проверяет:

```text
valid_from / valid_until
```

по принятому школой определению месяца.

Модель намеренно хранит конкретные даты.

Поэтому переход:

```text
calendar month
→
rolling month
```

не потребует менять Subscription schema.

---

# 52. subscriptions.services.assign_attendance_coverage()

```python
assign_attendance_coverage(
    *,
    attendance_id: UUID,
    actor: User | None,
) -> SubscriptionUsage | None
```

Это самая критичная транзакция приложения.

---

# 53. assign_attendance_coverage — preconditions

```text
Attendance.status = PRESENT

активного SubscriptionUsage
для Attendance ещё нет
```

Если Usage уже есть:

```text
return existing
```

Операция идемпотентна.

---

# 54. Определение категории

Через:

```text
Attendance
  ↓
Lesson
  ↓
LessonType.subscription_category
```

получаем:

```text
ICE
или
HALL
```

---

# 55. Сначала ищется MakeupEntitlement

Candidate:

```text
student = Attendance.student

category = Lesson category

cancelled_at IS NULL

valid_from <= lesson.date <= valid_until

target_lesson IS NULL
OR
target_lesson = Attendance.lesson

нет active SubscriptionUsage
для entitlement
```

Order:

```text
valid_until ASC
created_at ASC
```

То есть первым используется entitlement, который раньше сгорит.

---

# 56. Проверка source Subscription entitlement

После выбора entitlement:

```text
LOCK source_subscription
```

вычисляется balance.

Если:

```text
balance > 0
```

можно использовать.

Если:

```text
balance = 0
```

entitlement не создаёт дополнительное занятие и пропускается.

---

# 57. Затем обычный Subscription

Candidate:

```text
student = Attendance.student

category_snapshot = Lesson category

cancelled_at IS NULL

valid_from <= lesson.date
valid_until >= lesson.date
```

Order:

```text
valid_until ASC
valid_from ASC
created_at ASC
```

Каждый candidate Subscription блокируется:

```python
select_for_update()
```

Баланс рассчитывается после получения lock.

Первый:

```text
balance > 0
```

выбирается.

---

# 58. Создание coverage

Создаётся:

```text
SubscriptionUsage

attendance
subscription
makeup_entitlement optional
```

и:

```text
SubscriptionLedgerEntry

type = CONSUME
delta = -1
usage = созданный Usage
```

Event:

```text
SubscriptionVisitConsumed
AttendanceCoverageAssigned
```

---

# 59. Если покрытия нет

Attendance остаётся:

```text
PRESENT
```

`SubscriptionUsage` отсутствует.

Событие:

```text
AttendanceUncovered
```

Это корректное состояние.

Факт посещения важнее учёта абонемента.

---

# 60. Почему необходимо блокировать Subscription row

Рассмотрим:

```text
balance = 1
```

Два тренера одновременно отмечают два посещения.

Без lock обе транзакции могут увидеть:

```text
balance = 1
```

и обе списать:

```text
-1
```

Получится:

```text
balance = -1
```

Поэтому любой writer ledger сначала обязан:

```text
LOCK Subscription
```

и только потом:

```text
calculate balance
insert ledger
```

---

# 61. subscriptions.services.reverse_attendance_coverage()

```python
reverse_attendance_coverage(
    *,
    attendance_id: UUID,
    actor: User,
) -> None
```

Находит active:

```text
SubscriptionUsage
```

Блокирует:

```text
Usage
Subscription
```

Создаёт:

```text
LedgerEntry

RESTORE
+1
usage = Usage
```

Затем:

```text
usage.reversed_at
usage.reversed_by
```

Event:

```text
SubscriptionVisitRestored
AttendanceCoverageReversed
```

Если был MakeupEntitlement, после reverse он автоматически снова считается доступным, поскольку активного Usage больше нет.

---

# 62. subscriptions.services.adjust_subscription()

Только administrator.

```python
adjust_subscription(
    *,
    subscription_id: UUID,
    delta: int,
    reason: str,
    actor: User,
) -> SubscriptionLedgerEntry
```

Требования:

```text
delta != 0
reason != ""
```

Внутри:

```text
LOCK Subscription

current_balance = SUM(delta)

new_balance = current_balance + adjustment
```

Не допускается:

```text
new_balance < 0
```

Создаётся:

```text
ADJUSTMENT
```

Исходные LedgerEntry никогда не редактируются.

---

# 63. subscriptions.services.cancel_subscription()

```python
cancel_subscription(
    *,
    subscription_id: UUID,
    actor: User,
    now: datetime,
) -> Subscription
```

Заполняет:

```text
cancelled_at
cancelled_by
```

Исторические:

```text
GRANT
CONSUME
RESTORE
```

не меняются.

После cancellation новые Usage создавать нельзя.

---

# 64. attendance.services.declare_medical_absence()

```python
declare_medical_absence(
    *,
    student_id: UUID,
    lesson_id: UUID,
    actor: User,
) -> AbsenceJustification
```

Actor должен иметь:

```text
StudentAccess SELF
или
GUARDIAN
```

Создаётся:

```text
PENDING
MEDICAL
```

Справку в систему не загружаем.

---

# 65. attendance.services.verify_medical_absence()

```python
verify_medical_absence(
    *,
    justification_id: UUID,
    actor: User,
    makeup_valid_until: date,
) -> tuple[
    AbsenceJustification,
    MakeupEntitlement | None,
]
```

Только administrator.

Проверки:

```text
Justification.status = PENDING

Attendance(student, lesson)
существует

Attendance.status = ABSENT
```

Далее находится Subscription, который соответствовал:

```text
student
category
дате исходного Lesson
```

Если подходящего Subscription нет:

```text
Justification → VERIFIED
```

но entitlement не создаётся.

---

# 66. Создание medical MakeupEntitlement

Если source Subscription найден:

```text
reason = MEDICAL_VERIFIED

student = justification.student

source_lesson = justification.lesson

source_subscription = subscription

source_justification = justification

category = lesson.lesson_type.subscription_category

valid_from =
    max(subscription.valid_until + 1 day, today)
    либо значение policy

valid_until =
    makeup_valid_until
```

Не создаётся:

```text
GRANT +1
```

---

# 67. attendance.services.reject_medical_absence()

```python
reject_medical_absence(
    *,
    justification_id: UUID,
    actor: User,
) -> AbsenceJustification
```

Переход:

```text
PENDING → REJECTED
```

Event:

```text
AbsenceJustificationRejected
```

---

# 68. subscriptions.services.grant_administrative_makeup()

Для исключительных конфликтных случаев.

```python
grant_administrative_makeup(
    *,
    student_id: UUID,
    source_lesson_id: UUID,
    source_subscription_id: UUID,
    valid_until: date,
    target_lesson_id: UUID | None,
    actor: User,
) -> MakeupEntitlement
```

Создаёт:

```text
reason = ADMINISTRATIVE
```

но:

```text
не создаёт GRANT
не увеличивает Subscription balance
```

В AuditEvent фиксируется actor и основание операции.

---

# 69. subscriptions.services.rebind_attendance_coverage()

Не обязательно выводить в UI MVP, но полезно иметь сервис.

Сценарий:

```text
Attendance
покрыт Subscription A

но должен был покрываться B
```

Service:

```python
rebind_attendance_coverage(
    *,
    attendance_id: UUID,
    target_subscription_id: UUID,
    actor: User,
) -> SubscriptionUsage
```

В одной транзакции:

```text
LOCK A
LOCK B

A:
RESTORE +1

старый Usage:
reversed

B:
новый Usage
CONSUME -1
```

Никакая история не удаляется.

---

# 70. financial/selectors.py

Руководитель школы получает только CLOSED Lessons.

```python
get_closed_lesson_report(
    lesson_id: UUID,
)
```

Требует:

```text
Lesson.status = CLOSED
```

Возвращает:

```text
lesson
coach
lesson_type

present_students
absent_students

present_count
absent_count

covered_count
uncovered_count
```

Это заменяет ручную передачу итогов тренером.

---

# 71. Uncovered attendance report

```python
get_uncovered_attendance()
```

Query conceptually:

```text
Attendance.status = PRESENT

AND

нет active SubscriptionUsage
```

Показывает администратору:

```text
Дата
Ученик
Занятие
Категория
```

---

# 72. Expired unused report

```python
get_expired_subscriptions_with_balance(
    *,
    as_of: date,
)
```

Условие:

```text
valid_until < as_of

cancelled_at IS NULL

SUM(ledger.delta) > 0
```

Показывает:

```text
Student
Subscription
used
remaining
medical makeup available
```

Именно этот отчёт полезен для разбора конфликтов о «сгоревших» занятиях.

---

# 73. Admin protection

Следующие модели нельзя разрешать произвольно редактировать через стандартный Django Admin:

```text
Attendance
SubscriptionUsage
SubscriptionLedgerEntry
AbsenceJustification state fields
Lesson lifecycle fields
```

Для них Admin должен вызывать application services.

Особенно:

```text
SubscriptionLedgerEntry
```

должен быть read-only.

---

# 74. Django Admin — SubscriptionLedgerEntry

```text
list = allowed
view = allowed
add = forbidden
change = forbidden
delete = forbidden
```

Изменения выполняются только:

```text
issue_subscription()
adjust_subscription()
assign_attendance_coverage()
reverse_attendance_coverage()
```

---

# 75. Django Admin — Attendance

Можно показывать Attendance read-only и предоставить custom actions:

```text
Mark present
Mark absent
Reopen Lesson
```

Но action вызывает:

```text
attendance.services.set_attendance()
```

а не:

```python
attendance.status = ...
attendance.save()
```

---

# 76. Domain events

Все значимые services создают AuditEvent.

Минимальный набор:

```text
LessonCreated
LessonPublished
LessonResponseChanged
LessonMinimumReached
LessonMinimumNotMet
LessonConfirmed
LessonCancelled
LessonRescheduled
LessonCompleted
LessonAttendanceSubmitted
LessonAttendanceReopened

AttendanceMarkedPresent
AttendanceMarkedAbsent
AttendanceCorrectedToPresent
AttendanceCorrectedToAbsent
AttendanceCoverageAssigned
AttendanceCoverageReversed
AttendanceUncovered

SubscriptionIssued
SubscriptionVisitConsumed
SubscriptionVisitRestored
SubscriptionAdjusted
SubscriptionCancelled
SubscriptionExhausted
SubscriptionExpired
SubscriptionExpiredWithUnusedBalance

AbsenceJustificationDeclared
AbsenceJustificationVerified
AbsenceJustificationRejected
AbsenceJustificationRevoked

MakeupEntitlementGranted
MakeupEntitlementUsed
MakeupEntitlementCancelled
MakeupEntitlementExpired
```

---

# 77. Event payload

Не нужно копировать туда весь object.

Например:

```json
{
  "previous_status": "absent",
  "new_status": "present",
  "lesson_id": "...",
  "student_id": "..."
}
```

или:

```json
{
  "subscription_id": "...",
  "usage_id": "...",
  "delta": -1
}
```

Не писать:

```text
OAuth tokens
session IDs
медицинские сведения
сканы документов
```

---

# 78. correlation_id

Одна бизнес-операция использует один `correlation_id`.

Например:

```text
Trainer нажал PRESENT
```

может создать:

```text
AttendanceMarkedPresent
SubscriptionVisitConsumed
AttendanceCoverageAssigned
```

У всех:

```text
correlation_id = одинаковый UUID
```

Это сильно облегчает аудит.

---

# 79. Transaction boundary

Например:

```python
@transaction.atomic
def set_attendance(...):
    ...
```

Вся цепочка:

```text
Attendance
Usage
Ledger
Audit
```

commit одновременно.

Если Ledger insert не удался:

```text
Attendance также rollback.
```

---

# 80. External side effects

Не отправлять уведомление внутри незавершённой DB transaction.

Правило:

```python
transaction.on_commit(
    lambda: send_notification(...)
)
```

Например после:

```text
LessonRescheduled
```

можно после commit отправить сообщения участникам.

---

# 81. Никаких Django signals для core business logic

Допустимо использовать signals для второстепенной технической логики.

Нельзя использовать их для:

```text
создания CONSUME
создания RESTORE
смены Lesson state
выдачи MakeupEntitlement
```

Эта логика должна быть явно вызвана service layer.

---

# 82. Selectors

Читающую логику стоит отделить от services.

Например:

```text
scheduling/selectors.py

get_student_schedule()
get_coach_today_lessons()
get_lesson_roster()
get_lesson_viability()
```

```text
subscriptions/selectors.py

subscription_balance()
get_eligible_subscriptions()
get_available_makeups()
get_student_subscription_summary()
```

Selectors не изменяют данные.

---

# 83. Основной student schedule query

```python
get_student_schedule(
    *,
    student_id: UUID,
    from_date: date,
    until_date: date,
)
```

Использует:

```text
active LessonRosterEntry

Lesson.status != DRAFT
Lesson.status != CANCELLED
```

и дополнительно может показывать CANCELLED как информационные записи некоторое время после отмены.

---

# 84. Coach schedule

```python
get_coach_today_lessons(
    *,
    coach_id: UUID,
    day: date,
)
```

Index:

```text
(coach, starts_at)
```

уже обеспечивает основной access pattern.

---

# 85. Обязательные transaction tests

Обычный `TestCase` недостаточен для полноценной проверки части lock-сценариев.

Concurrency tests должны использовать:

```text
TransactionTestCase
```

и реальные конкурентные транзакции PostgreSQL.

Особенно тестируются:

```text
два одновременных PRESENT
один последний visit
```

Ожидаемый результат:

```text
только один CONSUME
второй Attendance → UNCOVERED
или другой Subscription
```

Но никогда:

```text
balance = -1
```

---

# 86. Критические DB invariants

PostgreSQL гарантирует:

```text
один Attendance на Student + Lesson

один LessonResponse на Student + Lesson

один roster entry на Student + Lesson

один active SubscriptionUsage на Attendance

один active Usage на MakeupEntitlement

один GRANT на Subscription

один CONSUME на Usage

один RESTORE на Usage

корректный знак Ledger delta

валидные временные диапазоны
```

---

# 87. Критические service invariants

Application layer гарантирует:

```text
родитель меняет только своего Student

тренер отмечает только своё Lesson

RSVP только в допустимое время

Attendance после CLOSED не меняется

ICE не расходует HALL Subscription

HALL не расходует ICE Subscription

истёкший Subscription не используется
без MakeupEntitlement

MakeupEntitlement не создаёт extra visit

Subscription balance никогда не становится отрицательным

CLOSED Lesson имеет полный Attendance roster
```

---

# 88. Итоговая схема

```text
User
 ├── ExternalIdentity
 ├── StudentAccess ───────────────┐
 └── CoachProfile                │
                                 ▼
                              Student
                              /     \
                             /       \
                            ▼         ▼
                  GroupMembership  Subscription
                         │          /    |    \
                         ▼         /     |     \
                  TrainingGroup   ▼      ▼      ▼
                         │      Ledger  Usage  MakeupEntitlement
                         ▼               ▲            │
                  ScheduleTemplate       │            │
                         │               │            │
                         ▼               │            │
                      Lesson ─────── Attendance       │
                     /  |  \            ▲             │
                    /   |   \           │             │
                   ▼    ▼    ▼          │             │
                Roster RSVP Enrollment  │             │
                   │                    │             │
                   └────────────────────┘             │
                                                     │
AbsenceJustification ────────────────────────────────┘
```

---

# 89. Источники истины

Это должно быть явно зафиксировано в кодовой базе.

```text
Фактическое посещение:
Attendance

Кто приглашён на занятие:
LessonRosterEntry

Планирует ли прийти:
LessonResponse

Количество оставшихся занятий:
SUM(SubscriptionLedgerEntry.delta)

Каким абонементом покрыто посещение:
active SubscriptionUsage

Медицинское основание:
AbsenceJustification

Право использовать остаток позже:
MakeupEntitlement
```

Ни одна из этих сущностей не должна пытаться подменять другую.

---

# 90. Рекомендуемый следующий этап реализации

После утверждения этой схемы модели уже достаточно стабильны для начала разработки.

Рациональный порядок:

```text
1. accounts models

2. scheduling models
   + lesson generation
   + publication
   + roster snapshot
   + RSVP

3. Attendance models
   + trainer interface

4. Subscription models
   + ledger

5. Coverage service
   + concurrency tests

6. Medical / Makeup flow

7. Lesson closing
   + financial report

8. Django Admin

9. User frontend

10. Security / deployment
```

Критическую часть `Attendance → SubscriptionUsage → Ledger` следует реализовать тестами раньше UI, поскольку именно она содержит основные финансовые и concurrency-инварианты системы.