import pytest
from django.core.mail import EmailMessage
from django.urls import reverse

from core.models import School, SchoolEmailTemplate
from scheduling import booking_email_templates as emails, messaging
from scheduling.email_settings import BookingBrandForm, BookingTemplateForm
from scheduling.message_models import BookingMessage
from scheduling.models import SchedulingProfile
from scheduling.tests.test_inventory import stock
from scheduling.tests.test_guest_lists import booking

pytestmark = pytest.mark.django_db


def activate(stock):
    stock.profile.branded_booking_email = True
    stock.profile.email_brand_name = 'Test Studio'
    stock.profile.save()
    emails.seed_defaults(stock.profile)


def test_preview_links_are_inert_but_sent_links_remain_live(stock, booking):
    activate(stock)
    template = emails.default_template(stock.school, 'party_confirmation')
    template.body = '<p><a href="https://example.com">Website</a></p>'
    assert 'href=' not in emails.preview(stock.profile, template)['html']
    data = emails.payload(booking, 'party_confirmation', '', '')
    assert 'href=' in emails.presentation(data['body'], data['email_layout'])[1]
    branding = emails.brand_preview(stock.profile)['html']
    assert 'Add guest details' not in branding and 'Design guide' not in branding


@pytest.mark.parametrize('guests,guide', [(True, False), (False, True), (False, False), (True, True)])
def test_button_choices_match_preview_and_delivery(stock, booking, settings, tmp_path, guests, guide):
    from scheduling.tests.test_guides import upload
    settings.MEDIA_ROOT = tmp_path
    settings.BOOKINGS_GUIDE_STORAGE_READY = True
    upload(stock)
    activate(stock)
    template = SchoolEmailTemplate.objects.get(school=stock.school, booking_event='party_confirmation')
    template.booking_action_enabled = guests
    template.booking_guide_enabled = guide
    template.save()
    preview = emails.preview(stock.profile, template)['html']
    data = emails.payload(booking, 'party_confirmation', '', '')
    actions = data['email_layout']['actions']
    assert any(a['kind'] == 'guests' for a in actions) == guests
    assert any(a['kind'] == 'guide' for a in actions) == guide
    assert ('Add guest details' in preview) == guests
    assert ('Design guide (PDF)' in preview) == guide


def test_booking_editor_aliases_and_saved_button_choices(stock):
    activate(stock)
    template = SchoolEmailTemplate.objects.get(school=stock.school, booking_event='party_confirmation')
    path = reverse('scheduling:email_template_edit', kwargs={'school_slug': stock.school.slug, 'pk': template.pk})
    response = stock.client.get(path)
    assert b'data-token="customer_name"' in response.content
    from html import unescape
    assert '{{customer_name}}' in unescape(response.content.decode())
    data = {'action':'save', 'name':template.name, 'subject':'{{business_name}} for {{customer_name}}',
        'body':'Hello {{customer_name}}', 'booking_links':['guide']}
    assert stock.client.post(path, data).status_code == 302
    template.refresh_from_db()
    assert template.booking_links == ['guide']
    assert 'Test Studio for Maya Chen' == emails.preview(stock.profile, template)['subject']


def test_branding_is_opt_in_and_seed_preserves_edits_and_enrollment(stock, booking):
    original = emails.payload(booking, 'party_confirmation', 'Original', 'Original plain body')
    assert original == {'subject': 'Original', 'body': 'Original plain body'}
    enrollment = SchoolEmailTemplate.objects.create(school=stock.school, name='Enrollment', subject='Original', body='Original')
    activate(stock)
    template = SchoolEmailTemplate.objects.get(school=stock.school, booking_event='party_confirmation')
    template.body = '<p>School wording</p>'
    template.save()
    emails.seed_defaults(stock.profile)
    template.refresh_from_db(); enrollment.refresh_from_db()
    assert template.body == '<p>School wording</p>' and enrollment.body == 'Original'
    assert SchoolEmailTemplate.objects.filter(school=stock.school, purpose='bookings').count() == 6
    other = SchedulingProfile.objects.create(school=School.objects.create(slug='untouched'))
    assert not other.branded_booking_email
    assert not SchoolEmailTemplate.objects.filter(school=other.school).exists()


