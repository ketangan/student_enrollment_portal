# Student Enrollment Portal — Operations & Administration Guide

For platform operators, support engineers, developers, and admin staff onboarding schools.

---

## Core Concepts

### Schools Are Defined in Two Places

**YAML config** (`configs/schools/<slug>.yaml`) — defines form structure, validation, branding, lead/scheduling config, export profiles.

**Database** (Admin UI) — defines plan, feature flags, active status, trial start date, Stripe billing fields, admin user memberships.

A YAML alone does not activate a school — it must have a School record in the database.

---

## Production Deployment Checklist

Run this before every production deploy. All items must be green before going live.

### Required Environment Variables

| Variable | Example | Notes |
|----------|---------|-------|
| `DJANGO_SECRET_KEY` | 50-char random string | App crashes at startup if missing in prod |
| `DJANGO_ENV` | `production` | Enables HTTPS redirect, secure cookies, Sentry |
| `DATABASE_URL` | `postgres://...` | PostgreSQL connection string |
| `ALLOWED_HOSTS` | `yourdomain.com,www.yourdomain.com` | Comma-separated |
| `CSRF_TRUSTED_ORIGINS` | `https://yourdomain.com` | Required for CSRF to work behind proxy |
| `DEFAULT_FROM_EMAIL` | `School Name <noreply@yourdomain.com>` | Must be a verified Resend sender |
| `RESEND_EMAIL_API_KEY` | `re_xxx` | From Resend dashboard |
| `BASE_URL` | `https://yourdomain.com` | Fallback base URL when APP_BASE_URL/DEMO_BASE_URL not set |
| `APP_BASE_URL` | `https://app.mypontora.com` | Base URL for production customer links (magic links, emails) |
| `DEMO_BASE_URL` | `https://demo.mypontora.com` | Base URL for prospect demo links |

### Recommended / Optional Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `SENTRY_DSN` | *(empty)* | Enable error tracking (strongly recommended) |
| `LOG_LEVEL` | `WARNING` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `RATELIMIT_ENABLE` | `true` | Rate limiting on public forms; disable in local dev |
| `STRIPE_MODE` | `test` | Set to `live` for real billing |
| `ANTHROPIC_API_KEY` | *(empty)* | AI summary feature; optional |

### Pre-Deploy Checks

```bash
# 1. Run migrations
python manage.py migrate --check  # must exit 0

# 2. Run full test suite
python -m pytest -q               # must be 0 failed

# 3. Collect static files
python manage.py collectstatic --noinput

# 4. Verify health check works
curl https://yourdomain.com/healthz/
# Expected: {"status": "ok"}

# 5. Verify DEBUG is off
python manage.py shell -c "from django.conf import settings; assert not settings.DEBUG"

# 6. Verify SECRET_KEY is not the default
python manage.py shell -c "
from django.conf import settings
assert settings.SECRET_KEY != 'unsafe-default-secret', 'SECRET_KEY not set!'
print('SECRET_KEY OK')
"
```

### Rate Limiting

Public form endpoints are rate-limited per IP when `RATELIMIT_ENABLE=true`:

| Endpoint | Limit |
|----------|-------|
| `POST /schools/<slug>/apply` | 30 requests / minute |
| `POST /schools/<slug>/lead-capture` | 20 requests / minute |

Exceeding the limit returns a 429 page. Limits are per-IP using Django's default cache backend.
For higher traffic, configure a Redis cache backend to share state across workers.

---

## Activating a New School

### Option A — Converting a Demo (recommended for prospects who said yes)

Use the Ops Portal:

1. `/ops/schools/<slug>/` → **Convert to Customer →** button (only visible when `is_demo=True`)
2. Fill in: real admin email, plan, trial length, whether to delete demo data
3. Submit — the conversion handles everything atomically:
   - Archives demo submissions/leads (rollback insurance)
   - Optionally deletes sample data
   - Expires demo access tokens
   - Creates/assigns real admin user
   - Flips `is_demo=False`, sets plan
   - Creates onboarding magic link (7 days)
   - Auto-completes first 4 checklist items
4. Copy the magic link from the success panel and send it to the school admin
5. Click **Send Welcome Email** to send the full onboarding package (magic link, enrollment URL, iframe snippet, QR code)

