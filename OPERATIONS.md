# Student Enrollment Portal — Operations & Administration Guide

For platform operators, support engineers, developers, and admin staff onboarding schools.

---

## Core Concepts

### Schools Are Defined in Two Places

**YAML config** (`configs/schools/<slug>.yaml`) — defines form structure, validation, branding, lead/scheduling config, export profiles.

**Database** (Admin UI) — defines plan, feature flags, active status, trial start date, Stripe billing fields, admin user memberships.

A YAML alone does not activate a school — it must have a School record in the database.

---

## Activating a New School

1. Create YAML: copy `example-school.yaml`, rename to `<slug>.yaml`, edit content
2. In `/admin/ → Core → Schools → Add`:
   - Slug (must match YAML filename exactly)
   - Display name
   - Plan: `trial` for new schools (trial clock starts automatically on first save)
3. Save — form is live at `/schools/<slug>/apply`
4. Create admin user: `/admin/ → Users → Add`, fill in info, select school, save
   - System sets `is_staff=True` and creates `SchoolAdminMembership`

---

## Admin Roles

| Role | Access |
|:-----|:-------|
| **Superuser** | All schools, all data, user/membership management |
| **School Admin** | One school only — submissions, leads, reports for that school |

If a school admin logs in but sees no data: confirm `is_staff=True` and `SchoolAdminMembership` exists linking them to the correct school.

---

## Plans & Feature Flags

Every school has a **plan** that controls feature availability. Plans are cumulative.

| Feature | Flag Key | Trial | Starter | Pro | Growth |
|:--------|:---------|:-----:|:-------:|:---:|:------:|
| Submission status | `status_enabled` | ✅ | ✅ | ✅ | ✅ |
| CSV export | `csv_export_enabled` | ✅ | ✅ | ✅ | ✅ |
| Audit log | `audit_log_enabled` | ✅ | ✅ | ✅ | ✅ |
| Reports & charts | `reports_enabled` | ❌ | ✅ | ✅ | ✅ |
| Email notifications | `email_notifications_enabled` | ❌ | ✅ | ✅ | ✅ |
| File uploads | `file_uploads_enabled` | ❌ | ✅ | ✅ | ✅ |
| Lead capture & pipeline | `leads_enabled` | ❌ | ✅ | ✅ | ✅ |
| Waiver/consent field | `waiver_enabled` | ❌ | ✅ | ✅ | ✅ |
| Custom branding (CSS/JS) | `custom_branding_enabled` | ❌ | ❌ | ✅ | ✅ |
| Multi-form / multi-step | `multi_form_enabled` | ❌ | ❌ | ✅ | ✅ |
| Custom statuses | `custom_statuses_enabled` | ❌ | ❌ | ✅ | ✅ |
| Lead → Submission conversion | `leads_conversion_enabled` | ❌ | ❌ | ✅ | ✅ |
| Save & Resume drafts | `save_resume_enabled` | ❌ | ❌ | ✅ | ✅ |
| AI Application Summary | `ai_summary_enabled` | ❌ | ❌ | ❌ | ✅ |

**YAML-configured features** (available regardless of plan, when configured in YAML):
- Scheduling link — `scheduling:` block in YAML
- YAML export profiles — `exports:` block in YAML (respects `csv_export_enabled`)
- Form embedding — iframe snippet always shown in admin school detail

### Setting a School's Plan

1. `/admin/ → Schools → (select school)`
2. Choose **Plan** from dropdown
3. (Optional) Add per-school overrides in **Feature flags** JSON field
4. Save — takes effect immediately

### Per-School Flag Overrides

The `feature_flags` JSON field stores **overrides only** (not the full set). At runtime: `plan defaults + overrides = effective flags`.

Example — give one Starter school access to multi-form:
```json
{"multi_form_enabled": true}
```

To revoke: remove the key or set to `false`.

### Plan Changes & Downgrades

Upgrades and downgrades take effect immediately on the next page load. No data is ever deleted on downgrade — features are gated at request time, data remains.

Key edge cases on downgrade:

| Scenario | What Happens |
|:---------|:-------------|
| Pro → Starter: multi-form YAML | Falls back to single-page form; YAML steps ignored |
| Pro → Starter: custom statuses | Dropdown reverts to defaults; existing values preserved in DB |
| Starter → Trial: file uploads | Upload fields render but files are not saved |
| Starter → Trial: email notifications | Confirmation emails silently stop sending |

**Operator checklist before downgrading**: check YAML for steps/file fields/custom CSS, communicate with school, bulk-update custom statuses if needed, then change plan.

---

## Trial System

### How Trials Work

- New schools are created with `plan="trial"`
- `trial_started_at` is auto-set to `timezone.now()` on the first save
- Trial length: **14 days** (`TRIAL_LENGTH_DAYS = 14` in `core/models.py`)
- After 14 days, `school.is_trial_expired` becomes `True`

### What Happens When a Trial Expires