@pytest.mark.parametrize('event', list(emails.DEFAULTS))
def test_all_events_preserve_facts_and_render_multipart(stock, booking, event):
    activate(stock)
    data = emails.payload(booking, event, 'Legacy', 'Legacy',
        before={'start': booking.starts_at.isoformat()}, refund_requested=True)
    message = messaging.capture(stock.profile, event_key=event, group_key=event,
        recipient='test@example.invalid', **data)
    message.refresh_from_db()
    email = emails.make_email(message, EmailMessage, to=[message.recipient])
    assert email.content_subtype == 'plain'
    assert email.alternatives[0].mimetype == 'text/html'
    assert 'Test Studio' in email.body and 'Participants: 2' in email.body
    assert '<table' in email.alternatives[0].content
    if event == 'payment_request':
        assert 'Balance due: USD 100.00' in email.body
        assert 'Make payment' in email.body
    if event == 'canceled':
        assert 'A refund has been requested' in email.body
    if event == 'rescheduled':
        assert 'Previous time:' in email.body and 'payments have not changed' in email.body
    if event == 'party_confirmation':
        assert '/guests/' in email.body
    # Future template/branding edits cannot rewrite an already queued email.
    stock.profile.email_brand_name = 'Changed later'
    stock.profile.save()
    SchoolEmailTemplate.objects.filter(school=stock.school).update(body='Changed later')
    message.refresh_from_db()
    assert emails.make_email(message, EmailMessage, to=[message.recipient]).body == email.body


def test_inactive_automatic_template_falls_back_and_manual_list_is_unchanged(stock, booking):
    from scheduling.reservation_email import templates_for
    activate(stock)
    SchoolEmailTemplate.objects.filter(school=stock.school, booking_event='party_confirmation').update(is_active=False, body='Hidden')
    manual = SchoolEmailTemplate.objects.create(school=stock.school, purpose='bookings', name='Personal', subject='Hi', body='Hello')
    assert 'Hidden' not in emails.payload(booking, 'party_confirmation', '', '')['body']
    assert list(templates_for(booking)) == [manual]


def test_sanitized_body_and_escaped_tags_and_layout(stock, booking):
    activate(stock)
    template = SchoolEmailTemplate.objects.get(school=stock.school, booking_event='party_confirmation')
    template.body = '<p>Hello {{full_name}}</p><script>bad()</script>'
    data = emails.render_payload(stock.profile, template, {'full_name': '<img src=x onerror=bad()>'},
        {'details': [('Customer', '<script>bad()</script>')], 'actions': [{'label': 'Bad', 'url': 'javascript:bad()'}]})
    text, html = emails.presentation(data['body'], data['email_layout'])
    assert '<script>' not in html and '<img' not in html and 'javascript:' not in html
    assert '&lt;img' in html
    assert '<img' in text


def test_brand_save_seeds_and_preview_does_not_write_or_send(stock, mailoutbox):
    path = reverse('scheduling:email_settings', kwargs={'school_slug': stock.school.slug})
    data = {'action': 'preview_brand', 'branded_booking_email': 'on', 'email_brand_name': 'Preview only',
        'email_brand_color': '#2563eb', 'email_signoff': 'See you soon'}
    response = stock.client.post(path, data)
    assert response.status_code == 200 and 'Preview only' in response.json()['html']
    stock.profile.refresh_from_db()
    assert not stock.profile.branded_booking_email and not SchoolEmailTemplate.objects.exists()
    data['action'] = 'save_brand'
    assert stock.client.post(path, data).status_code == 302
    stock.profile.refresh_from_db()
    assert stock.profile.branded_booking_email and SchoolEmailTemplate.objects.count() == 6
    assert not mailoutbox and not BookingMessage.objects.exists()


