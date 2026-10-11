from unittest.mock import patch
import uuid

import pytest
from django.test import Client
from django.urls import reverse

from core.models import School
from scheduling import messaging as msg, appointments as ap
from scheduling.message_forms import MessageSettingsForm
from scheduling.message_models import BookingMessage
from scheduling.models import SchedulingMembership, SchedulingProfile
from scheduling.tests.test_messaging import enabled, capture
from scheduling.tests.test_lessons import music
from scheduling.tests.test_appointments import offer, booked

pytestmark = pytest.mark.django_db


def route(profile,name,**extra):
    return reverse("scheduling:"+name,kwargs={"school_slug":profile.school.slug,**extra})


def test_inbox_owner_settings_process_and_stale_edit(enabled,booking_member,booking_user,client,mailoutbox):
    client.force_login(booking_user)
    url=route(enabled,"messages")
    response=client.get(url)
    assert response.status_code==200 and b"No confirmation messages yet" in response.content
    assert "no-store" in response["Cache-Control"]
    revision=response.context["settings_form"].fields["edit_revision"].initial
    assert client.post(url,{"action":"settings","edit_revision":revision}).status_code==302
    assert client.post(url,{"action":"settings","edit_revision":revision,"message_preview_enabled":"on"}).status_code==400
    enabled.refresh_from_db();assert not enabled.message_preview_enabled
    item=capture(enabled)
    fresh=client.get(url).context["settings_form"].fields["edit_revision"].initial
    client.post(url,{"action":"settings","edit_revision":fresh,"message_preview_enabled":"on"})
    item.refresh_from_db();assert item.state=="draft"
    msg.change_message(booking_user,enabled.school.slug,item.public_id,"queue")
    assert client.post(url,{"action":"process","limit":1,"outcome":"success"}).status_code==302
    item.refresh_from_db();assert item.state=="simulated" and len(mailoutbox)==1
    assert client.post(url,{"action":"process","limit":100,"outcome":"live"}).status_code==400
    assert client.post(url,{"action":"bad"}).status_code==400


@pytest.mark.parametrize("role",["scheduler","viewer"])
def test_read_only_message_roles(enabled,booking_member,booking_user,client,role):
    booking_member.role=role;booking_member.save()
    item=capture(enabled)
    client.force_login(booking_user)
    for url in [route(enabled,"messages"),route(enabled,"message_detail",public_id=item.public_id)]:
        response=client.get(url)
        assert response.status_code==200
        assert b"Save settings" not in response.content and b"Queue local attempt" not in response.content
        assert client.post(url,{"action":"suppress","confirm":"yes"}).status_code==404


def test_detail_confirm_retry_suppression_and_scope(enabled,booking_member,booking_user,client):
    item=capture(enabled)
    client.force_login(booking_user)
    url=route(enabled,"message_detail",public_id=item.public_id)
    assert client.post(url,{"action":"suppress"}).status_code==400
    assert client.post(url,{"action":"bad","confirm":"yes"}).status_code==400
    msg.process_batch(enabled.pk,outcome="retry")
    response=client.get(url)
    assert b"Next local attempt" in response.content and b"Not accepted; safe to retry" in response.content
    assert client.post(url,{"action":"suppress","confirm":"yes"}).status_code==302
    assert b"Suppress message" not in client.get(url).content
    other=SchedulingProfile.objects.create(school=School.objects.create(slug="hidden-mail"),enabled=True)
    foreign=capture(other)
    assert client.get(route(enabled,"message_detail",public_id=foreign.public_id)).status_code==404
    assert client.get(route(enabled,"message_detail",public_id=uuid.uuid4())).status_code==404


def test_unknown_and_empty_attempt_history(enabled,booking_member,booking_user,client):
    item=capture(enabled)
    client.force_login(booking_user)
    url=route(enabled,"message_detail",public_id=item.public_id)
    assert b"No delivery attempts" in client.get(url).content
    msg.process_batch(enabled.pk,outcome="uncertain")
    result=client.get(url)
    assert b"outcome is unknown" in result.content and b"Queue local attempt" not in result.content


