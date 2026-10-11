# Kid Works Go-Live Checklist

Updated: 2026-10-08
School: `kidworks-childrens-center`
Status: Signed; onboarding information pending. No launch approval.

This is the working reference for Kid Works go-live changes. Update the checklist
and evidence log as work progresses. Do not mark configuration, implementation,
testing, or customer acceptance complete merely because the demo exists.

## Source of Truth

- Dinuka's October 2 meeting transcript, shared in the **Recommend plan pricing**
  chat (`01a0edfe-c3ec-7101-b917-5bc3e0716ad7`), is the operational reference.
  Local source: `/Users/njulka/.codex/attachments/8b9783e1-4911-49d6-be11-37024fcab1dc/Pasted text.txt`.
- [October 2 meeting recap](https://mail.google.com/mail/u/?authuser=ketan%40mypontora.com#all/1a0ff541e0975acf).
- [Signed agreement and completion notice, October 8](https://mail.google.com/mail/u/?authuser=ketan%40mypontora.com#all/1a11c8903b2232a3).
- Subsequent explicit customer decisions supersede earlier operational assumptions;
  record them here with a date/source. Contract changes require explicit agreement.
- The school website is known to be inaccurate. Do not derive workflow requirements
  from it or ask Dinuka to reconfirm decisions already settled in the meeting.
- [Original demo notes](kidworks-tour-demo.md) describe historical implementation,
  not the approved live workflow. Demo dates, capacities, fees and wording are not
  approved business rules.

## Established Requirements

- Tours precede applications and enrollment. Playdates happen **after enrollment**
  to support the child's transition, not as an admissions assessment.
- One new child, accompanied by an adult, per playdate slot. Once booked, the slot
  must be unavailable to another new child. Do not count the accompanying adult as
  a second new-child booking or infer advanced room/resource rules.
- Predefined tour availability plus an option to request another time. A request
  is not a confirmed appointment.
- Capture intended intake/start timing to distinguish relevant enquiries from
  distant-future enquiries. Keep future enquiries for follow-up. The meeting did
  not establish an automatic rejection rule or exact qualification cutoff.
- Preserve personal communication and a place for parents' questions/comments.
- Dinuka is currently the sole administrator. Do not request a staff roster.
- Dinuka handles Brightwheel uploads. Pontora provides compatible exports and
  agreed setup support, not automatic synchronization or replacement of Brightwheel.
- Starter: $49/month or $499/year, with the agreed simple tour/playdate scheduling
  and Brightwheel export extensions included without additional subscription fees.
- No setup fee for the agreed initial forms/workflow. The 60-day trial begins with
  written notice that the agreed workflow is live; configuration time is excluded.
- No complex staff/room/equipment allocation or unlimited custom development.

## Internal Work: Can Begin Without More Customer Decisions

Items below track implementation; completion is not deployment or launch approval.
This checklist is not authorization to
change production, send email, collect payment, or migrate existing customer data.

- [ ] **KW-01 - Correct workflow/state handling.** Separate post-enrollment playdate
  progress from enrollment status. Booking, moving, canceling, or completing a
  playdate must never demote an enrolled child. Preserve history and parent/admin
  consistency; review the existing admissions connector before choosing the change.
- [ ] **KW-02 - Real scheduling foundation.** Reuse the existing scheduling module
  behind Kid Works opt-in configuration instead of YAML-only sample appointments.
  Enforce single-new-child playdate capacity, atomic last-slot booking, idempotent
  retries, cancellation release and safe rescheduling. Add alternative-time request
  capture/review without assuming the final notification or confirmation policy.
- [ ] **KW-03 - Inquiry fields/filtering.** Support child age/date of birth and
  intended start date/intake. Preserve future enquiries and parent questions.
  Do not invent the current intake window or enable automatic rejection/release.
- [ ] **KW-04 - Reusable export hardening.** Build on the existing configurable
  export-profile service. Support documented Brightwheel fields/value formats,
  relevant-record filtering, missing-data preview/warnings, safe CSV values, and
  clear export history. Distinguish export from successful upload. Preserve known
  Brightwheel IDs for existing profiles; never invent IDs or infer import success.
- [x] **KW-05 - Package/trial controls (implemented and locally verified).**
  Prepared per-school Starter feature overrides and a 60-day, explicitly activated
  trial starting at the recorded written go-live notice. Demo access is separate;
  setup does not consume trial days. Checkout is blocked through the opted-in free
  period; no monthly/annual choice, charge or email is automatic. Existing ordinary
  trials and plan defaults stay unchanged. See [verification and rollout notes](kidworks-kw05-verification.md).
  **Not deployed or applied to Dinuka's school.** The additive migration, authorized
  contract preparation, clean launch setup, feature-specific acceptance (KW-01/02/04)
  and eventual real notice/activation remain deployment and launch gates.
- [ ] **KW-06 - Clean, secure launch setup.** Prepare production-only settings and
  secure access, domain/link checks, backups/restore, email delivery infrastructure
  and monitoring. Exclude demo records/credentials. Remove the disabled $50 fee
  placeholder from launch configuration; do not add a payment workflow by assumption.
  Preserve the separate demo and existing records; no destructive cleanup without
  a reviewed backup/migration plan and authorization.
- [ ] **KW-07 - Verification and handover.** Add meaningful unit/integration tests,
  target at least 90% coverage of new runtime code, and manually exercise desktop
  and mobile parent/admin journeys. Test permission/tenant isolation, concurrent
  bookings, rescheduling, status preservation, export validation, email links and
  Starter/trial transitions. Regress SBMC and other non-opted-in schools. Prepare
  a concise administrator walkthrough with non-sensitive sample data.

## Customer Inputs: Do Not Guess

- [ ] **Forms/programs:** Current blank inquiry/application forms and relevant
  enrollment documents; program names and attendance options; which information
  Pontora collects versus what stays in Brightwheel.
- [ ] **Tour availability:** Actual dates or recurring days/times, duration, tour
  capacity, closures, booking notice and cancellation/rescheduling rules. Times
  mentioned in the meeting were examples, not approved availability.
- [ ] **Alternative tours:** Preferred way to receive, discuss and confirm an
  alternative-time request. Keep personal communication available.
- [ ] **Playdate availability:** Actual times, duration, number of visits per child
  and notice/rescheduling rules. Single-new-child capacity is already agreed.
- [ ] **Current intake:** Which intake period is relevant for tours now. Do not
  reopen the overall qualification discussion or presume an automatic cutoff.
- [ ] **Form placement/invitations:** Embedded inquiry widget or linked branded
  page, desired website placement/contact, and whether tour invitations follow
  Dinuka's review. Confirm manual versus automatic application invitations after
  tours and playdate invitations after enrollment. A public unrestricted
  application is not the default recommendation. Embedded and linked access can
  coexist without becoming two separate forms.
- [ ] **Communications:** Existing email templates (tour replies, applications,
  confirmations/reminders), preferred personal follow-up and notification routing.
  `info@kidworkscc.com` is the verified correspondence address; the historical
  school YAML uses a different domain, so verify routing before live delivery.
- [ ] **Enrollment handoff:** What marks a child enrolled and where fees/paperwork
  are handled. Do not infer this from the demo Pay button. Confirm when records
  should be exported to Brightwheel relative to enrollment and transition visits.
- [ ] **Brightwheel configuration:** Account upload instructions/options, exact
  classroom names, wanted fields, any existing import template and whether profiles
  may already exist. Dinuka remains upload owner; no staff-roster request.
- [ ] **Launch:** Target window, whether existing enquiries/waitlist need transfer,
  customer acceptance and explicit approval to go live. Billing preference/payment
  setup is needed before paid service, not to begin configuration.

## Brightwheel Handoff Checks

Ask Dinuka to open **My School > Students > Add Students > Bulk upload** on a
computer and share the upload instructions/options with children's details hidden.
She can also share a blank template she already uses and exact classroom names.
We can obtain the official blank roster templates ourselves; do not make her
research the technical format or send a complete student database/password.

References reviewed October 8, 2026:

- [Bulk upload instructions](https://help.mybrightwheel.com/en/articles/942359-bulk-add-new-students)
- [Official roster templates](https://help.mybrightwheel.com/en/articles/11548615-download-pre-made-student-roster-templates-for-adding-students)
- [Fields and formats](https://help.mybrightwheel.com/en/articles/11548710-what-to-include-in-a-student-and-family-roster-upload)

- [ ] Verify account support for small ongoing batches. Published guidance describes
  a staff-assisted upload service and recommends manual entry for fewer than ten
  children; do not promise instant import from CSV generation alone.
- [ ] Agree supported fields/classroom mapping, date/contact/status conversion and
  existing-profile handling. The current generic CSV mapper is not a certified
  Kid Works profile; the school YAML currently has no Brightwheel export mapping.
- [ ] Review a sample export, then verify the agreed upload procedure with Dinuka
  or Brightwheel support. Do not upload fictional children to a live account or
  trigger family invitations without an explicitly approved test procedure.
- [ ] Record upload acceptance, limitations and a repeatable handover guide.

## Launch Gates

- [ ] Customer inputs are recorded and configured, with previews approved.
- [ ] Contracted workflow works under the actual Starter-plus-extensions setup.
- [ ] Full scoped regression/manual checks and critical self-review are complete;
  evidence includes commands, results, environment and unresolved limitations.
- [ ] Kid Works-specific opt-in rollout/rollback and existing-data handling are
  reviewed. Shared-code edits include impact analysis; SBMC stays unchanged.
- [ ] Authorized real email delivery and all public/private links are verified.
- [ ] Brightwheel handoff is verified, not merely described as compatible.
- [ ] Dinuka accepts the workflow; deployment is authorized; go-live notice records
  the 60-day trial start. No trial clock is inherited from the old demo.

## Demo Reference

The password-free admin demo link was sent in the
[September 3 email](https://mail.google.com/mail/u/?authuser=ketan%40mypontora.com#all/1a069a86b40e32c0).
Retrieve it from that email when needed; do not commit the access token/password
to this repository. The same email includes the non-authenticated
[form-placement preview](https://demo.mypontora.com/demo/kidworks-demo/).
The demo is for familiarization, not proof of approved go-live behavior.

## Evidence Log

| Date | Change | Verification / remaining work |
| --- | --- | --- |
| 2026-10-08 | Created this checklist from the meeting transcript, signed terms and user decisions; linked it from project and historical demo notes. | Documentation only. No implementation item completed, runtime changes, production actions or email sent. Demo link located in sent mail; live access not retested. |
| 2026-10-08 | Implemented KW-05 opt-in 60-day trial activation, Starter overrides, demo/trial separation and setup/free-trial checkout protection. | 3,547-test full regression passed; additional focused coverage and paid-Starter transition checks are recorded in the verification notes. Desktop/mobile browser checks used synthetic, isolated records. No real school conversion, data cleanup, notice, charge, deployment or trial activation. |