@pytest.mark.parametrize('branded', [True, False])
@pytest.mark.parametrize('color', ['#ff8a92', '#2563eb'])
def test_brand_fields_preview_save_and_reload(stock, mailoutbox, branded, color):
    path = reverse('scheduling:email_settings', kwargs={'school_slug': stock.school.slug})
    original = (stock.profile.email_brand_name, stock.profile.email_brand_color, stock.profile.email_signoff)
    data = {'action': 'preview_brand', 'email_brand_name': 'Studio & friends',
        'email_brand_color': color, 'email_signoff': 'See you soon!\nThe team'}
    if branded:
        data['branded_booking_email'] = 'on'
    response = stock.client.post(path, data)
    assert response.status_code == 200
    html = response.json()['html']
    assert ('background:' + color in html) == branded
    assert ('Studio &amp; friends' in html) == branded
    assert 'See you soon!<br>The team' in html
    stock.profile.refresh_from_db()
    assert (stock.profile.email_brand_name, stock.profile.email_brand_color, stock.profile.email_signoff) == original
    data['action'] = 'save_brand'
    assert stock.client.post(path, data).status_code == 302
    stock.profile.refresh_from_db()
    assert stock.profile.branded_booking_email == branded
    assert stock.profile.email_brand_color == color
    assert stock.profile.email_brand_name == 'Studio & friends'
    assert stock.profile.email_signoff == data['email_signoff']
    response = stock.client.get(path + '?tab=branding')
    assert response.context['brand_preview']['html'] == html
    assert not mailoutbox and not BookingMessage.objects.exists()


