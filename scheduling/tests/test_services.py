from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from ice_school.workflows import reschedule_lesson_with_entitlements

from accounts.models import CoachProfile
from attendance.models import Attendance
from audit.models import AuditEvent
from scheduling.models import Lesson, LessonType, TrainingGroup, Venue
from scheduling.services import complete_lesson

User = get_user_model()


@pytest.fixture
def coach_user(db):
    return User.objects.create_user(username="coach-lifecycle", password="test")


@pytest.fixture
def school_context(db, coach_user):
    coach = CoachProfile.objects.create(user=coach_user, display_name="Coach")
    group = TrainingGroup.objects.create(code="g-life", name="Group")
    venue = Venue.objects.create(code="v-life", name="Venue")
    lesson_type = LessonType.objects.create(
        code="ice-life",
        name="Ice",
        subscription_category="ice",
    )
    return coach, group, venue, lesson_type


def make_lesson(*, school_context, status, starts_at):
    coach, group, venue, lesson_type = school_context
    return Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=status,
    )


@pytest.mark.django_db
def test_complete_lesson_transitions_confirmed_to_completed(school_context):
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = make_lesson(
        school_context=school_context,
        status=Lesson.Status.CONFIRMED,
        starts_at=starts_at,
    )
    now = starts_at + timedelta(hours=1, minutes=1)

    completed = complete_lesson(lesson_id=lesson.id, now=now)

    assert completed.status == Lesson.Status.COMPLETED
    assert completed.completed_at == now


@pytest.mark.django_db
def test_complete_lesson_rejects_early_completion(school_context):
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = make_lesson(
        school_context=school_context,
        status=Lesson.Status.CONFIRMED,
        starts_at=starts_at,
    )

    with pytest.raises(ValidationError):
        complete_lesson(
            lesson_id=lesson.id,
            now=starts_at + timedelta(minutes=30),
        )


@pytest.mark.django_db
def test_complete_lesson_rejects_wrong_state(school_context):
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = make_lesson(
        school_context=school_context,
        status=Lesson.Status.RSVP_OPEN,
        starts_at=starts_at,
    )

    with pytest.raises(ValidationError):
        complete_lesson(
            lesson_id=lesson.id,
            now=starts_at + timedelta(hours=2),
        )


from accounts.models import Student, StudentAccess
from scheduling.models import (
    GroupMembership,
    ScheduleTemplate,
    LessonEnrollment,
    LessonResponse,
    LessonRosterEntry,
)
from scheduling.selectors import (
    get_coach_schedule,
    get_student_schedule,
)
from scheduling.services import (
    add_lesson_enrollment,
    create_group_membership,
    cancel_lesson,
    confirm_lesson,
    evaluate_lesson_viability,
    generate_lessons,
    publish_daily_schedule,
    publish_lesson,
    reschedule_lesson,
    set_lesson_response,
)
from subscriptions.models import (
    AttendanceCoverage,
    MakeupEntitlement,
    OneTimeEntitlement,
    SubscriptionPlan,
    SubscriptionPlanAllowance,
)
from subscriptions.services import issue_subscription


@pytest.fixture
def student(db):
    return Student.objects.create(display_name="Student A")


@pytest.fixture
def second_student(db):
    return Student.objects.create(display_name="Student B")


@pytest.fixture
def guardian(db):
    return User.objects.create_user(username="guardian", password="test")


@pytest.fixture
def admin(db):
    return User.objects.create_user(
        username="admin-scheduling",
        password="test",
        is_staff=True,
        is_superuser=True,
    )


def add_membership(*, student, group, starts_on, ends_on=None):
    return GroupMembership.objects.create(
        student=student,
        group=group,
        starts_on=starts_on,
        ends_on=ends_on,
    )


