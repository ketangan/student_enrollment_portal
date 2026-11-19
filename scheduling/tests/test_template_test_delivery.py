from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.urls import reverse

from core.models import School, SchoolEmailTemplate
from scheduling import booking_email_templates as emails, messaging
from scheduling.tests.test_inventory import stock
from scheduling.tests.test_guest_lists import booking
from scheduling.tests.test_booking_email_templates import activate

pytestmark = pytest.mark.django_db


def draft(stock, template):
    return dict(action='send_test', name=template.name, subject='Hello {{customer_name}}',
        body='<p><strong>Guests:</strong> {{participant_count}}</p>',
        booking_kind=template.booking_kind, booking_links=['payment', 'guests', 'guide'],
        test_recipient='review@example.invalid', is_active='on')


@pytest.mark.parametrize('branded', [True, False])
def test_actual_automatic_email_preserves_wording_and_brand_mode(stock, booking, mailoutbox, branded):
    activate(stock)
    template = SchoolEmailTemplate.objects.get(school=stock.school, booking_event='party_confirmation')
    template.body = '<p><strong>Custom guests:</strong> {{participant_count}}</p>'
    template.booking_details_in_body = True
    template.save()
    stock.profile.branded_booking_email = branded
    stock.profile.email_brand_color = '#ff8a92'
    stock.profile.save()
    result = emails.payload(booking, 'party_confirmation', 'Old subject', 'Old body')
    message = messaging.capture(stock.profile, event_key='delivery-check', group_key='delivery-check',
        recipient='review@example.invalid', **result)
    email = emails.make_email(message, EmailMessage, to=[message.recipient])
    assert email.send() == 1
    html = mailoutbox[0].alternatives[0].content
    assert '<strong>Custom guests:</strong> 2' in html
    assert ('#ff8a92' in html) == branded
    assert 'href=' in html  # Real booking delivery retains its actual links.
    assert 'Custom guests:' in mailoutbox[0].body


@pytest.mark.parametrize('branded', [True, False])
def test_sample_delivery_no_save_no_real_links(stock, mailoutbox, branded):
    cache.clear()
    activate(stock)
    stock.profile.branded_booking_email = branded
    stock.profile.save()
    template = SchoolEmailTemplate.objects.get(school=stock.school, booking_event='party_confirmation')
    template.booking_links = ['payment', 'guests', 'guide']
    template.save()
    original = template.body
    path = reverse('scheduling:email_template_edit', args=[stock.school.slug, template.pk])
    values = draft(stock, template)
    response = stock.client.post(path, values)
    assert response.status_code == 200, response.content
    assert 'captured locally' in response.json()['message']
    email = mailoutbox[0]
    assert email.to == ['review@example.invalid']
    assert email.subject == '[Test] Hello Maya Chen'
    html = email.alternatives[0].content
    assert '<strong>Guests:</strong> 3' in html
    assert 'href=' not in html and '/pay/' not in html
    assert all(label in html for label in ['Make payment', 'Add guest details', 'Design guide (PDF)'])
    template.refresh_from_db()
    assert template.body == original
    assert not messaging.BookingMessage.objects.exists()
    assert stock.client.post(path, values).status_code == 429
    assert len(mailoutbox) == 1


def test_test_email_validation_and_tenant_isolation(stock, mailoutbox):
    cache.clear()
    activate(stock)
    template = SchoolEmailTemplate.objects.get(school=stock.school, booking_event='party_confirmation')
    path = reverse('scheduling:email_template_edit', args=[stock.school.slug, template.pk])
    values = draft(stock, template)
    assert stock.client.post(path, {**values, 'test_recipient':'bad'}).status_code == 400
    response = stock.client.post(path, {**values, 'subject':''})
    assert response.status_code == 400 and 'subject' in response.json()['errors']
    other = SchoolEmailTemplate.objects.create(school=School.objects.create(slug='other-test-mail'),
        name='Other', purpose='bookings', body='Private', subject='Private')
    assert stock.client.post(reverse('scheduling:email_template_edit', args=[stock.school.slug, other.pk]), values).status_code == 404
    stock.client.logout()
    assert stock.client.post(path, values).status_code == 302
    assert not mailoutbox


def test_test_email_transport_failure_is_not_success_or_retried(stock, mailoutbox):
    cache.clear()
    activate(stock)
    template = SchoolEmailTemplate.objects.get(school=stock.school, booking_event='party_confirmation')
    path = reverse('scheduling:email_template_edit', args=[stock.school.slug, template.pk])
    with patch('scheduling.email_settings.get_school_email_connection', side_effect=RuntimeError('secret')) as transport:
        response = stock.client.post(path, draft(stock, template))
    assert response.status_code == 502 and b'secret' not in response.content
    assert transport.call_count == 1 and not mailoutbox


def test_brand_mode_persists_across_tabs_without_changing_other_brand_fields(stock):
    activate(stock)
    path = reverse('scheduling:email_settings', args=[stock.school.slug])
    template = SchoolEmailTemplate.objects.get(school=stock.school, booking_event='party_confirmation')
    original_color = stock.profile.email_brand_color
    for enabled in (False, True):
        response = stock.client.post(path, dict(action='save_brand_mode',
            branded_booking_email=str(enabled).lower(), email_brand_color='#123456'))
        assert response.status_code == 200
        stock.profile.refresh_from_db()
        assert stock.profile.branded_booking_email is enabled
        assert stock.profile.email_brand_color == original_color
        for tab in ('templates', 'branding', 'delivery'):
            tab_page = stock.client.get(path, {'tab': tab})
            assert b'data-brand-mode-form' in tab_page.content
        page = stock.client.get(path, {'tab':'templates', 'template':template.pk})
        assert page.context['template_workspace']
        assert b'Insert field' in page.content
        assert b'Buttons &amp; links' in page.content
        assert b'email-preview-test' in page.content
        assert b'All booking emails' in page.content
        groups = page.context['template_token_groups']
        assert {key for _, fields in groups for key, _ in fields} == set(dict(page.context['template_tokens']))
        assert (original_color in page.context['template_preview']['html']) is enabled
        preview = stock.client.post(reverse('scheduling:email_template_edit', args=[stock.school.slug, template.pk]),
            {**draft(stock, template), 'action':'preview'})
        assert preview.status_code == 200
        assert (original_color in preview.json()['html']) is enabled
    assert stock.client.post(path, {'action':'save_brand_mode'}).status_code == 400