| Entry Point | Behaviour |
|:------------|:----------|
| Public apply form (`/schools/<slug>/apply`) | Shows `trial_expired.html` — no submission created |
| Public lead capture form | Shows `trial_expired.html` — no lead created |
| Admin quick-add lead | Error message + redirect — no lead created |
| Admin lead → submission conversion | Error message + redirect — no submission created |
| Admin submission/lead edit forms | Read-only (change permission removed) |
| Admin dashboard (billing, reports, etc.) | Still accessible — school can view data and upgrade |

School admins see a persistent **trial banner** in the Django admin:
- Active trial: amber banner with days remaining + "Upgrade now" link
- Expired trial: red banner with "Your trial has expired" + "Upgrade now" link
- Superusers never see the banner

### Extending or Resetting a Trial

To extend a school's trial, update `trial_started_at` to a later date in `/admin/ → Schools`:

```
trial_started_at = now - (desired_remaining_days - 14 days)
```

Or set a future `trial_started_at` to give a fresh 14 days from that point.

### Converting Trial to Paid

School admin completes Stripe Checkout from the billing page. On `checkout.session.completed` webhook, `plan` is updated and `is_active` is confirmed `True`. Trial enforcement is lifted immediately.

---

## Stripe Billing

### Dual-Mode Env Var Convention

All Stripe keys come in `_TEST` and `_LIVE` variants. Set `STRIPE_MODE=test` (default) or `STRIPE_MODE=live` to select which set is active.

```
STRIPE_MODE=test   # or live
```

### Required Environment Variables

| Variable | Description |
|:---------|:------------|
| `STRIPE_SECRET_KEY_TEST` / `_LIVE` | Secret API key — server-side only |
| `STRIPE_PUBLISHABLE_KEY_TEST` / `_LIVE` | Publishable key — client-side checkout |
| `STRIPE_WEBHOOK_SECRET_TEST` / `_LIVE` | Webhook signing secret |
| `STRIPE_PRICE_STARTER_MONTHLY_TEST` / `_LIVE` | Price ID for monthly Starter |
| `STRIPE_PRICE_STARTER_ANNUAL_TEST` / `_LIVE` | Price ID for annual Starter |
| `STRIPE_PRICE_PRO_MONTHLY_TEST` / `_LIVE` | Price ID for monthly Pro (omit to hide from billing page) |
| `STRIPE_PRICE_PRO_ANNUAL_TEST` / `_LIVE` | Price ID for annual Pro |
| `STRIPE_PRICE_GROWTH_MONTHLY_TEST` / `_LIVE` | Price ID for monthly Growth (omit to hide) |
| `STRIPE_PRICE_GROWTH_ANNUAL_TEST` / `_LIVE` | Price ID for annual Growth |

Price IDs come from Stripe Dashboard → Products → [Product] → Prices → copy `price_xxx`. Plan options only appear on the billing page if the corresponding price ID env var is set.

### Billing State Grid

| State | Conditions | School Access | Billing UI |
|:------|:-----------|:-------------|:-----------|
| **Trial** | `plan="trial"`, `is_active=True`, no subscription | Active | Upgrade pricing cards shown |
| **Trial Expired** | `plan="trial"`, `trial_started_at` + 14d in past | Intake blocked | Upgrade pricing cards + expired banner |
| **Active** | Subscription active/past_due/unpaid | Active | "Manage Subscription" portal button |
| **Scheduled Cancel** | Subscription active, cancel scheduled | Active | "Manage Subscription" + cancel date banner |
| **Ended/Locked** | `is_active=False` | Locked (billing page only) | "Account inactive" banner + "Re-subscribe" cards |

### Webhook Handlers

**`checkout.session.completed`** — links Stripe customer + subscription to school, sets `plan`, `is_active=True`, clears cancel fields.

**`customer.subscription.updated`** — syncs status, plan, and cancel scheduling fields. Sets `is_active=False` if status is `canceled` with no active period remaining.

**`customer.subscription.deleted`** — sets `stripe_subscription_status="canceled"`, `is_active=False`. Does not revert `plan` (preserves tier for records).

### Local Testing with Stripe CLI

```bash
# Forward webhooks to local server
stripe listen --forward-to http://localhost:8000/stripe/webhook/

# Trigger events
stripe trigger checkout.session.completed
stripe trigger customer.subscription.updated
stripe trigger customer.subscription.deleted
```

### Production (Render) Checklist

- Set `STRIPE_MODE=live`
- Add all `_LIVE` env vars
- Create webhook endpoint in Stripe Dashboard: `https://<render-host>/stripe/webhook/`
- Subscribe to: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`
- Copy endpoint signing secret → `STRIPE_WEBHOOK_SECRET_LIVE`
- Deploy and verify webhook deliveries return HTTP 200

### Monitoring Cancellations

```bash
python manage.py billing_cancel_reminders
```

Logs upcoming cancellations (within 3 days) as Sentry WARNINGs and overdue cancellations (end date passed, still active) as Sentry ERRORs. Run daily via cron. Overdue schools require manual deactivation in admin (uncheck `is_active`).

### Troubleshooting

- **Webhook signature errors**: confirm `STRIPE_WEBHOOK_SECRET_TEST/LIVE` matches the endpoint signing secret exactly
- **No pricing shown**: verify price ID env vars are Price IDs (not product IDs) and `STRIPE_MODE` matches the key suffix

---

## Lead Pipeline

### YAML Configuration

Add a `leads:` block to the school's YAML to enable the public lead capture form:

```yaml
leads:
  heading: "Join our waitlist"
  programs:
    - "Ballet"
    - "Jazz"