@pytest.mark.django_db
def test_publish_lesson_snapshots_group_and_enrollment(
    school_context,
    student,
    second_student,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )
    membership = add_membership(
        student=student,
        group=group,
        starts_on=date(2026, 9, 1),
    )
    enrollment = LessonEnrollment.objects.create(
        lesson=lesson,
        student=second_student,
        reason=LessonEnrollment.Reason.GUEST,
        created_by=coach_user,
    )

    published = publish_lesson(
        lesson_id=lesson.id,
        actor=coach_user,
        now=starts_at - timedelta(hours=3),
    )

    assert published.status == Lesson.Status.RSVP_OPEN
    group_roster = LessonRosterEntry.objects.get(
        lesson=lesson,
        student=student,
    )
    enrollment_roster = LessonRosterEntry.objects.get(
        lesson=lesson,
        student=second_student,
    )
    assert group_roster.source == LessonRosterEntry.Source.GROUP
    assert group_roster.group_membership_id == membership.id
    assert enrollment_roster.source == LessonRosterEntry.Source.ENROLLMENT
    assert enrollment_roster.lesson_enrollment_id == enrollment.id


@pytest.mark.django_db
def test_publish_lesson_excludes_expired_membership(
    school_context,
    student,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.DRAFT,
    )
    add_membership(
        student=student,
        group=group,
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 9, 10),
    )

    publish_lesson(
        lesson_id=lesson.id,
        actor=coach_user,
        now=starts_at - timedelta(hours=3),
    )

    assert not LessonRosterEntry.objects.filter(
        lesson=lesson,
        student=student,
    ).exists()


@pytest.mark.django_db
def test_set_lesson_response_requires_student_access(
    school_context,
    student,
    guardian,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=1),
        decision_deadline=starts_at - timedelta(minutes=30),
        status=Lesson.Status.RSVP_OPEN,
    )
    LessonRosterEntry.objects.create(
        lesson=lesson,
        student=student,
        source=LessonRosterEntry.Source.MANUAL,
        added_by=coach_user,
    )

    with pytest.raises(PermissionDenied):
        set_lesson_response(
            actor=guardian,
            student_id=student.id,
            lesson_id=lesson.id,
            status=LessonResponse.Status.YES,
            now=starts_at - timedelta(hours=2),
        )

    StudentAccess.objects.create(
        user=guardian,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )
    response = set_lesson_response(
        actor=guardian,
        student_id=student.id,
        lesson_id=lesson.id,
        status=LessonResponse.Status.YES,
        now=starts_at - timedelta(hours=2),
    )
    assert response.status == LessonResponse.Status.YES


@pytest.mark.django_db
def test_set_lesson_response_is_last_write_wins(
    school_context,
    student,
    guardian,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=1),
        decision_deadline=starts_at - timedelta(minutes=30),
        status=Lesson.Status.RSVP_OPEN,
    )
    LessonRosterEntry.objects.create(
        lesson=lesson,
        student=student,
        source=LessonRosterEntry.Source.MANUAL,
        added_by=coach_user,
    )
    StudentAccess.objects.create(
        user=guardian,
        student=student,
        role=StudentAccess.Role.GUARDIAN,
    )

    first = set_lesson_response(
        actor=guardian,
        student_id=student.id,
        lesson_id=lesson.id,
        status=LessonResponse.Status.YES,
        now=starts_at - timedelta(hours=2),
    )
    second = set_lesson_response(
        actor=guardian,
        student_id=student.id,
        lesson_id=lesson.id,
        status=LessonResponse.Status.NO,
        now=starts_at - timedelta(hours=2),
    )

    assert second.id == first.id
    assert second.status == LessonResponse.Status.NO
    assert LessonResponse.objects.filter(
        lesson=lesson,
        student=student,
    ).count() == 1


@pytest.mark.django_db
def test_evaluate_lesson_viability_snapshots_counts(
    school_context,
    student,
    second_student,
    guardian,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=2,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.RSVP_OPEN,
    )
    for s in (student, second_student):
        LessonRosterEntry.objects.create(
            lesson=lesson,
            student=s,
            source=LessonRosterEntry.Source.MANUAL,
            added_by=coach_user,
        )
    LessonResponse.objects.create(
        lesson=lesson,
        student=student,
        status=LessonResponse.Status.YES,
        updated_by=guardian,
    )

    result = evaluate_lesson_viability(
        lesson_id=lesson.id,
        now=starts_at - timedelta(minutes=30),
    )

    lesson.refresh_from_db()
    assert result.yes_count == 1
    assert result.no_count == 0
    assert result.no_response_count == 1
    assert result.minimum_met is False
    assert lesson.decision_yes_count == 1
    assert lesson.decision_no_response_count == 1