### Option B — Brand-New School (no prior demo)

1. Create YAML: copy `example-school.yaml`, rename to `<slug>.yaml`, edit content
2. In `/ops/schools/new/`:
   - Slug (must match YAML filename exactly)
   - Display name
   - Plan: `trial` for new schools (trial clock starts automatically on first save)
3. Save — form is live at `/schools/<slug>/apply`
4. In `/ops/schools/<slug>/` → School Admins → Add by email
5. From the same page, click **Send Welcome Email** to send the onboarding package

---

## Admin Roles

| Role | Access |
|:-----|:-------|
| **Superuser** | All schools, all data, user/membership management |
| **School Admin** | One school only — submissions, leads, reports for that school |

If a school admin logs in but sees no data: confirm `is_staff=True` and `SchoolAdminMembership` exists linking them to the correct school.

---

## School-Facing Admin UI

### Two Separate Admin Areas

| URL | Purpose | Who Uses It |
|:----|:--------|:------------|
| `/schools/<slug>/admin/` | School-facing dashboard — day-to-day submissions, leads, reports | School admins (primary workflow) |
| `/admin/` | Django admin — full data management, user/membership, plan changes | Superusers + school admins for detail pages |

School admins have access to both, but the school-facing UI is their primary workspace. The Django admin is for power operations (editing individual records, status changes, exports).

### Middleware Auto-Redirect

`SchoolAdminRedirectMiddleware` automatically redirects staff school admins from the Django admin **index** to their school dashboard. Deep links into the Django admin (e.g. `/admin/core/submission/42/change/`) are passed through untouched.

Only GET requests to the admin index root are intercepted — POST, PUT, etc. are never redirected. Superusers are never redirected.

### Changing the Admin URL

By default the Django admin is at `/admin/`. To harden a deployment:

1. Set `ADMIN_URL=myadmin/` in the environment (with trailing slash)
2. The middleware and URL router both read this setting at startup — no code changes needed

### School-Facing Routes

| Route | View | Notes |
|:------|:-----|:------|
| `/schools/<slug>/admin/` | Dashboard | KPI counts, recent submissions inbox |
| `/schools/<slug>/admin/submissions/` | Submissions list | Filter by status/search, cap 2,000 rows |
| `/schools/<slug>/admin/leads/` | Leads pipeline | Filter by status/search, cap 200 rows |
| `/schools/<slug>/admin/reports/` | Reports | Starter+ feature; `/admin/reports` 301s here |

Access gate (`_get_accessible_school_for_admin`): 404 if school slug not found, 403 if user has no membership for that school, 403 if school is inactive.

### Search Limitation

The submissions and leads search is Python-level substring matching against an in-memory queryset slice (up to 2,000 / 200 rows respectively). It does **not** scan the full table. If a school has many records and a match is not found, the result is incomplete — the UI shows a "Showing first N results" notice.

Future: move search to DB-level `ILIKE` filter before slicing.

### YAML Contact Fields

Lead and submission contact information (email, phone) is read from `submission.data[key]` using keys configured in the school's YAML. There is no canonical schema — if a YAML field key is renamed, existing records lose the displayed contact value (the raw data is preserved in `data`, but the display lookup will fail silently).

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
- Trial length: **30 days** (`TRIAL_LENGTH_DAYS = 30` in `core/models.py`)
- After 30 days, `school.is_trial_expired` becomes `True`

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
trial_started_at = now - (desired_remaining_days - 30 days)
```

Or set a future `trial_started_at` to give a fresh 30 days from that point.

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
| **Trial Expired** | `plan="trial"`, `trial_started_at` + 30d in past | Intake blocked | Upgrade pricing cards + expired banner |
| **Active** | Subscription active/past_due/unpaid | Active | "Manage Subscription" portal button |
| **Scheduled Cancel** | Subscription active, cancel scheduled | Active | "Manage Subscription" + cancel date banner |
| **Ended/Locked** | `is_active=False` | Locked (billing page only) | "Account inactive" banner + "Re-subscribe" cards |

### Webhook Handlers

**`checkout.session.completed`** — links Stripe customer + subscription to school, sets `plan`, `is_active=True`, clears cancel fields.

**`customer.subscription.updated`** — syncs status, plan, and cancel scheduling fields. Sets `is_active=False` if status is `canceled` with no active period remaining.

**`customer.subscription.deleted`** — sets `stripe_subscription_status="canceled"`, `is_active=False`. Does not revert `plan` (preserves tier for records).

### Local Testing with Stripe CLI

```bash
# Forward webhooks to local server (dev server runs on 8001)
stripe listen --forward-to http://localhost:8001/stripe/webhook/

