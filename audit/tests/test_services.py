from uuid import uuid4

import pytest

from audit.services import record_event


@pytest.mark.django_db
def test_record_event_preserves_explicit_correlation_id():
    correlation_id = uuid4()
    aggregate_id = uuid4()

    event = record_event(
        event_type="TestEvent",
        aggregate_type="TestAggregate",
        aggregate_id=aggregate_id,
        actor=None,
        payload={"value": 1},
        correlation_id=correlation_id,
    )

    assert event.correlation_id == correlation_id
    assert event.aggregate_id == aggregate_id
    assert event.payload == {"value": 1}