@pytest.mark.django_db
def test_confirm_lesson_does_not_require_minimum(
    school_context,
    admin,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=4,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.RSVP_OPEN,
    )

    confirmed = confirm_lesson(
        lesson_id=lesson.id,
        actor=admin,
        now=starts_at - timedelta(minutes=30),
    )

    assert confirmed.status == Lesson.Status.CONFIRMED
    assert confirmed.confirmed_by_id == admin.id


@pytest.mark.django_db
def test_cancel_lesson_records_reason(
    school_context,
    admin,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.CONFIRMED,
    )

    cancelled = cancel_lesson(
        lesson_id=lesson.id,
        actor=admin,
        reason=Lesson.CancellationReason.LOW_ATTENDANCE,
        now=starts_at - timedelta(minutes=30),
    )

    assert cancelled.status == Lesson.Status.CANCELLED
    assert cancelled.cancellation_reason == Lesson.CancellationReason.LOW_ATTENDANCE


@pytest.mark.django_db
def test_reschedule_creates_draft_without_copying_rsvp(
    school_context,
    student,
    guardian,
    admin,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    source = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.RSVP_OPEN,
    )
    LessonResponse.objects.create(
        lesson=source,
        student=student,
        status=LessonResponse.Status.YES,
        updated_by=guardian,
    )

    replacement = reschedule_lesson(
        lesson_id=source.id,
        new_starts_at=starts_at + timedelta(days=1),
        new_ends_at=starts_at + timedelta(days=1, hours=1),
        actor=admin,
        reason=Lesson.CancellationReason.ADMINISTRATIVE,
        now=starts_at - timedelta(hours=3),
    )

    source.refresh_from_db()
    assert source.status == Lesson.Status.CANCELLED
    assert source.replacement_lesson_id == replacement.id
    assert replacement.status == Lesson.Status.DRAFT
    assert replacement.responses.count() == 0


@pytest.mark.django_db
def test_reschedule_beyond_subscription_creates_targeted_makeup(
    school_context,
    student,
    guardian,
    admin,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(2026, 9, 29, 15, 0, tzinfo=dt_timezone.utc)
    source = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.RSVP_OPEN,
    )
    LessonResponse.objects.create(
        lesson=source,
        student=student,
        status=LessonResponse.Status.YES,
        updated_by=guardian,
    )
    plan = SubscriptionPlan.objects.create(
        code="reschedule-ice",
        name="Reschedule ICE",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category="ice",
        visit_limit=1,
    )
    subscription = issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=admin,
    )

    replacement = reschedule_lesson_with_entitlements(
        lesson_id=source.id,
        new_starts_at=datetime(
            2026,
            10,
            2,
            15,
            0,
            tzinfo=dt_timezone.utc,
        ),
        new_ends_at=datetime(
            2026,
            10,
            2,
            16,
            0,
            tzinfo=dt_timezone.utc,
        ),
        actor=admin,
        reason=Lesson.CancellationReason.ADMINISTRATIVE,
        now=starts_at - timedelta(hours=3),
    )

    makeup = MakeupEntitlement.objects.get(
        student=student,
        source_lesson=source,
        target_lesson=replacement,
    )
    assert makeup.reason == MakeupEntitlement.Reason.SCHOOL_RESCHEDULE
    assert makeup.source_subscription_allowance.subscription_id == subscription.id
    assert makeup.valid_from == date(2026, 10, 2)
    assert makeup.valid_until == date(2026, 10, 2)



