from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import School
from scheduling.models import Reservation, Location, Offering, SchedulingProfile
from scheduling.tests.test_reservations import inventory, reserve, workshop_request, path, settle

pytestmark = pytest.mark.django_db


def test_quick_filters_payment_display_and_positive_completion_switch(inventory, booking_member, client):
    item = reserve(booking_member, workshop_request(inventory))
    client.force_login(booking_member.user)
    url = path(booking_member, "reservations")
    page = client.get(url)
    assert b'Payment</th>' in page.content and b'$100.00 due' in page.content
    assert b'Workshop</span>' in page.content
    assert b'data-lucide="ticket"' in page.content
    assert b'dash-initials' not in page.content
    assert page.context['balance_count'] == 1
    assert not page.context['filter_form']['show_completed'].value()
    settle(booking_member, item)
    page = client.get(url, {'balance': '1'})
    assert b'$75.00 due' in page.content and b'of $100.00' in page.content
    assert page.context['preparation_count'] == 1
    page = client.get(url, {'preparation': '1', 'prep': 'pending', 'show_completed': 'false'})
    assert page.context['preparation_only'] and page.context['items'].paginator.count == 1
    settle(booking_member, item, 7500)
    assert not client.get(url, {'balance': '1'}).context['items']
    assert b'payment-paid">Paid' in client.get(url).content
    Reservation.objects.filter(pk=item.pk).update(state='completed')
    assert not client.get(url).context['items']
    page = client.get(url, {'show_completed': 'on'})
    assert page.context['items'].paginator.count == 1
    assert page.context['filter_form']['show_completed'].value()
    assert 'show_completed=on' in page.context['balance_url']
    assert not client.get(url, {'show_completed': 'false', 'hide_completed': '0'}).context['items']


def test_balance_filter_uses_net_payments_and_excludes_cancellations(inventory, booking_member, client):
    item = reserve(booking_member, workshop_request(inventory))
    settle(booking_member, item, 10000)
    client.force_login(booking_member.user)
    url = path(booking_member, 'reservations')
    Reservation.objects.filter(pk=item.pk).update(state='canceled')
    settle(booking_member, item, 2000, kind='refund')
    page = client.get(url)
    assert page.context['items'][0].net_received == 8000
    assert b'$20.00 due' not in page.content
    page = client.get(url, {'balance': '1'})
    assert page.context['balance_count'] == 0 and not page.context['items']


def test_completed_hidden_by_default_and_explicitly_recoverable(inventory, booking_member, client):
    item = reserve(booking_member, workshop_request(inventory))
    client.force_login(booking_member.user)
    url = path(booking_member, "reservations")
    assert client.get(url).context['items'].paginator.count == 1
    Reservation.objects.filter(pk=item.pk).update(starts_at=timezone.now() - timedelta(hours=2), ends_at=timezone.now() - timedelta(seconds=1))
    response = client.get(url)
    assert response.context['hide_completed']
    assert response.context['total_count'] == response.context['items'].paginator.count == 0
    response = client.get(url, {'hide_completed': '0'})
    assert response.context['items'].paginator.count == 1
    assert 'hide_completed=0' in response.context['sort_urls']['name']
    assert 'hide_completed=0' in response.context['attention_url']
    Reservation.objects.filter(pk=item.pk).update(state='completed', ends_at=timezone.now()+timedelta(days=1))
    assert not client.get(url).context['items']
    assert client.get(url, {'hide_completed': '0'}).context['items'].paginator.count == 1
    from scheduling.workspace import overview
    card = next(card for card in overview(inventory[0])['dashboard_cards'] if card['label'] == 'Reservations')
    assert client.get(card['url']).context['items'].paginator.count == card['count'] == 1


def test_header_filters_and_prep_badges_agree(inventory, booking_member, client):
    item = reserve(booking_member, workshop_request(inventory))
    client.force_login(booking_member.user)
    url = path(booking_member, "reservations")
    assert client.get(url, {'offering': inventory[1].pk, 'prep': 'hold'}).context['items'].paginator.count == 1
    assert not client.get(url, {'prep': 'pending'}).context['items']
    settle(booking_member, item)
    assert client.get(url, {'prep': 'pending'}).context['items'].paginator.count == 1
    Reservation.objects.filter(pk=item.pk).update(prepared_at=timezone.now())
    assert not client.get(url, {'prep': 'pending'}).context['items']
    assert client.get(url, {'prep': 'complete'}).context['items'].paginator.count == 1
    assert client.get(url, {'preparation': '1', 'prep': 'complete'}).context['items'].paginator.count == 1
    Reservation.objects.filter(pk=item.pk).update(state='canceled')
    assert not client.get(url, {'prep': 'complete'}).context['items']
    assert client.get(url, {'prep': 'not_required'}).context['items'].paginator.count == 1
    for query in ({'prep': 'forged'}, {'offering': '999999'}):
        assert not client.get(url, query).context['items']
    content = client.get(url).content
    assert b'New party request' not in content
    assert b'Status &amp; date filters' not in content
    for name in ('offering', 'location', 'state', 'prep', 'date_from', 'date_to'):
        assert f'name="{name}"'.encode() in content


def test_offering_choices_cannot_leak_other_business(inventory, booking_member, client):
    other = SchedulingProfile.objects.create(school=School.objects.create(slug='other-filter-school'))
    place = Location.objects.create(profile=other, name='Private location')
    offering = Offering.objects.create(location=place, name='Private workshop')
    client.force_login(booking_member.user)
    response = client.get(path(booking_member, 'reservations'), {'offering': offering.pk})
    assert not response.context['items']
    assert b'Private workshop' not in response.content