```

Also enable the feature flag: `leads_enabled: true` (automatic on Starter+).

### Admin Workflow

- **Inbox**: leads sorted by status (New → Contacted → Trial Scheduled → Enrolled/Lost)
- **Quick-add lead**: "Add Lead" button on the leads changelist
- **Follow-up scheduling**: set `next_follow_up_at` on any lead
- **Bulk actions**: mark as contacted, mark as enrolled, mark as lost
- **Convert to Submission**: button on lead detail page — matches by email field, creates a `Submission` from the lead (Pro+ feature)

### Pipeline Analytics

Available in the Reports page (`/schools/<slug>/admin/reports`). Shows lead counts by status, conversion rate, and recent activity.

---

## Email (Resend)

Email is sent via the [Resend](https://resend.com) API.

Required env var: `RESEND_API_KEY`

Emails sent:
- **Submission confirmation** — sent to applicant on successful form submission (Starter+, `email_notifications_enabled`)
- **Staff notification** — sent to addresses in `notifications.submission_email.to` in YAML (Starter+)

The `from_email` in the YAML `notifications` block must be a verified sender domain in Resend.

Troubleshooting:
- Email not sending: check `RESEND_API_KEY` is set and valid
- From address rejected: verify the sender domain in Resend Dashboard
- Check Django logs for Resend API error responses

---

## Submissions Admin

Features:
- Search by name, program, school (superusers)
- Status tracking (configurable via YAML or custom statuses)
- Edit submission field data
- Export selected rows to CSV (flat or YAML-configured profile)
- AI application summary (Growth tier — "Generate Summary" button in change form)
- Download file attachments
- Audit log per submission

**Sequential application numbers**: each school has its own counter (`school_submission_number`). Displayed as the public application ID.

### YAML Export Profiles

Add an `exports:` block to map submission fields to a third-party system's format:

```yaml
exports:
  brightwheel:
    field_map:
      first_name:
        source: student_first_name
      homeroom:
        value: "Dance Academy"
      parent_1_email:
        source_any:
          - contact_email
          - guardian_email
```

Field map spec:
- `{source: key}` — looks up `submission.data[key]`; missing → empty + warning
- `{value: "literal"}` — hardcoded literal; never looked up
- `{source_any: [k1, k2]}` — first key with non-empty value wins; none found → empty + warning
- Bare string — treated as `source:` only

Export profiles appear as separate admin actions ("Export selected → Brightwheel CSV"). Capped at 5,000 rows per export.

---

## Save & Resume Drafts

Applicants can save a partial form via email. A magic link is emailed to them. Clicking the link restores the draft.

- Drafts are stored in `DraftSubmission` model
- Drafts expire (configurable; default 7 days)
- Completing submission deletes the draft
- Pro+ feature (`save_resume_enabled`)

---

## Form Embedding

Each school's apply form can be embedded on external websites via an iframe. The embed snippet is shown in the school's admin detail page under "Embed on your website".

`X-Frame-Options` is set to `SAMEORIGIN` to allow embedding.

---

## File Upload Handling

Uploaded files: `media/uploads/<school_slug>/<submission_id>/`

Secure admin download: `/admin/uploads/<file_id>/`
- Requires staff login
- Enforces school-scoped access

**Production note**: local disk is ephemeral on most hosts (Render without a persistent disk). Attach a persistent disk or configure S3/remote storage.

---

## Reporting

Accessible from admin sidebar → Reports, or `/schools/<slug>/admin/reports`.

Available to school admins (Starter+):
- Date-range filter
- Program breakdown with submission counts
- Recent submissions list
- Lead pipeline section (if leads enabled)

---

## Testing

```bash
# Unit + integration tests
python -m pytest -q

# With coverage
python -m pytest --cov=core --cov-report=term-missing

# E2E tests (Playwright)
npx playwright test
```

578+ unit/integration tests passing. E2E tests cover billing flows, apply form, and admin interactions.

---

## Troubleshooting

| Symptom | Check |
|:--------|:------|
| School admin sees no data | `SchoolAdminMembership` exists; user has `is_staff=True` |
| Apply form shows "trial expired" | School's `trial_started_at` + 14 days has passed — upgrade plan or extend `trial_started_at` |
| Upload fails / 404 | `MEDIA_ROOT` reachable; file exists in `media/`; upload route exists |
| Form fields not saving | YAML field keys unique; required fields present; restart server after YAML change |
| Webhook not processing | Stripe CLI forwarding active; signing secret matches; check logs for verification errors |
| Email not sending | `RESEND_API_KEY` valid; `from_email` is verified sender in Resend |
| No pricing options on billing page | Price ID env vars set to `price_xxx` IDs; `STRIPE_MODE` matches suffix |