@pytest.mark.django_db
def test_generate_lessons_creates_matching_weekdays_and_is_idempotent(
    school_context,
    admin,
    settings,
):
    coach, group, venue, lesson_type = school_context
    group.default_minimum_attendees = 4
    group.save(update_fields=["default_minimum_attendees"])
    template = ScheduleTemplate.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        weekday=1,
        start_time=datetime(2026, 9, 1, 18, 0).time(),
        duration_minutes=60,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        is_active=True,
    )
    settings.SCHEDULING_RSVP_DEADLINE_MINUTES_BEFORE_START = 180
    settings.SCHEDULING_DECISION_DEADLINE_MINUTES_BEFORE_START = 120

    first = generate_lessons(
        template_id=template.id,
        from_date=date(2026, 9, 1),
        until_date=date(2026, 9, 15),
        actor=admin,
    )
    second = generate_lessons(
        template_id=template.id,
        from_date=date(2026, 9, 1),
        until_date=date(2026, 9, 15),
        actor=admin,
    )

    assert [lesson.id for lesson in second] == [lesson.id for lesson in first]
    assert len(first) == 3
    assert all(lesson.status == Lesson.Status.DRAFT for lesson in first)
    assert all(lesson.minimum_attendees == 4 for lesson in first)
    assert all(
        lesson.starts_at - lesson.rsvp_deadline == timedelta(hours=3)
        for lesson in first
    )
    assert all(
        lesson.starts_at - lesson.decision_deadline == timedelta(hours=2)
        for lesson in first
    )


@pytest.mark.django_db
def test_generate_lessons_uses_template_minimum_override(
    school_context,
    admin,
):
    coach, group, venue, lesson_type = school_context
    group.default_minimum_attendees = 5
    group.save(update_fields=["default_minimum_attendees"])
    template = ScheduleTemplate.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        weekday=1,
        start_time=datetime(2026, 9, 1, 18, 0).time(),
        duration_minutes=60,
        valid_from=date(2026, 9, 1),
        minimum_attendees_override=2,
        is_active=True,
    )

    lessons = generate_lessons(
        template_id=template.id,
        from_date=date(2026, 9, 1),
        until_date=date(2026, 9, 1),
        actor=admin,
    )

    assert len(lessons) == 1
    assert lessons[0].minimum_attendees == 2


@pytest.mark.django_db
def test_generate_lessons_rejects_invalid_deadline_policy(
    school_context,
    admin,
    settings,
):
    coach, group, venue, lesson_type = school_context
    template = ScheduleTemplate.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        weekday=1,
        start_time=datetime(2026, 9, 1, 18, 0).time(),
        duration_minutes=60,
        valid_from=date(2026, 9, 1),
        is_active=True,
    )
    settings.SCHEDULING_RSVP_DEADLINE_MINUTES_BEFORE_START = 60
    settings.SCHEDULING_DECISION_DEADLINE_MINUTES_BEFORE_START = 120

    with pytest.raises(ValidationError):
        generate_lessons(
            template_id=template.id,
            from_date=date(2026, 9, 1),
            until_date=date(2026, 9, 1),
            actor=admin,
        )