@pytest.mark.parametrize("unresolved", ["sending", "uncertain"])
def test_waiting_message_links_to_scoped_blocker(enabled, booking_member, booking_user, client, unresolved):
    older = capture(enabled)
    claimed = msg.claim(enabled.pk)
    if unresolved == "uncertain":
        msg.finish(claimed, "uncertain")
    newer = capture(enabled, event_key="booking:newer")
    client.force_login(booking_user)
    blocker_url = route(enabled, "message_detail", public_id=older.public_id)
    response = client.get(route(enabled, "messages"))
    assert b"Waiting for" in response.content and blocker_url.encode() in response.content
    response = client.get(route(enabled, "message_detail", public_id=newer.public_id))
    assert response.context["blocker"].pk == older.pk
    assert b"will not be processed until" in response.content
    assert blocker_url.encode() in response.content
    assert client.get(blocker_url).context["blocker"] is None
    msg.finish(claimed, "simulated") if unresolved == "sending" else msg.change_message(
        booking_user, enabled.school.slug, older.public_id, "suppress")
    assert b"Waiting for" not in client.get(route(enabled, "messages")).content
    assert client.get(route(enabled, "message_detail", public_id=newer.public_id)).context["blocker"] is None


def test_blocker_display_never_crosses_workspaces(enabled, booking_member, booking_user, client):
    other = SchedulingProfile.objects.create(
        school=School.objects.create(slug="other-message-workspace"), enabled=True, message_preview_enabled=True)
    foreign = capture(other)
    msg.process_batch(other.pk, outcome="uncertain")
    local = capture(enabled)
    assert local.group_key == foreign.group_key
    client.force_login(booking_user)
    response = client.get(route(enabled, "messages"))
    assert b"Waiting for" not in response.content
    assert str(foreign.public_id).encode() not in response.content
    assert client.get(route(enabled, "message_detail", public_id=local.public_id)).context["blocker"] is None


def test_csrf_and_guards(enabled,booking_member,booking_user,settings):
    client=Client(enforce_csrf_checks=True);client.force_login(booking_user)
    item=capture(enabled)
    url=route(enabled,"message_detail",public_id=item.public_id)
    assert client.post(url,{"confirm":"yes","action":"suppress"}).status_code==403
    settings.BOOKINGS_MESSAGE_PREVIEW=False
    assert client.get(url).status_code==404
    assert client.get(route(enabled,"messages")).status_code==404


def test_pagination_is_scoped(enabled,booking_member,booking_user,client):
    for n in range(26): capture(enabled,event_key=f"event:{n}",group_key=f"group:{n}")
    client.force_login(booking_user)
    result=client.get(route(enabled,"messages"),{"page":2})
    assert len(result.context["queued_messages"])==1 and b"Previous" in result.content
    assert b"Next" in client.get(route(enabled,"messages")).content


def test_legacy_notice_explicit_queue_latest_only(music,offer,settings,client):
    settings.BOOKINGS_MESSAGE_PREVIEW=True
    m=music
    item,_,_=booked(m,offer)
    notice=item.notices.get()
    # Simulate a notice saved before the additive outbox migration.
    old_delivery=notice.delivery
    notice.delivery=None;notice.save()
    old_delivery.delete()
    client.force_login(m["user"])
    url=route(m["profile"],"lesson_notice_queue",pk=notice.pk)
    assert client.get(url).status_code==405
    assert client.post(url,{"confirm":"yes"}).status_code==302
    notice.refresh_from_db();assert notice.delivery_id is None
    m["profile"].message_preview_enabled=True;m["profile"].save()
    assert client.post(url,{"confirm":"yes"}).status_code==302
    notice.refresh_from_db();assert notice.delivery.state=="queued"
    assert client.post(url,{"confirm":"yes"}).status_code==302
    assert BookingMessage.objects.count()==1
    ap.save_notice(item,"later","Lesson changed")
    client.post(url,{"confirm":"yes"})
    notice.refresh_from_db();assert notice.delivery.state=="superseded"
    detail=client.get(route(m["profile"],"lesson_booking_detail",public_id=item.public_id))
    assert b"Delivery history" in detail.content
    settings.BOOKINGS_MESSAGE_PREVIEW=False
    assert client.post(url,{"confirm":"yes"}).status_code==404