# Trigger events
stripe trigger checkout.session.completed
stripe trigger customer.subscription.updated
stripe trigger customer.subscription.deleted
```

### Stripe Test Card Numbers

Use these in any Stripe payment form (checkout or application fee) when `STRIPE_MODE=test`.

| Card Number | Expiry | CVC | Result |
|:------------|:-------|:----|:-------|
| `4242 4242 4242 4242` | Any future date | Any 3 digits | ✅ Succeeds immediately |
| `4000 0025 0000 3155` | Any future date | Any 3 digits | ✅ Succeeds after 3DS authentication popup |
| `4000 0000 0000 9995` | Any future date | Any 3 digits | ❌ Always declines (insufficient funds) |
| `4000 0000 0000 0002` | Any future date | Any 3 digits | ❌ Always declines (generic decline) |
| `4000 0000 0000 3220` | Any future date | Any 3 digits | ⚠️ 3DS required — use to test the redirect-back confirm flow |

**Postal code**: any 5-digit zip (e.g. `12345`). **Name**: anything.

These cards work for both platform subscription billing (checkout sessions) and per-school application fees (PaymentIntents).

### Application Fee — End-to-End Local Test

The **Maplewood Learning Center** school is pre-configured for local application fee testing:

- **Apply URL**: `http://127.0.0.1:8000/schools/maplewood-learning/apply/`
- **Form type**: 3-step wizard (Student Info → Program & Schedule → Parent/Guardian)
- **Fee**: $50 non-refundable on the final step (Step 3 → Submit)
- **Stripe keys**: set to the same test keys as the platform (from `.env`)

Steps:
1. Fill in all 3 steps → click **Submit Application**
2. Payment page renders with Stripe Elements
3. Enter test card `4242 4242 4242 4242`, any future date, any CVC
4. Click **Pay $50 and Submit Application**
5. Stripe confirms → redirected to success page
6. In admin (`/schools/maplewood-learning/admin/submissions/`) → open the submission → `payment_status` shows `paid`, `payment_intent_id` populated

To test a **declined card**: use `4000 0000 0000 9995` — page re-renders with a Stripe error message; no submission is created.

To test **3DS authentication**: use `4000 0025 0000 3155` — Stripe modal opens for OTP; after completing, redirects to confirm URL; submission created normally.

To configure application fees for a different school: go to `/ops/schools/<slug>/` and fill in **App Fee Stripe Public Key** and **App Fee Stripe Secret Key**, then add the `application_fee:` block to the school's YAML.

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

Required env var: `RESEND_EMAIL_API_KEY` (note: not `RESEND_API_KEY` — the env var name is `RESEND_EMAIL_API_KEY`)

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

Each school's apply form and interest (lead capture) form can be embedded on external websites or linked from a button.

`X-Frame-Options` is set to `SAMEORIGIN` — iframes from external origins are blocked by default. To allow embedding on a school's own domain, set `EMBED_ALLOWED_ORIGINS` in settings or switch to `X_FRAME_OPTIONS = "ALLOWALL"` (not recommended for multi-tenant use).

**Recommended approach for most schools: link-out buttons.** Iframes have mobile scaling issues and same-origin restrictions. A button that opens the form in a new tab gives a better mobile experience and avoids CORS/frame-busting concerns.

### Option A — Link-Out Button (Recommended)

Paste this HTML snippet on the school's website:

