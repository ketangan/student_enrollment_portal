import itertools

import pytest
from django.urls import reverse

from core.models import SchoolEmailTemplate
from scheduling import booking_email_templates as emails, reservation_email
from scheduling.message_models import BookingMessage
from scheduling.email_settings import BookingTemplateForm
from scheduling.tests.test_inventory import stock
from scheduling.tests.test_guest_lists import booking
from scheduling.tests.test_reservation_email_links import payable, paid
from scheduling.tests.test_booking_email_templates import activate
from scheduling.tests.test_guides import upload
from scheduling.tests.test_reservation_email import data, url

pytestmark = pytest.mark.django_db


def test_every_template_offers_three_links_and_rejects_unknown_links(stock):
    activate(stock)
    for template in SchoolEmailTemplate.objects.filter(school=stock.school):
        form = BookingTemplateForm(instance=template, branded=True)
        assert list(form.fields['booking_links'].choices) == list(emails.LINK_CHOICES)
    form = BookingTemplateForm(dict(name='New', subject='Hello', body='Hello',
        booking_kind='both', booking_links=['payment', 'guests', 'guide']), branded=True)
    assert form.is_valid(), form.errors
    assert form.save(commit=False).booking_links == ['payment', 'guests', 'guide']
    bad = BookingTemplateForm(dict(name='Bad', subject='Hello', body='Hello',
        booking_kind='both', booking_links=['https://untrusted.invalid']), branded=True)
    assert not bad.is_valid() and 'booking_links' in bad.errors


def test_brand_toggle_changes_preview_without_saving(stock):
    path = reverse('scheduling:email_settings', args=[stock.school.slug])
    values = dict(action='preview_brand', email_brand_name='Brand test', email_brand_color='#ff8a92')
    plain = stock.client.post(path, values).json()['html']
    branded = stock.client.post(path, {**values, 'branded_booking_email': 'on'}).json()['html']
    assert 'Brand test' not in plain and '#ff8a92' not in plain
    assert 'Brand test' in branded and '#ff8a92' in branded
    stock.profile.refresh_from_db()
    assert not stock.profile.branded_booking_email


@pytest.mark.parametrize('flags', list(itertools.product([False, True], repeat=3)))
def test_all_link_combinations_preview_and_delivery(stock, payable, settings, tmp_path, flags):
    settings.MEDIA_ROOT = tmp_path
    settings.BOOKINGS_GUIDE_STORAGE_READY = True
    upload(stock)
    activate(stock)
    template = SchoolEmailTemplate.objects.get(school=stock.school, booking_event='party_confirmation')
    template.booking_links = [key for (key, _), enabled in zip(emails.LINK_CHOICES, flags) if enabled]
    template.save()
    preview = emails.preview(stock.profile, template)['html']
    result = emails.payload(payable, 'party_confirmation', '', '')
    assert [a['kind'] for a in result['email_layout']['actions']] == template.booking_links
    for key, label in [('payment', 'Make payment'), ('guests', 'Add guest details'), ('guide', 'Design guide (PDF)')]:
        assert (label in preview) == (key in template.booking_links)
    if 'guide' in template.booking_links:
        assert 'text-decoration:underline;font-size:14px">Design guide' in preview


def test_edit_booking_details_save_preview_and_delivery(stock, booking):
    activate(stock)
    template = SchoolEmailTemplate.objects.get(school=stock.school, booking_event='party_confirmation')
    path = reverse('scheduling:email_template_edit', args=[stock.school.slug, template.pk])
    page = stock.client.get(path)
    assert '{{package_name}}' in page.context['template_form'].initial['body']
    body = '<p><strong>Celebration:</strong> {{package_name}}</p><p>Guests: {{participant_count}}</p>'
    values = dict(name=template.name, subject=template.subject, body=body, booking_links=['guests', 'guide'])
    preview = stock.client.post(path, {**values, 'action': 'preview'}).json()['html']
    assert '<strong>Celebration:</strong>' in preview and 'Guests: 3' in preview
    assert '>Starts<' not in preview
    template.refresh_from_db()
    assert not template.booking_details_in_body
    assert stock.client.post(path, values).status_code == 302
    template.refresh_from_db()
    assert template.body == body and template.booking_details_in_body
    result = emails.payload(booking, 'party_confirmation', '', '')
    assert 'Guests: 2' in result['body'] and '<strong>Celebration:</strong>' in result['body']
    assert not result['email_layout']['details']
    assert '{{' not in result['body']


def test_manual_multi_links_signed_rechecked_and_idempotent(stock, payable, settings, tmp_path):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path
    settings.BOOKINGS_GUIDE_STORAGE_READY = True
    upload(stock)
    activate(stock)
    template = SchoolEmailTemplate.objects.create(school=stock.school, purpose='bookings',
        name='All links', subject='Your booking', body='<p>Hello {{customer_name}}</p>',
        booking_links=['payment', 'guests', 'guide'])
    option = reservation_email.context_for(stock.user, payable)['email_options'][str(template.pk)]
    values = data(stock, payable, template=str(template.pk), link_token=option['link_token'])
    assert stock.client.post(url(stock, payable), values).status_code == 302
    message = BookingMessage.objects.get(notice_kind='manual_links')
    assert len(message.email_layout['actions']) == 3
    assert reservation_email.deliverable(message, payable)
    assert stock.client.post(url(stock, payable), values).status_code == 302
    assert BookingMessage.objects.count() == 1
    paid(stock, payable, 2500)
    assert not reservation_email.deliverable(message, payable)
    assert stock.client.post(url(stock, payable), values).status_code == 400