@pytest.mark.django_db
def test_publish_daily_schedule_only_publishes_requested_date(
    school_context,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    first_start = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    second_start = first_start + timedelta(days=1)
    first = make_lesson(
        school_context=school_context,
        status=Lesson.Status.DRAFT,
        starts_at=first_start,
    )
    second = make_lesson(
        school_context=school_context,
        status=Lesson.Status.DRAFT,
        starts_at=second_start,
    )

    published = publish_daily_schedule(
        school_date=date(2026, 9, 15),
        now=first_start - timedelta(hours=4),
    )

    first.refresh_from_db()
    second.refresh_from_db()
    assert [lesson.id for lesson in published] == [first.id]
    assert first.status == Lesson.Status.RSVP_OPEN
    assert second.status == Lesson.Status.DRAFT


@pytest.mark.django_db
def test_add_lesson_enrollment_to_published_lesson_adds_roster(
    school_context,
    student,
    admin,
):
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = make_lesson(
        school_context=school_context,
        status=Lesson.Status.RSVP_OPEN,
        starts_at=starts_at,
    )

    enrollment = add_lesson_enrollment(
        lesson_id=lesson.id,
        student_id=student.id,
        reason=LessonEnrollment.Reason.GUEST,
        actor=admin,
    )

    roster = LessonRosterEntry.objects.get(
        lesson=lesson,
        student=student,
    )
    assert roster.source == LessonRosterEntry.Source.ENROLLMENT
    assert roster.lesson_enrollment_id == enrollment.id
    assert roster.is_active is True


@pytest.mark.django_db
def test_add_lesson_enrollment_to_draft_defers_roster_until_publish(
    school_context,
    student,
    admin,
):
    starts_at = datetime(2026, 9, 15, 15, 0, tzinfo=dt_timezone.utc)
    lesson = make_lesson(
        school_context=school_context,
        status=Lesson.Status.DRAFT,
        starts_at=starts_at,
    )

    enrollment = add_lesson_enrollment(
        lesson_id=lesson.id,
        student_id=student.id,
        reason=LessonEnrollment.Reason.MAKEUP,
        actor=admin,
    )

    assert not LessonRosterEntry.objects.filter(
        lesson=lesson,
        student=student,
    ).exists()

    publish_lesson(
        lesson_id=lesson.id,
        actor=admin,
        now=starts_at - timedelta(hours=4),
    )

    roster = LessonRosterEntry.objects.get(
        lesson=lesson,
        student=student,
    )
    assert roster.lesson_enrollment_id == enrollment.id



@pytest.mark.django_db
def test_student_schedule_uses_roster_snapshot_and_excludes_draft(
    school_context,
    student,
    coach_user,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    visible = make_lesson(
        school_context=school_context,
        status=Lesson.Status.RSVP_OPEN,
        starts_at=starts_at,
    )
    hidden = make_lesson(
        school_context=school_context,
        status=Lesson.Status.DRAFT,
        starts_at=starts_at + timedelta(days=1),
    )
    cancelled = make_lesson(
        school_context=school_context,
        status=Lesson.Status.RSVP_OPEN,
        starts_at=starts_at + timedelta(days=2),
    )
    cancelled.status = Lesson.Status.CANCELLED
    cancelled.cancelled_at = starts_at
    cancelled.cancellation_reason = Lesson.CancellationReason.ADMINISTRATIVE
    cancelled.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancellation_reason",
        ]
    )

    for lesson in (visible, hidden, cancelled):
        LessonRosterEntry.objects.create(
            lesson=lesson,
            student=student,
            source=LessonRosterEntry.Source.MANUAL,
            added_by=coach_user,
        )

    result = get_student_schedule(
        student_id=student.id,
        from_date=date(2026, 9, 15),
        until_date=date(2026, 9, 17),
    )

    assert [item.lesson.id for item in result] == [
        visible.id,
        cancelled.id,
    ]


@pytest.mark.django_db
def test_student_schedule_includes_response_attendance_and_coverage(
    school_context,
    student,
    guardian,
    coach_user,
):
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        status=Lesson.Status.COMPLETED,
        starts_at=starts_at,
    )
    LessonRosterEntry.objects.create(
        lesson=lesson,
        student=student,
        source=LessonRosterEntry.Source.MANUAL,
        added_by=coach_user,
    )
    LessonResponse.objects.create(
        lesson=lesson,
        student=student,
        status=LessonResponse.Status.YES,
        updated_by=guardian,
    )
    attendance = Attendance.objects.create(
        lesson=lesson,
        student=student,
        status=Attendance.Status.PRESENT,
        marked_by=coach_user,
    )
    entitlement = OneTimeEntitlement.objects.create(
        student=student,
        lesson=lesson,
        entitlement_type=OneTimeEntitlement.Type.SINGLE_ICE,
        category="ice",
        created_by=coach_user,
    )
    coverage = AttendanceCoverage.objects.create(
        attendance=attendance,
        one_time_entitlement=entitlement,
        created_by=coach_user,
    )

    result = get_student_schedule(
        student_id=student.id,
        from_date=date(2026, 9, 15),
        until_date=date(2026, 9, 15),
    )

    assert len(result) == 1
    assert result[0].response_status == LessonResponse.Status.YES
    assert result[0].attendance_status == Attendance.Status.PRESENT
    assert result[0].coverage.id == coverage.id