```html
<!-- Apply Now button -->
<a href="https://yourdomain.com/schools/SCHOOL_SLUG/apply/"
   target="_blank"
   style="display:inline-block;padding:12px 28px;background:#2563eb;color:#fff;
          font-size:16px;font-weight:600;border-radius:8px;text-decoration:none;">
  Apply Now &rarr;
</a>

<!-- Request Info button (lead capture form) -->
<a href="https://yourdomain.com/schools/SCHOOL_SLUG/interest/"
   target="_blank"
   style="display:inline-block;padding:12px 28px;background:#f8fafc;color:#1e293b;
          font-size:16px;font-weight:600;border-radius:8px;text-decoration:none;
          border:1px solid #e2e8f0;">
  Request Info &rarr;
</a>
```

Replace `SCHOOL_SLUG` with the school's slug (e.g. `young-minds-la`).

### Option B — Iframe Embed

Only use this if the school's website is on the **same domain** as the portal, or if `X_FRAME_OPTIONS` has been relaxed:

```html
<!-- Apply form iframe -->
<iframe
  src="https://yourdomain.com/schools/SCHOOL_SLUG/apply/"
  width="100%"
  height="800"
  style="border:none;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.08);"
  title="Enrollment Application">
</iframe>

<!-- Interest / lead capture form iframe -->
<iframe
  src="https://yourdomain.com/schools/SCHOOL_SLUG/interest/"
  width="100%"
  height="500"
  style="border:none;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.08);"
  title="Request Information">
</iframe>
```

**Mobile note**: add `<meta name="viewport" content="width=device-width, initial-scale=1">` to the host page to prevent scaling issues. Set `height="auto"` is not supported in standard iframes — use a fixed height or a JS `postMessage` resize approach.

The embed snippet (pre-filled with the correct URL) is shown in the school's Django admin detail page under "Embed on your website".

---

## File Upload Handling

Uploaded files: `media/uploads/<school_slug>/<submission_id>/`

Secure admin download: `/admin/uploads/<file_id>/`
- Requires staff login
- Enforces school-scoped access

**Production note**: local disk is ephemeral on most hosts (Render without a persistent disk). Attach a persistent disk or configure S3/remote storage.

---

## Program Management

Programs are DB records (`SchoolProgram`) that drive the program-selection field on the enrollment form. They replace the static `options:` list in the YAML for that field.

### Prerequisites

1. Set `School.program_field_key` in Django admin (Superadmin → Schools → edit school) to match the YAML field key (e.g. `interested_in`).
2. Remove the static `options:` block from that field in the school's YAML — the DB records take over.

### School Admin UI

School admins manage programs via **Settings → Programs**:

- **Add Program** — name (required), capacity (optional), auto-enroll toggle
- **Edit Program** — update any field; Activate/Deactivate toggle and (if inactive + no submissions) Remove are on the edit page, not the list
- **Show inactive** checkbox on the settings list reveals inactive programs (hidden by default)
- **Remove** is a soft delete — sets `is_deleted=True`, never hard-deletes the DB row; recovery requires Django admin or a direct DB update

### Auto-Enrollment

When `auto_enroll=True` on a program:
- Submission status is set to `Enrolled` on submit (if capacity available)
- If `waitlist_enabled=True` and at capacity → `Waitlisted`
- If at capacity with no waitlist → stays `New` (no status change, no audit event)

Concurrency-safe via `select_for_update()` on `SchoolProgram`.

### Manual Enrollment Override

For DB-program schools (`program_field_key` set), admins can manually move a student to `Enrolled` or `Waitlisted` even if those statuses aren't in the YAML `submission_statuses` list.

`Enrolled` and `Waitlisted` are always injected into the effective status choices for these schools via `get_effective_submission_status_choices()`. This affects all three status-update surfaces:
- **Detail page pipeline sidebar** — clickable steps include Enrolled/Waitlisted
- **List-page inline dropdown** — inline status select includes both options
- **Bulk status update** — Enrolled/Waitlisted appear in the bulk bar

Transition graph enforcement applies only to YAML-to-YAML moves. Enrolled and Waitlisted are system statuses that bypass the graph — admins can always reach them from any YAML status.

Manual override actions are audit-logged with `manual_enrollment_override: true` plus `program_code` and `session_code` when a program/session FK is set on the submission.

### Seeding Programs from YAML (management command)

```bash
python manage.py seed_school_programs <slug>
```

Reads any `options:` under the `program_field_key` field in the school's YAML and creates `SchoolProgram` records. Safe to re-run (skips existing codes).

