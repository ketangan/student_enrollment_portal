Student Enrollment Portal — Master Checklists

Single source of truth for onboarding schools, running pilots, and going live.

---

## 0) Platform Readiness (one-time per environment)

Do this once per environment (local, staging, production).

- [ ] Render service deployed and reachable (public URL works)
- [ ] Django admin reachable (`/admin/`)
- [ ] Superuser login works
- [ ] Database migrations applied (`python manage.py migrate` / `showmigrations` looks sane)
- [ ] Static files load correctly (no broken CSS on apply or admin pages)
- [ ] Resend API key set (`RESEND_API_KEY`) and sender domain verified
- [ ] Sentry connected and capturing errors
- [ ] Stripe env vars configured:
  - [ ] `STRIPE_MODE` set — keep `test` until you're ready to charge schools real money for Enrollify subscriptions; flip to `live` only when enabling self-service billing (see note below)
  - [ ] `STRIPE_SECRET_KEY_TEST` and `STRIPE_SECRET_KEY_LIVE` both set
  - [ ] `STRIPE_PUBLISHABLE_KEY_TEST` and `STRIPE_PUBLISHABLE_KEY_LIVE` both set
  - [ ] `STRIPE_WEBHOOK_SECRET_TEST` and `STRIPE_WEBHOOK_SECRET_LIVE` both set
  - [ ] `STRIPE_PRICE_STARTER_MONTHLY_TEST` / `STRIPE_PRICE_STARTER_MONTHLY_LIVE`
  - [ ] `STRIPE_PRICE_STARTER_ANNUAL_TEST` / `STRIPE_PRICE_STARTER_ANNUAL_LIVE`
  - [ ] `STRIPE_PRICE_PRO_MONTHLY_TEST` / `STRIPE_PRICE_PRO_MONTHLY_LIVE`
  - [ ] `STRIPE_PRICE_PRO_ANNUAL_TEST` / `STRIPE_PRICE_PRO_ANNUAL_LIVE`
  - [ ] `STRIPE_PRICE_GROWTH_MONTHLY_TEST` / `STRIPE_PRICE_GROWTH_MONTHLY_LIVE`
  - [ ] `STRIPE_PRICE_GROWTH_ANNUAL_TEST` / `STRIPE_PRICE_GROWTH_ANNUAL_LIVE`
  - [ ] `STRIPE_PRICE_CUSTOM_MONTHLY_TEST` / `STRIPE_PRICE_CUSTOM_MONTHLY_LIVE` (for custom/founder plan schools)
  - [ ] `STRIPE_PRICE_CUSTOM_ANNUAL_TEST` / `STRIPE_PRICE_CUSTOM_ANNUAL_LIVE`
  - [ ] Stripe webhook endpoint registered: `https://<host>/stripe/webhook/`
  - [ ] Webhook subscribes to: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`

> **STRIPE_MODE note:** `STRIPE_MODE` controls Enrollify's own billing to its school customers (subscriptions). It has **no effect** on per-school application fee collection — those use each school's own Stripe keys set in their admin settings page.
> - Keep `STRIPE_MODE=test` as long as you are manually assigning plans (e.g. custom plan). No self-service billing = no reason to flip.
> - Flip to `live` only when you're ready for schools to self-subscribe and be charged real money via Stripe Checkout.
> - Before flipping: verify all `*_LIVE` price IDs exist in your Stripe Dashboard → Products, and register a live-mode webhook endpoint.

---

## A) School Intake / Pre-Pilot (per school)

Goal: confirm what the school needs and what success looks like.

**School basics**
- [ ] School name confirmed
- [ ] School slug confirmed (URL-safe, permanent — cannot change after launch)
- [ ] Website URL captured

**Current enrollment flow**
- [ ] PDF only / Email only / Phone only / Mixed

**MVP scope**
- [ ] Enrollment request
- [ ] Trial/evaluation request
- [ ] Lead capture (waitlist/interest form)
- [ ] Mixed

**Form requirements**
- [ ] Student name fields
- [ ] DOB / Age
- [ ] Program selection
- [ ] Guardian info (required / optional)
- [ ] Schedule preferences
- [ ] Notes / free text
- [ ] File upload (e.g. birth certificate, photo)
- [ ] Waiver / consent acknowledgment

**Branding**
- [ ] Default theme acceptable
- [ ] Custom colors / logo required

**Notifications**
- [ ] Submission email recipients (`to` list)
- [ ] Optional CC / BCC
- [ ] Include platform owner on BCC during pilot

**Plan**
- [ ] Trial (default — 30 days, then upgrade required)
- [ ] Starting on paid plan (Starter / Pro / Growth)

---

## B) Build & Configuration (per school)

Goal: YAML + Admin setup correct and consistent.

**YAML configuration**
- [ ] YAML file created at `configs/schools/<slug>.yaml`
- [ ] `school.slug` matches filename and URL exactly
- [ ] Form renders without errors
- [ ] Required fields enforced correctly
- [ ] Select/multiselect options have both `label` and `value`
- [ ] `success:` block present
- [ ] `notifications.submission_email.to` list non-empty
- [ ] `from_email` is a verified Resend sender
- [ ] If file upload: `type: file` field present and `file_uploads_enabled` confirmed for plan
- [ ] If waiver: `type: waiver` field present and `waiver_enabled` confirmed for plan
- [ ] If scheduling link: `scheduling.url` set in YAML
- [ ] If lead capture: `leads:` block present
- [ ] If save & resume: Pro+ plan confirmed (`save_resume_enabled`)
- [ ] If program select field uses `inject_db_program_options` (no YAML options list): `SchoolProgram` records exist in DB for this school — verify dropdown is populated on the form

**Admin setup**
- [ ] School record created via `/ops/schools/new/` (or converted from demo via `/ops/schools/<slug>/convert/`)
  - [ ] Slug matches YAML filename
  - [ ] Plan set correctly
  - [ ] `is_active = True`
- [ ] School admin user created and assigned via `/ops/schools/<slug>/` → School Admins → Add
- [ ] School admin confirmed scoped correctly (cannot see other schools)
- [ ] Trial start date confirmed in school record (`trial_started_at` set automatically on first save)

---

## C) Demo Readiness

Goal: clean 10–15 minute demo, no surprises.

**Public form**
- [ ] Apply page loads (`/schools/<slug>/apply`)
- [ ] Test submission completes (no files)
- [ ] Success page loads with correct messaging
- [ ] Scheduling link shown on success page (if configured)
- [ ] File upload works (if configured)
- [ ] Waiver checkbox renders and is enforced (if configured)
- [ ] Save & Resume flow works (if Pro+)
- [ ] Lead capture form loads (`/schools/<slug>/leads`) (if configured)

**Admin**
- [ ] Submission appears in admin
- [ ] Application number visible (sequential per school)
- [ ] Student name and program populated
- [ ] Submission editable and saves correctly
- [ ] Audit log entry created
- [ ] Submission confirmation email received (applicant) — verify body is personalized (includes student name, application ID); if generic, add `message:` key to `applicant_confirmation` in YAML
- [ ] Staff notification email received — verify subject includes student name (requires `subject:` key in YAML; default is bare "New submission" with no name)
- [ ] Reports page loads with data
- [ ] CSV export works
- [ ] Lead appears in admin leads inbox (if lead captured)
- [ ] Trial banner visible for school admin (if on trial plan)

**Billing**
- [ ] Billing page loads from admin reports hub
- [ ] Pricing options visible (if price IDs configured)
- [ ] Trial expiry date shows correctly in banner

---

## D) Go-Live Checklist (per school)

Goal: move from demo to real usage safely.

**Final verification**
- [ ] Production URL confirmed and accessible
- [ ] Admin URL confirmed
- [ ] School admin credentials tested on production
- [ ] Email recipients finalized (remove owner BCC if desired)
- [ ] Mobile rendering verified on apply form
- [ ] No obvious permission leaks (school admin cannot see other schools)
- [ ] Trial status confirmed or school on paid plan

**Stripe (per-school application fee — if applicable)**
- [ ] School has a Stripe account and is ready to collect real money
- [ ] School provides their live Stripe publishable key and secret key
- [ ] Enter live keys in school admin settings: `/schools/<slug>/admin/settings/` → Stripe card
- [ ] Verify keys start with `pk_live_` and `sk_live_` (not `pk_test_`)
- [ ] Send a real test enrollment to confirm fee appears in the school's Stripe Dashboard
- [ ] Confirm school receives the payment in their Stripe account

> **Note:** Per-school Stripe keys are independent of the platform `STRIPE_MODE`. Even when the platform is in `test` mode, a school can collect real application fees if their own live keys are set in their admin settings.

**Launch**
- [ ] Apply page link sent to school
- [ ] **Send Welcome Email** sent from Ops Portal (`/ops/schools/<slug>/`) — includes magic login link, enrollment URL, embed snippet
- [ ] One real submission completed by school staff
- [ ] School confirms confirmation email received
- [ ] School confirms admin access and can see submission

---

## E) Post-Go-Live Monitoring (first 48h)

- [ ] Monitor Sentry for errors
- [ ] Confirm email delivery (not going to spam)
- [ ] Verify submissions appear as expected
- [ ] Verify Stripe webhooks delivering (Stripe Dashboard → Developers → Webhooks)
- [ ] Collect initial feedback from school

**Known post-launch dev tasks (platform-wide, not per-school)**
- [ ] Fix `trial_expired.html` billing link — currently points to `/admin/billing/` (old Django Admin). Change to `{% url 'school_billing' school.slug %}`. Low risk, 4-line fix. Affects any trial school whose admin clicks the "upgrade" link on the expired public enrollment page.

---

## F) Trial → Paid Conversion

**Self-service (school-initiated):**
- [ ] School initiates upgrade from billing page in their admin
- [ ] Stripe Checkout completes
- [ ] Webhook received: `checkout.session.completed`
- [ ] School `plan` updated automatically (Starter / Pro / Growth)
- [ ] Trial banner disappears from school admin
- [ ] New features available immediately (reports, email, leads, etc. per plan)

**Operator-initiated (demo → real customer):**
- [ ] Use `/ops/schools/<slug>/convert/` → fills plan, creates admin, generates magic link
- [ ] Send Welcome Email from Ops Portal school detail page
- [ ] Complete onboarding checklist items as school gets set up

---

## G) Pilot Closeout

- [ ] Review pilot usage
- [ ] Confirm value delivered
- [ ] Share pricing / next steps
- [ ] Convert to paid OR close pilot

If closing:
- [ ] Mark as dead lead
- [ ] Optionally remove YAML and school config later

---

## Notes

- YAML files used for early demos are disposable — only production schools require long-term config hygiene.
- Application number (sequential per school) is the school-facing identifier; internal DB IDs stay internal.
- `trial_started_at` is auto-set on school creation — no manual action required unless resetting/extending a trial.
- Plan changes take effect immediately; no restart required.