@pytest.mark.django_db
def test_coach_schedule_counts_only_active_roster(
    school_context,
    student,
    second_student,
    guardian,
    coach_user,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    lesson = make_lesson(
        school_context=school_context,
        status=Lesson.Status.RSVP_OPEN,
        starts_at=starts_at,
    )
    active = LessonRosterEntry.objects.create(
        lesson=lesson,
        student=student,
        source=LessonRosterEntry.Source.MANUAL,
        added_by=coach_user,
    )
    inactive = LessonRosterEntry.objects.create(
        lesson=lesson,
        student=second_student,
        source=LessonRosterEntry.Source.MANUAL,
        added_by=coach_user,
        is_active=False,
        deactivated_at=starts_at - timedelta(days=1),
        deactivated_by=coach_user,
    )
    LessonResponse.objects.create(
        lesson=lesson,
        student=student,
        status=LessonResponse.Status.YES,
        updated_by=guardian,
    )
    LessonResponse.objects.create(
        lesson=lesson,
        student=second_student,
        status=LessonResponse.Status.YES,
        updated_by=guardian,
    )

    result = get_coach_schedule(
        coach_id=coach.id,
        from_date=date(2026, 9, 15),
        until_date=date(2026, 9, 15),
    )

    assert len(result) == 1
    assert result[0].roster_count == 1
    assert result[0].yes_count == 1
    assert result[0].no_count == 0
    assert result[0].no_response_count == 0


@pytest.mark.django_db
def test_coach_schedule_excludes_draft(
    school_context,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(
        2026,
        9,
        15,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    make_lesson(
        school_context=school_context,
        status=Lesson.Status.DRAFT,
        starts_at=starts_at,
    )

    result = get_coach_schedule(
        coach_id=coach.id,
        from_date=date(2026, 9, 15),
        until_date=date(2026, 9, 15),
    )

    assert result == ()



@pytest.mark.django_db
def test_group_membership_service_rejects_overlapping_interval(
    school_context,
    student,
    admin,
):
    coach, group, venue, lesson_type = school_context
    create_group_membership(
        student_id=student.id,
        group_id=group.id,
        starts_on=date(2026, 1, 1),
        ends_on=date(2026, 6, 30),
        actor=admin,
    )

    with pytest.raises(ValidationError):
        create_group_membership(
            student_id=student.id,
            group_id=group.id,
            starts_on=date(2026, 6, 15),
            ends_on=date(2026, 8, 31),
            actor=admin,
        )


@pytest.mark.django_db
def test_reschedule_copies_active_enrollments_without_rsvp(
    school_context,
    student,
    guardian,
    admin,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(
        2026,
        9,
        25,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    source = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.RSVP_OPEN,
    )
    LessonEnrollment.objects.create(
        lesson=source,
        student=student,
        reason=LessonEnrollment.Reason.GUEST,
        created_by=admin,
    )
    LessonResponse.objects.create(
        lesson=source,
        student=student,
        status=LessonResponse.Status.YES,
        updated_by=guardian,
    )

    replacement = reschedule_lesson(
        lesson_id=source.id,
        new_starts_at=starts_at + timedelta(days=1),
        new_ends_at=starts_at + timedelta(days=1, hours=1),
        actor=admin,
        reason=Lesson.CancellationReason.ADMINISTRATIVE,
        now=starts_at - timedelta(hours=3),
    )

    copied = LessonEnrollment.objects.get(
        lesson=replacement,
        student=student,
    )
    assert copied.reason == LessonEnrollment.Reason.GUEST
    assert not LessonResponse.objects.filter(
        lesson=replacement,
        student=student,
    ).exists()



@pytest.mark.django_db
def test_generate_lessons_uses_school_timezone_not_active_request_timezone(
    school_context,
    admin,
    settings,
):
    coach, group, venue, lesson_type = school_context
    settings.SCHOOL_TIME_ZONE = "Europe/Riga"
    timezone.activate("UTC")
    template = ScheduleTemplate.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        weekday=1,
        start_time=datetime(2026, 9, 1, 18, 0).time(),
        duration_minutes=60,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 1),
        is_active=True,
    )

    lessons = generate_lessons(
        template_id=template.id,
        from_date=date(2026, 9, 1),
        until_date=date(2026, 9, 1),
        actor=admin,
    )

    assert len(lessons) == 1
    assert lessons[0].starts_at.astimezone(dt_timezone.utc).hour == 15



@pytest.mark.django_db
def test_reschedule_workflow_rolls_back_when_entitlement_permission_missing(
    school_context,
    student,
    admin,
):
    coach, group, venue, lesson_type = school_context
    actor = User.objects.create_user(
        username="lesson-only-admin",
        password="test",
        is_staff=True,
    )
    from django.contrib.auth.models import Permission

    actor.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="scheduling",
            codename="change_lesson",
        )
    )
    starts_at = datetime(
        2026,
        9,
        25,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    source = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.RSVP_OPEN,
    )
    LessonResponse.objects.create(
        lesson=source,
        student=student,
        status=LessonResponse.Status.YES,
        updated_by=admin,
    )
    plan = SubscriptionPlan.objects.create(
        code="rollback-permission-plan",
        name="Rollback permission plan",
    )
    SubscriptionPlanAllowance.objects.create(
        plan=plan,
        category="ice",
        visit_limit=1,
    )
    issue_subscription(
        student_id=student.id,
        plan_id=plan.id,
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 9, 30),
        actor=admin,
    )

    with pytest.raises(PermissionDenied):
        reschedule_lesson_with_entitlements(
            lesson_id=source.id,
            new_starts_at=datetime(
                2026, 10, 2, 15, 0, tzinfo=dt_timezone.utc
            ),
            new_ends_at=datetime(
                2026, 10, 2, 16, 0, tzinfo=dt_timezone.utc
            ),
            actor=actor,
            reason=Lesson.CancellationReason.ADMINISTRATIVE,
            now=starts_at - timedelta(hours=3),
        )

    source.refresh_from_db()
    assert source.status == Lesson.Status.RSVP_OPEN
    assert source.replacement_lesson_id is None
    assert Lesson.objects.filter(
        replaced_lesson=source,
    ).count() == 0