### Reports

When `program_field_key` is set, the Reports page includes a **Program Enrollment Breakdown** section (§4.6):
- Horizontal stacked bar chart: Enrolled / Waitlisted / Pending / Declined per program
- Table with conversion rate (Enrolled / Submitted)
- Inactive programs that had submissions in the period appear dimmed under "Past Programs"
- Scoped to the selected date range (7d / 30d / 90d)

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

1119 unit/integration tests passing. E2E tests cover billing flows, apply form, and admin interactions.

---

## Sales Narrative

### One-Line Pitch

**Student Enrollment Portal gives school admins one place to capture interest, track families through the pipeline, and enroll students — no spreadsheets, no missed follow-ups.**

### Problems It Solves

- **Interest forms go into a void** — families fill out a Google Form and never hear back; the admin has no pipeline view
- **Follow-ups live in someone's head** — who to call, when to call, what was said last time: all manual
- **Leads and applications are disconnected** — a family who filled out an interest form last month submits an application today; the admin has no idea they're the same person
- **No visibility into where families drop off** — admins can't answer "how many leads became applications, how many enrolled?" without exporting CSVs and counting manually
- **Every school is different** — enrollment workflows, required fields, and status names vary; rigid software forces process changes

### How This Product Solves Each

- **Single intake funnel** — interest forms (leads) and application forms (submissions) flow into one admin dashboard; nothing is siloed
- **Structured follow-up** — every lead has a status, a last-contacted date, and a scheduled follow-up; the dashboard surfaces what needs attention today
- **Lead → Submission conversion** — one click from a lead detail page opens a pre-filled application form; when submitted, the lead and submission are linked for life
- **Conversion reporting built in** — Reports show Leads → Applications → Enrolled with rates, week-over-week trends, and a "where you're losing people" section with clickable filters
- **YAML-driven forms** — each school's form fields, validation rules, status names, and workflows are defined in a config file; no code changes per school

### Plans

| Plan | Monthly | Best For |
|:-----|:--------|:---------|
| **Starter** | $49.99/mo | Schools new to digital enrollment; includes leads, reports, email confirmations, CSV export |
| **Pro** | $99/mo | Growing schools; adds custom branding, save & resume drafts, lead → submission conversion tracking, multi-form support |
| **Growth** | $199/mo | High-volume schools; adds AI application summary for fast review, all Pro features |

All plans start with a **30-day full-featured trial** — no credit card required.

### Customer Success Template

