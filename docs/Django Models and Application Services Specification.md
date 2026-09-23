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

core/
    permissions.py

ice_school/
    workflows.py
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

Cross-app orchestration, которое по смыслу затрагивает несколько доменов,
размещается в composition root `ice_school.workflows`, а не в нижнем
`core` и не создаёт обратную зависимость между domain apps.

Audit persistence централизуется через:

```text
audit.services.record_event()
audit.services.event_exists()
```

Domain services могут иметь тонкие локальные wrappers для удобства payload,
но не должны писать `AuditEvent.objects.create()` напрямую.

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

    revocation_reason = models.CharField(
        max_length=32,
        choices=["attendance_correction", "administrative"],
        null=True,
        blank=True,
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
    condition=models.Q(status__in=["pending", "verified"]),
    name="absence_active_student_lesson_uq",
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
            revocation_reason__isnull=False,
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

# 20. SubscriptionPlan и SubscriptionPlanAllowance

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

---

# 27. Запрещённая архитектура

Нельзя выполнять финансово-учётную цепочку внутри `Attendance.save()` или Django signals.

Критическая операция должна быть явно видна в application service:

```text
Attendance
→ AttendanceCoverage
→ optional SubscriptionLedgerEntry
→ AuditEvent
```

One-time coverage не создаёт ledger entry; allowance-backed coverage создаёт `CONSUME/RESTORE` только через service layer.

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

Это low-level scheduling transition. Интерактивные callers не должны вызывать
его напрямую: административный UI/CLI использует
`ice_school.workflows.reschedule_lesson_with_entitlements()`, чтобы перенос
Lesson и entitlement'ов был одной транзакцией.

```python
reschedule_lesson(
    *,
    lesson_id: UUID,
    new_starts_at: datetime,
    new_ends_at: datetime,
    actor: User,
    reason: CancellationReason,
    now: datetime,
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

Активные `LessonEnrollment` переносятся на replacement как новые enrollment
records. RSVP при этом не копируются.

После этого replacement публикуется отдельно.

---

# 37. Entitlements при переносе школы

Создание make-up entitlement не находится в `scheduling.services`.

Subscription-domain service:

```python
subscriptions.services.apply_school_reschedule_entitlements(
    *,
    source_lesson_id: UUID,
    replacement_lesson_id: UUID,
    actor: User,
)
```

Для атомарного пользовательского/административного сценария используется
cross-app orchestration:

```python
ice_school.workflows.reschedule_lesson_with_entitlements(...)
```

Workflow в одной транзакции вызывает:

```text
scheduling.services.reschedule_lesson()
        ↓
subscriptions.services.apply_school_reschedule_entitlements()
```

Неиспользованный и неотменённый `OneTimeEntitlement`, привязанный к
исходному Lesson и совпадающий по категории, перепривязывается к replacement.
Использованный one-time entitlement остаётся исторически привязанным к исходному
занятию. Новое разовое право при переносе не создаётся.

Если replacement Lesson выходит за обычный срок, для участников с `RSVP=YES`
определяется `SubscriptionAllowance` той же категории, который мог покрыть
исходный Lesson. При необходимости создаётся
`MakeupEntitlement(source_subscription_allowance=..., target_lesson=replacement)`.

Никакого `GRANT` при этом не создаётся.

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

# 49. subscriptions.selectors.allowance_balance()

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

Если предыдущая justification была автоматически отозвана из-за коррекции
Attendance в PRESENT и Attendance снова становится ABSENT, создаётся **новая**
PENDING justification с новым UUID. Старые REVOKED/REJECTED строки не
перезаписываются. Административно REVOKED justification автоматически заново
не заявляется.

Справку в систему не загружаем.

---

# 65. attendance.services.verify_medical_absence()

Lock order для коррекции/верификации:

```text
Lesson → Attendance → AbsenceJustification → entitlement/allowance
```

При VERIFIED определяется конкретный `SubscriptionAllowance` категории исходного Lesson, который мог покрыть пропуск. Если allowance найден, создаётся MakeupEntitlement на него. При повторном заявлении создаётся новая justification, а новая верификация
создаёт новый MakeupEntitlement. Отменённый entitlement не реактивируется и
сохраняет прежние даты/статус как историческую запись. Если подходящего allowance нет, justification остаётся VERIFIED, но entitlement не создаётся.

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

`Attendance.status=PRESENT` и нет active `AttendanceCoverage`.

---

# 72. Expired unused report

Отчёт агрегирует positive balances по `SubscriptionAllowance` истёкших Subscription и показывает ICE/HALL отдельно, плюс доступные MakeupEntitlement.

---

# 73. Admin protection

Прямое изменение operational models запрещено даже для обычного `is_staff`.

Просмотр использует стандартные Django model permissions. Service-backed
actions дополнительно требуют соответствующий `change_*` permission.

Read-only через обычный Admin: `Attendance`, `AttendanceCoverage`, `SubscriptionLedgerEntry`, lifecycle fields justification/lesson. Изменения выполняются application services.

---

# 74. Django Admin — SubscriptionLedgerEntry

`list/view` разрешены, `add/change/delete` запрещены. Записи создают только `issue_subscription()`, `adjust_allowance()`, `assign_attendance_coverage()` и `reverse_attendance_coverage()`.

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

Запись audit events выполняется через `audit.services.record_event()`.

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

---

# 90. Рекомендуемый следующий этап реализации

После утверждения консолидированной модели реализация идёт в порядке: accounts/scheduling → Attendance → allowance-based subscriptions + ledger → AttendanceCoverage + concurrency tests → OneTimeEntitlement → Makeup flow → Admin/UI → future Billing.

Server-rendered presentation layer должен соблюдать UX-контракт раздела:

```text
«Mobile-first UI и адаптивность»
```

в основной системной спецификации.

Mobile/desktop responsive presentation не меняет domain semantics: views/templates не создают и не изменяют Lesson lifecycle, Attendance, Coverage, Ledger или entitlements напрямую; пользовательские действия вызывают application services.

Критическую цепочку `Attendance → AttendanceCoverage → optional Ledger` необходимо покрыть transaction tests до разработки финансового UI.


---

# 91. Versioning ScheduleTemplate

ScheduleTemplate, который уже используется для генерации, не редактируется
in-place. Для изменения используется:

```python
version_schedule_template(
    *,
    template_id: UUID,
    effective_from: date,
    actor: User,
    now: datetime,
    ...new_values,
) -> ScheduleTemplate
```

Старая версия сохраняет `is_active=True`, но получает
`valid_until = effective_from - 1`, поэтому cron продолжает генерировать
занятия старой версии до границы периода.

Операция блокируется, если в затрагиваемом диапазоне существуют:

- RSVP_OPEN / CONFIRMED / COMPLETED / CLOSED lessons;
- DRAFT lesson с активным LessonEnrollment;
- DRAFT lesson с активным OneTimeEntitlement.

Безопасные DRAFT без броней отменяются с audit event, после чего новая версия
может генерироваться начиная с `effective_from`.
