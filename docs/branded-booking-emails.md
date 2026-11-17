# Branded Booking Emails

## School Setup

1. Open Settings > Email settings > Branding for the intended school.
2. Enable branded booking emails, set the displayed name, accent color and sign-off,
   inspect the sample preview, and save.
3. On the Templates tab, select and edit the automatic templates in place: party confirmation, party reminder,
   workshop confirmation, payment request, reservation rescheduled and cancellation.
   Existing staff-composed templates remain available.
4. Edit the subject, heading and message using the existing formatting and token
   controls. Preview uses sample data; it never sends an email.

The shared layout is saved on the school's scheduling profile. Each message's
editable content stays in SchoolEmailTemplate. Booking facts, prices and secure
links are supplied by the existing booking services, not editable copies of facts.
Automatic templates retain their event bindings. All branded templates have three
independent link checkboxes: payment, guest details, and design guide. The PDF is
an understated text link. Secure destinations are still generated per reservation;
the preview shows sample links, not working customer links. Unavailable optional
actions are omitted from automatic messages. Staff-composed templates requiring
an unavailable action cannot be sent until a suitable template is chosen.

Package/workshop, location, dates and participant count appear in the editable
message as formatted labels and tags. Existing wording is preserved; legacy details
are included in the editor until first saved. After saving, staff control those
labels, formatting, order and removal. Editing wording never changes a reservation.
Payment totals and operational change/refund notices remain system-generated.

## Delivery and Isolation

- Branding is off by default. Enrollment emails and schools that have not opted in
  keep their existing rendering. There are no customer-name or slug branches.
- The existing queue, school email connection, delivery permissions, demo safeguards,
  attachments, retries and audit trail remain in use. Branding does not enable sending.
- Queued messages snapshot wording and layout. Later template edits do not rewrite
  an already queued message. HTML emails include a plain-text alternative.
- Saving an automatic template applies its wording and button choices directly.
  Secure URLs remain system-generated. Notification switches remain
  in Guest communication.
- Inactive manual templates are omitted from the branded template list, without
  deleting their records or changing historical messages.
- Branded emails do not add demo prefixes or test-booking footers. This is only
  presentation: delivery eligibility and test-payment isolation remain enforced.
- Light accent colors use dark button text automatically. Pink and blue swatches
  are available as selections (existing custom colors remain selectable). Defaults are school records,
  not customer-specific branches in the renderer.
- Re-enabling branding creates missing defaults without overwriting existing edits.
- Custom templates can include any combination of Payment, Guest details and Design
  guide (PDF). Private links are generated per reservation; the guide
  uses the school's existing Guest communication upload. Body links still use
  the existing editor. A new automatic trigger or new data tag requires code.
- Insert details uses the shared tag insertion control with literal token labels;
  enrollment's existing tag display is unchanged. Incomplete previews are allowed,
  but saving requires a name, subject and message.
- Arbitrary full HTML documents are not supported by the body editor. It retains
  the existing HTML sanitizer; the surrounding email layout is application-managed.

## Release and Rollback

Apply core migrations through 0073 and scheduling migration 0062 before activation. Deploy
with branding off, then explicitly enable and verify the intended school's emails.
Verify a real inbox on demo before production approval, including secure links and
attachments. Do not change SMTP credentials merely to enable branding.

To roll back appearance, turn off branded booking emails for that school. Newly
captured automatic emails use the legacy renderer; already queued emails retain
their captured appearance. Do not delete templates or queued messages.