*(Leave blank — fill in with a real school's story after first paying customer)*

> **[School Name]** was spending ___ hours per week managing enrollment in spreadsheets.
> After switching, they reduced follow-up time by ___% and enrolled ___ more students in the first ___ months.

---

## Demo Walk-Through

Use this script to walk a prospect through the product in under 5 minutes.

### Setup (do this before the demo)

```bash
# 1. Use the existing demo school (young-minds-la or enrollment-request-demo)
#    or create a fresh one:
python manage.py shell -c "
from core.models import School, SchoolAdminMembership
from django.contrib.auth.models import User
s = School.objects.create(slug='demo', display_name='Demo School', plan='trial')
u = User.objects.create_user('demo', password='demo', is_staff=True)
SchoolAdminMembership.objects.create(user=u, school=s)
print('Done — login: demo / demo')
"

# 2. Create leads in different pipeline stages
python manage.py shell -c "
from core.models import Lead, School
school = School.objects.get(slug='demo')
Lead.objects.create(school=school, name='Sarah Chen', email='sarah@example.com', status='new', source='organic')
Lead.objects.create(school=school, name='Marcus Williams', email='marcus@example.com', status='contacted', source='organic')
Lead.objects.create(school=school, name='Jordan Lee', email='jordan@example.com', status='trial_scheduled', source='referral')
print('3 leads created: new, contacted, trial_scheduled')
"

# 3. Create submissions in different stages + 1 converted lead
python manage.py shell -c "
from core.models import School, Submission, Lead
from django.utils import timezone
school = School.objects.get(slug='demo')
# Submissions at various stages
Submission.objects.create(school=school, status='New',
    data={'student_first_name':'Emma','student_last_name':'Lee','contact_email':'emma@example.com'})
Submission.objects.create(school=school, status='In Review',
    data={'student_first_name':'Liam','student_last_name':'Brown','contact_email':'liam@example.com'})
enrolled_sub = Submission.objects.create(school=school, status='Enrolled',
    data={'student_first_name':'Zoe','student_last_name':'Kim','contact_email':'zoe@example.com'})
# Converted lead — shows the full lead-to-enrollment journey
Lead.objects.create(school=school, name='Zoe Kim', email='zoe@example.com',
    status='enrolled', source='organic',
    converted_submission=enrolled_sub, converted_at=timezone.now())
print('3 submissions + 1 converted lead created')
"
```

### Demo Script (5 minutes)

**1. Dashboard** (`/schools/demo/admin/`)
- Show the KPI cards: "New submissions", "New Leads", "Needs Attention", "Enrolled"
- Point out: "Everything needing action is right here — no hunting through spreadsheets"
- Click a KPI card to drill into the filtered list

**2. Leads pipeline** (`/schools/demo/admin/leads/`)
- Show leads list with status badges (New → Contacted → Trial Scheduled → Enrolled)
- Click a lead in "new" or "contacted" status → show detail page: contact info, pipeline state
- Demonstrate "Mark Contacted" — updates last contacted date instantly
- Show "Open Form →" — opens pre-filled enrollment form in new tab (lead details auto-filled)
- Show "Send link to family" — copy the pre-filled link to send to the family directly
- Show the converted lead (Zoe Kim) → click "View Submission →" to jump to the linked submission

**3. Submissions inbox** (`/schools/demo/admin/submissions/`)
- Show "New" row highlighted — needs review
- Click a submission → show detail: form answers, parent contact, linked lead (if any)
- Show status transition buttons (e.g. Approve / Decline) from YAML config

**4. Lead → Submission conversion (closed loop)**
- Click Marcus Williams (contacted lead) → click "Open Form →" → fill and submit the form
- Return to Marcus's lead — it now shows "Converted — linked to a submission"
- Go to the linked submission → "Linked Lead" card shows Marcus's lead with "Open Lead" button
- This demonstrates the full loop: lead → enrollment form → submission, all traceable

**5. Reports** (`/schools/demo/admin/reports/`)
- Show funnel KPI bar: Leads → Applications → Enrolled → Conversion rate
- Show 7-day trend (requires data from past 2 weeks for comparison)
- Show "Pipeline Gaps" section: click "View →" next to "Active leads not yet converted"
  — pre-filters the leads list to show only unconverted active leads

### What the Demo Proves

- Leads come in → admins follow up → leads become applications → applications become enrollments
- Every step has a clear next action, no dead ends
- Reports show exactly where families are dropping off

---

## Lead Capture — Public Form + Webhook

### Overview

Two intake paths feed leads directly into the Leads admin without requiring manual entry:

| Path | URL | Source label |
|------|-----|--------------|
| Public inquiry form | `/schools/<slug>/lead/` | `website_lead_form` |
| Embeddable (iframe) | `/schools/<slug>/lead/?embed=1` | `website_lead_form` |
| External webhook | `POST /webhooks/leads/<slug>/<token>/` | `webhook` |

All leads created by either path appear immediately in `/schools/<slug>/admin/leads/`, update dashboard counts, and flow into the lead follow-up pipeline.

### Public Inquiry Form

A lightweight 5-field form: Name, Email, Phone (optional), Program Interest (optional), Message (optional).

**YAML config** (`leads:` section):
```yaml
leads:
  form_title: "Request Information"
  form_description: "Tell us what you're looking for and we'll follow up."
  cta_text: "Send My Request"
  success_message: "Thanks! We'll follow up soon."
  confirmation_enabled: true   # sends confirmation email to family
  notify_to: "admin@school.com"  # blank = no admin notification
```

All keys are optional — defaults apply if absent (backward-compatible with existing YAMLs).

If the school has DB-driven programs (`school.program_field_key` set), active programs appear as program interest options automatically.

**Spam protection**: honeypot field silently discards bot submissions. Rate limit: 10 POSTs/minute/IP.

### Webhook Lead Intake

Allows schools to forward their existing Contact Us / inquiry form into Pontora via Zapier, Make, WordPress, Wix, Squarespace, or custom HTML.

**Setup**:
1. Generate a token from the Django shell or admin: `from core.services.lead_intake import ensure_lead_webhook_token; ensure_lead_webhook_token(school)`
2. Give the school the URL: `POST https://your-domain/webhooks/leads/<slug>/<token>/`
3. School configures their form tool to POST to that URL

**Payload**: JSON (`Content-Type: application/json`) or form-encoded. Common field aliases are mapped automatically:

| Accepted names | Maps to |
|----------------|---------|
| `name`, `parent_name`, `contact_name`, `guardian_name` | Lead.name |
| `email`, `parent_email`, `contact_email` | Lead.email |
| `phone`, `parent_phone`, `contact_phone` | Lead.phone |
| `student_name`, `child_name` | Lead.data["student_name"] |
| `program`, `program_interest`, `interested_in` | Lead.interested_in_label/value |
| `message`, `notes`, `comments` | Lead.data["message"] |

Unknown fields are stored in `Lead.data["extra"]`.

**Validation**: Name required; at least one of email or phone required.

**Responses**:
- `200 {"ok": true, "lead_id": "abc123"}` — success
- `400 {"ok": false, "error": "..."}` — validation failure
- `404 {"ok": false, "error": "Not found."}` — bad token or inactive school

Payload cap: 50 KB. Larger bodies are rejected with 400.

---

## Customer Onboarding (Ops Portal)

The Ops Portal (`/ops/`) is the primary tool for activating new schools. All onboarding actions are logged to the audit trail.

### Demo → Customer Conversion

When a prospect demo converts, use `/ops/schools/<slug>/convert/`:

| Step | What happens |
|:-----|:------------|
| Archive | Demo submissions/leads/config saved to `DemoArchive` (rollback available) |
| Clean up | Sample submissions/leads optionally deleted |
| Tokens | All demo access tokens expired immediately |
| Access | Demo admin membership removed (user account preserved); new real admin created/assigned |
| School | `is_demo=False`, plan updated, trial clock reset if applicable |
| Magic link | 7-day onboarding token created (`purpose=onboarding`) — logs admin in without a password |
| Checklist | First 4 items auto-completed: school_created, plan_configured, trial_configured, admin_invited |

The operation is atomic and idempotent — safe to retry if interrupted.

### Onboarding Checklist

Each non-demo school has a 15-item fixed checklist in the Ops Portal school detail page. Items toggle on click. Completion is logged per-user.

### Welcome Email

The **Send Welcome Email** button (Ops Portal → school detail) sends the school admin:
- Magic login link (7 days, no password needed)
- Admin portal URL + enrollment form URL
- iframe embed snippet + QR code (for flyers/signage)
- Getting started steps

Requires: at least one `SchoolAdminMembership` with an email address, and `RESEND_EMAIL_API_KEY` configured.

### Demo Access Tokens After Conversion

Old `purpose=demo` tokens for converted schools redirect to the live enrollment form (not an error page). New `purpose=onboarding` tokens log in the real admin with no demo banner.

---

## Troubleshooting

| Symptom | Check |
|:--------|:------|
| School admin sees no data | `SchoolAdminMembership` exists; user has `is_staff=True` |
| Apply form shows "trial expired" | School's `trial_started_at` + 30 days has passed — upgrade plan or extend `trial_started_at` |
| Upload fails / 404 | `MEDIA_ROOT` reachable; file exists in `media/`; upload route exists |
| Form fields not saving | YAML field keys unique; required fields present; restart server after YAML change |
| Webhook not processing | Stripe CLI forwarding active; signing secret matches; check logs for verification errors |
| Email not sending | `RESEND_API_KEY` valid; `from_email` is verified sender in Resend |
| No pricing options on billing page | Price ID env vars set to `price_xxx` IDs; `STRIPE_MODE` matches suffix |
| Lead form 404 | School DB record exists and `is_active=True`; YAML config file present |
| Webhook returns 404 | Token matches `school.lead_webhook_token`; school `is_active=True`; token not empty |
| Webhook lead not appearing in admin | School `leads_enabled` feature flag active; check audit log for creation entry |