@pytest.mark.django_db
def test_reschedule_transfers_unused_one_time_entitlement(
    school_context,
    student,
    admin,
):
    coach, group, venue, lesson_type = school_context
    starts_at = datetime(
        2026,
        9,
        25,
        15,
        0,
        tzinfo=dt_timezone.utc,
    )
    source = Lesson.objects.create(
        group=group,
        lesson_type=lesson_type,
        coach=coach,
        venue=venue,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=1),
        minimum_attendees=1,
        rsvp_deadline=starts_at - timedelta(hours=2),
        decision_deadline=starts_at - timedelta(hours=1),
        status=Lesson.Status.RSVP_OPEN,
    )
    entitlement = OneTimeEntitlement.objects.create(
        student=student,
        lesson=source,
        entitlement_type=OneTimeEntitlement.Type.SINGLE_ICE,
        category="ice",
        created_by=admin,
    )

    replacement = reschedule_lesson_with_entitlements(
        lesson_id=source.id,
        new_starts_at=starts_at + timedelta(days=1),
        new_ends_at=starts_at + timedelta(days=1, hours=1),
        actor=admin,
        reason=Lesson.CancellationReason.ADMINISTRATIVE,
        now=starts_at - timedelta(hours=3),
    )

    entitlement.refresh_from_db()
    assert entitlement.lesson_id == replacement.id
    assert AuditEvent.objects.filter(
        event_type="OneTimeEntitlementTransferred",
        aggregate_id=entitlement.id,
    ).exists()