def test_template_preview_retains_tokens_and_binding_without_saving(stock, mailoutbox):
    activate(stock)
    template = SchoolEmailTemplate.objects.get(school=stock.school, booking_event='party_confirmation')
    path = reverse('scheduling:email_template_edit', kwargs={'school_slug': stock.school.slug, 'pk': template.pk})
    response = stock.client.get(path)
    assert b'data-token="first_name"' in response.content
    assert b'data-preview-frame' in response.content
    from html.parser import HTMLParser
    class Frames(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == 'iframe':
                self.frame = dict(attrs)
    parser = Frames()
    parser.feed(response.content.decode())
    frame = parser.frame
    assert '<html lang="en">' in frame['srcdoc']
    assert '<table' in frame['srcdoc'] and '</html>' in frame['srcdoc']
    assert frame.get('sandbox') == ''
    data = {'action': 'preview', 'name': template.name, 'subject': 'Hello {{first_name}}', 'heading': 'Welcome {{first_name}}',
        'body': '<p><strong>Thank you</strong> {{full_name}}</p>', 'booking_kind': 'all', 'booking_link': 'payment', 'is_active': 'on'}
    response = stock.client.post(path, data)
    assert response.status_code == 200
    assert 'Maya Chen' in response.json()['html'] and '<strong>Thank you</strong>' in response.json()['html']
    template.refresh_from_db()
    assert template.subject != data['subject'] and template.booking_link == 'guests'
    data['action'] = 'save'
    assert stock.client.post(path, data).status_code == 302
    template.refresh_from_db()
    assert template.subject == data['subject'] and template.booking_link == 'guests'
    assert not mailoutbox


@pytest.mark.parametrize('color', ['#GGGGGG', 'red'])
def test_invalid_brand_color_rejected(stock, color):
    form = BookingBrandForm({'email_brand_color': color}, instance=stock.profile)
    assert not form.is_valid() and 'email_brand_color' in form.errors


def test_pink_brand_uses_readable_text_and_no_demo_copy(stock):
    stock.school.is_demo = True
    stock.school.save()
    stock.profile.email_brand_color = '#ff8a92'
    form = BookingBrandForm({'email_brand_color': '#ff8a92', 'branded_booking_email': 'on'}, instance=stock.profile)
    assert form.is_valid()
    result = emails.preview(stock.profile, emails.default_template(stock.school, 'party_confirmation'))
    assert '[Demo]' not in result['subject']
    assert 'test booking' not in result['html'] and 'Sample' not in result['html']
    assert 'color:#111111' in result['html']


def test_templates_edit_and_preview_stay_on_settings_tab(stock):
    activate(stock)
    archived = SchoolEmailTemplate.objects.create(school=stock.school, purpose='bookings',
        name='Unused sample', subject='Old', body='Old', is_active=False)
    path = reverse('scheduling:email_settings', kwargs={'school_slug': stock.school.slug})
    response = stock.client.get(path)
    assert response.status_code == 200
    assert b'data-email-form' in response.content and b'data-preview-frame' in response.content
    assert b'data-token="first_name"' in response.content
    assert b'Unused sample' not in response.content
    assert len(response.context['email_templates']) == 6
    template = response.context['selected_template']
    assert f'template={template.pk}'.encode() in response.content
    other = SchoolEmailTemplate.objects.create(school=School.objects.create(slug='outside-email'),
        purpose='bookings', name='Foreign', subject='Foreign', body='Foreign')
    assert stock.client.get(path, {'template': other.pk}).status_code == 404
    assert stock.client.get(path, {'template': 'invalid'}).status_code == 404
    archived.refresh_from_db()
    assert not archived.is_active


def test_automatic_editor_hides_fixed_controls(stock):
    activate(stock)
    path = reverse('scheduling:email_settings', kwargs={'school_slug': stock.school.slug})
    response = stock.client.get(path)
    assert b'Available for:' not in response.content
    assert b'Use custom wording' not in response.content
    assert b'Insert field' in response.content
    assert b'Auto-filled Tokens' not in response.content
    assert b'data-token="first_name"' in response.content


def test_new_template_preview_accepts_incomplete_draft_but_save_requires_fields(stock):
    activate(stock)
    path = reverse('scheduling:email_template_new', kwargs={'school_slug': stock.school.slug})
    count = SchoolEmailTemplate.objects.count()
    data = {'action': 'preview', 'booking_kind': 'both', 'booking_link': 'none'}
    response = stock.client.post(path, data)
    assert response.status_code == 200, response.content
    assert 'errors' not in response.json()
    data['action'] = 'save'
    response = stock.client.post(path, data)
    assert response.status_code == 400
    assert {'name', 'subject', 'body'} <= response.context['template_form'].errors.keys()
    assert SchoolEmailTemplate.objects.count() == count


def test_design_guide_button_and_automatic_preview(stock, booking, settings, tmp_path):
    from scheduling.reservation_email import customer_link
    from scheduling.tests.test_guides import upload
    settings.MEDIA_ROOT = tmp_path
    settings.BOOKINGS_GUIDE_STORAGE_READY = True
    activate(stock)
    assert customer_link(booking, 'guide') is None
    upload(stock)
    link = customer_link(booking, 'guide')
    assert '/design-guide/' in link['url']
    result = emails.preview(stock.profile, emails.default_template(stock.school, 'party_confirmation'))
    assert 'Design guide (PDF)' in result['html']
    form = BookingTemplateForm({'name': 'Guide', 'subject': 'Designs', 'body': 'Choose your design',
        'booking_kind': 'both', 'booking_link': 'guide'}, branded=True)
    assert form.is_valid(), form.errors


def test_manual_guide_uses_existing_signed_send_and_delivery_checks(stock, booking, settings, tmp_path):
    from scheduling import party_notifications, reservation_email
    from scheduling.tests.test_guides import upload
    from scheduling.tests.test_reservation_email import data, url, template
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path
    settings.BOOKINGS_GUIDE_STORAGE_READY = True
    upload(stock)
    activate(stock)
    item = template(stock, booking_link='guide')
    stock.profile.party_notifications_enabled = True
    stock.profile.save(update_fields=['party_notifications_enabled'])
    option = reservation_email.context_for(stock.user, booking)['email_options'][str(item.pk)]
    payload = data(stock, booking, template=str(item.pk), link_token=option['link_token'])
    assert stock.client.post(url(stock, booking), payload).status_code == 302
    message = BookingMessage.objects.get(notice_kind='manual_links')
    assert '/design-guide/' in message.email_layout['actions'][0]['url']
    assert party_notifications.deliverable(message)
    assert stock.client.post(url(stock, booking), payload).status_code == 302
    assert BookingMessage.objects.count() == 1


def test_unknown_tokens_and_multiline_subject_rejected(stock):
    form = BookingTemplateForm({'name': 'Bad', 'booking_kind': 'all', 'booking_link': 'none',
        'subject': 'Hello\nBcc: nobody', 'body': '{{unknown}}', 'heading': '{{unknown}}'}, branded=True)
    assert not form.is_valid()
    assert {'subject', 'body', 'heading'} <= form.errors.keys()


@pytest.mark.parametrize('live', [False, True])
def test_branded_party_worker_keeps_attachment_and_idempotency(stock, booking, settings, tmp_path, monkeypatch, mailoutbox, live):
    from scheduling import party_notifications
    from scheduling.tests.test_guides import upload
    settings.MEDIA_ROOT = tmp_path
    settings.BOOKINGS_GUIDE_STORAGE_READY = True
    upload(stock)
    activate(stock)
    stock.profile.party_notifications_enabled = stock.profile.message_preview_enabled = True
    stock.profile.save()
    settings.DEBUG = True
    settings.BOOKINGS_MESSAGE_PREVIEW = True
    settings.EMAIL_BACKEND = messaging.LOCAL_BACKEND
    if live:
        booking.order.environment = 'live'
        booking.order.save()
        settings.DEBUG = False
        settings.BOOKINGS_PARTY_EMAIL_ENABLED = True
        class Transport:
            def send_messages(self, messages):
                mailoutbox.extend(messages)
                return 1
        monkeypatch.setattr(party_notifications, 'get_school_email_connection', lambda *args: Transport())
    party_notifications.capture_for_reservation(booking.pk)
    message = BookingMessage.objects.get()
    SchoolEmailTemplate.objects.filter(school=stock.school).update(body='Later changes')
    party_notifications.capture_for_reservation(booking.pk)
    process = party_notifications.process if live else messaging.process_batch
    assert process(stock.profile.pk) == 1
    assert process(stock.profile.pk) == 0
    assert len(mailoutbox) == 1 and mailoutbox[0].attachments
    assert mailoutbox[0].alternatives and '/guests/' in mailoutbox[0].body
    assert 'Later changes' not in mailoutbox[0].body
    message.refresh_from_db()
    assert message.state == ('sent' if live else 'simulated')


def test_branded_workshop_capture_and_local_delivery(stock, settings, mailoutbox):
    from scheduling import party_notifications
    from scheduling.tests.test_workshop_selections import pending_booking
    activate(stock)
    stock.profile.workshop_notifications_enabled = stock.profile.message_preview_enabled = True
    stock.profile.save()
    settings.DEBUG = settings.BOOKINGS_MESSAGE_PREVIEW = True
    settings.EMAIL_BACKEND = messaging.LOCAL_BACKEND
    item, _ = pending_booking(stock)
    party_notifications.capture_for_reservation(item.pk)
    assert messaging.process_batch(stock.profile.pk) == 1
    assert len(mailoutbox) == 1 and mailoutbox[0].alternatives
    assert '/workshop-selections/' in mailoutbox[0].body


def test_manual_template_heading_and_retry_keep_original_brand(stock, booking, settings):
    from scheduling.tests.test_reservation_email import data, template, url
    settings.DEBUG = True
    activate(stock)
    item = template(stock, heading='Hello {{first_name}}')
    payload = data(stock, booking, template=str(item.pk))
    assert stock.client.post(url(stock, booking), payload).status_code == 302
    message = BookingMessage.objects.get(notice_kind='manual')
    assert message.email_layout['heading'].startswith('Hello ')
    assert '{{' not in message.email_layout['heading']
    original = message.email_layout
    stock.profile.email_brand_name = 'Changed later'
    stock.profile.save()
    assert stock.client.post(url(stock, booking), payload).status_code == 302
    message.refresh_from_db()
    assert message.email_layout == original
    assert BookingMessage.objects.count() == 1
