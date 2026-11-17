import uuid
from datetime import timedelta

import pytest

from scheduling.message_models import BookingMessage
from scheduling.tests.test_inventory import stock, url
from scheduling.tests.test_session_changes import notifications, booked, HEADER
from scheduling.tests.test_workshop_schedule import daytime, second_session, data_for, path

pytestmark = pytest.mark.django_db


def series(s):
    s.session.series_id = uuid.uuid4()
    s.session.save()
    other = second_session(s)
    other.series_id = s.session.series_id
    other.save()
    return other


def endpoint(s):
    return path(s, 'edit') + f'?session={s.session.pk}'


def values(s, **changes):
    return {**data_for(s), 'scope': 'following', 'notify_customers': '1', **changes}


def preflight(s, data):
    return s.client.post(endpoint(s), data, HTTP_X_SESSION_PREFLIGHT='1')


@pytest.mark.parametrize('headers', [{}, HEADER])
def test_setup_and_calendar_share_recurring_editor(notifications, headers):
    s = notifications
    series(s)
    page = s.client.get(url(s, 'session_edit', pk=s.session.pk), **headers)
    assert page.status_code == 200
    assert b'data-recurring-editor' in page.content
    assert len(page.context['rows']) == 2
    assert page.context['form']['preview_token'].value()
    assert b'Preview message' not in page.content
    assert page.content.count(b'class="session-notify"') == 1
    assert page.content.index(b'data-session-live-review') < page.content.index(b'class="session-notify"')


@pytest.mark.parametrize('scope', ['one', 'following'])
def test_scopes_duration_capacity_notifications_and_retry(notifications, scope):
    s = notifications
    other = series(s)
    original_start, original_series = s.session.starts_at, s.session.series_id
    booking = booked(s, 2)
    data = values(s, scope=scope, duration_minutes=75, capacity=4)
    result = preflight(s, data)
    assert result.status_code == 200, result.content
    assert 'session-change-highlight' in result.json()['review_html']
    assert '<del>' in result.json()['review_html']
    assert 'Seats:' in result.json()['review_html']
    data['preview_token'] = result.json()['review_token']
    for _ in range(2):
        assert s.client.post(endpoint(s), data, **HEADER).json() == {'saved': True}
    s.session.refresh_from_db()
    other.refresh_from_db()
    booking.refresh_from_db()
    assert s.session.capacity == 4
    assert s.session.ends_at - s.session.starts_at == timedelta(minutes=75)
    assert booking.starts_at == s.session.starts_at
    assert s.session.series_id == (None if scope == 'one' else original_series)
    assert other.series_id == original_series
    assert other.starts_at == original_start + timedelta(days=7, hours=0 if scope == 'one' else 2)
    assert BookingMessage.objects.filter(notice_kind='rescheduled').count() == 1


def test_all_conflicts_listed_and_forged_save_cannot_partially_write(notifications):
    s = notifications
    other = series(s)
    first = s.session
    booked(s, 2)
    s.session = other
    booked(s, 2)
    s.session = first
    data = values(s, capacity=1)
    result = preflight(s, data)
    assert result.status_code == 400
    assert '2 to resolve' in result.json()['review_html']
    assert not result.json()['review_token']
    response = s.client.post(endpoint(s), data, **HEADER)
    assert response.context['conflict_count'] == 2
    assert b'class="primary" disabled' in response.content
    first.refresh_from_db()
    other.refresh_from_db()
    assert first.capacity == other.capacity == 100
    assert not BookingMessage.objects.exists()


def test_stale_preview_and_notifications_off(notifications):
    s = notifications
    series(s)
    data = values(s, notify_customers='0')
    result = preflight(s, data)
    assert result.status_code == 200
    data['preview_token'] = result.json()['review_token']
    booked(s)
    response = s.client.post(endpoint(s), data, **HEADER)
    assert response.context['conflict_count'] == 1
    data['preview_token'] = ''
    result = preflight(s, data)
    data['preview_token'] = result.json()['review_token']
    assert s.client.post(endpoint(s), data, **HEADER).json()['saved']
    assert not BookingMessage.objects.exists()


def test_following_excludes_past_detached_and_other_series(notifications):
    from django.utils import timezone
    s = notifications
    other = series(s)
    past = second_session(s)
    past.series_id = s.session.series_id
    past.starts_at = timezone.now() - timedelta(days=1)
    past.ends_at = past.starts_at + timedelta(hours=1)
    past.save()
    detached = second_session(s)
    detached.starts_at += timedelta(days=14)
    detached.ends_at += timedelta(days=14)
    detached.save()
    result = preflight(s, values(s))
    assert result.status_code == 200
    from django.core import signing
    from scheduling.workshop_schedule import SALT
    signed = signing.loads(result.json()['review_token'], salt=SALT)
    assert signed['sessions'] == [s.session.pk, other.pk]


def test_overlap_conflicts_are_listed_without_writing(notifications):
    s = notifications
    series(s)
    blocker = second_session(s)
    blocker.starts_at = s.session.starts_at + timedelta(hours=2)
    blocker.ends_at = blocker.starts_at + timedelta(hours=1)
    blocker.save()
    result = preflight(s, values(s))
    assert result.status_code == 400
    assert '1 to resolve' in result.json()['review_html']
    assert not result.json()['review_token']
