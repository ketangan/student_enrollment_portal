This file is what new users, school admins, or partners will read first. I’ve kept it very clear, non-technical, and structured so someone without Django experience can follow it.

# Student Enrollment Portal

Student Enrollment Portal is a multi-tenant web application that lets schools/programs collect enrollment or registration submissions online using **configurable YAML forms** — no coding required per school.

This MVP is ideal for small organizations (dance studios, arts schools, academies, summer programs, etc.) that currently collect applications via email or PDF.

---

## 🚀 What It Does

- One backend, many schools
- Each school has a **YAML form** that defines:  
  • fields and sections  
  • validation rules  
  • branding and theme  
  • optional file upload fields
- Applicants submit via a public form
- Data is stored in PostgreSQL
- School admins review applications in the admin UI
- Attachments can be downloaded
- Admins can export CSVs and view reports

---

## 🧠 How It Works (High-Level)

1. Each school has a **slug** (e.g., `my-dance-school`)
2. There is a YAML config file at `configs/schools/<school_slug>.yaml`
3. Visiting `/schools/<slug>/apply`:
   - loads the config
   - dynamically renders the application form
4. On POST:
   - data is validated
   - stored in the database
   - files are saved to disk
5. Admins use `/admin/` to review submissions and files

---

## 📦 Repo Structure

student_enrollment_portal/
├── config/                 # Django settings, URLs
├── core/                   # Models, views, admin
│   ├── services/           # YAML loading & helpers
│   ├── templates/          # Shared HTML templates
│   └── tests/              # Unit & integration tests
├── configs/
│   └── schools/            # YAML per school
├── static/                 # Static files (CSS, custom brand assets)
├── media/                  # Uploaded files
├── .env.example
├── README.md
├── OPERATIONS.md
└── manage.py

---

## 🛠 Local Setup (Step-by-Step)

1. Clone the repository:
   ```bash
   git clone <repo_url>
   cd student_enrollment_portal

	2.	Create & activate a virtual environment:

python3 -m venv venv
source venv/bin/activate


	3.	Install dependencies:

pip install -r requirements.txt


	4.	Configure your environment variables:

cp .env.example .env

Edit .env and add:

DJANGO_SECRET_KEY=<your-secret-key>
DJANGO_DEBUG=True
DATABASE_URL=postgres://<user>@localhost:5432/student_enrollment_portal
ALLOWED_HOSTS=localhost,127.0.0.1


	5.	Start Postgres (e.g., via Homebrew on macOS):

brew install postgresql@16
brew services start postgresql@16
createdb student_enrollment_portal


	6.	Run migrations:

python manage.py migrate


	7.	Create a superuser:

python manage.py createsuperuser


	8.	Start the server:

python manage.py runserver


	9.	Visit:
	•	Public app: http://127.0.0.1:8000/
	•	Admin UI: http://127.0.0.1:8000/admin/

⸻

➕ Adding a New School (No Code)
	1.	Copy an existing YAML file:

configs/schools/example-school.yaml


	2.	Rename it to match the slug:

my-new-school.yaml


	3.	Edit the YAML:

school:
  slug: "my-new-school"
  display_name: "My New School"


	4.	Restart the server
	5.	Your form is now live at:

/schools/my-new-school/apply



⸻

⚙ Branding + Theme

Each YAML may include optional branding:

branding:
  logo_url: "/static/logos/mylogo.png"
  theme:
    primary_color: "#111827"
    accent_color: "#2563EB"

You may also include custom CSS/JS overrides via static file references.

⸻

📄 File Uploads (MVP)

If the YAML has fields with type: file, applicants can upload documents/images.

Uploaded files are stored under:

media/uploads/<school_slug>/<submission_id>/

School admins can download attachments from the admin UI.
By default files are served by a download route that restricts access to logged-in admins.

⸻

📊 Admin Features

✔ View submissions per school
✔ Download attachments
✔ Export CSV (selected rows)
✔ School-scoped admin users
✔ Per-school reporting with filters

⸻

👤 Admin Users

There are two roles:

Superuser
	•	sees all schools & all data
	•	manages users and memberships

School Admin
	•	limited to one school
	•	sees only that school’s submissions
	•	cannot see other schools’ data

To create a school admin:
	1.	Go to /admin/ → Users → Add
	2.	Fill in user info
	3.	Choose the School (superuser only)
	4.	Save

The system automatically:
	•	sets is_staff = True
	•	creates a membership linking the user to the school

⸻

🧪 Testing

Run all unit and integration tests:

python -m pytest -q

Coverage target: ≥ 90%

If you use Playwright for E2E tests:

npx playwright test


⸻

🧩 Future Improvement Ideas
	•	Admin-friendly submission detail view (no JSON blob)
	•	Multi-step forms
	•	E-signature for waivers
	•	Per-school custom domain options
	•	Email invites / password reset via SMTP

⸻

❗ MVP Tips & Gotchas
	•	If custom CSS doesn’t load, verify the static path in the YAML
	•	If uploads disappear on deploy (non-persistent host), switch to S3 or attach a persistent disk
	•	School slug must match the YAML filename

---

## ✅ Updated **OPERATIONS.md**

> This doc is for internal operators, maintainers, or support engineers — the runbook for running, onboarding, and troubleshooting.

```markdown
# Student Enrollment Portal — Operations & Administration Guide

This document is for:
- Platform operators
- Support engineers
- Developers
- Admin/operations staff onboarding schools

---

## 🔑 Core Concepts

### Schools Are Defined in Two Places

#### YAML config (configs/schools)
Defines:
- form structure
- validation rules
- branding & theme
- file upload behavior

#### Database (Admin UI)
Defines:
- which schools are active
- admin user memberships
- scoped access

A YAML alone does not activate a school — it must be added in the Admin UI.

---

## 🆕 Activating a New School

1. Add YAML:
   - Copy `example-school.yaml`
   - Rename to `<slug>.yaml`
   - Edit content

2. Activate in Admin:
   - Go to `/admin/`
   - Core → Schools → Add
   - Enter:
     - Slug (matches YAML filename)
     - Display name
   - Save

The form is now live at:

/schools//apply

---

## 👤 Admin Roles & Permissions

**Superuser**
- full access
- sees all schools
- manages users/memberships

**School Admin**
- scoped to one school
- sees only that school’s submissions & reports
- cannot access other schools’ data

To create a school admin:
1. `/admin/ → Users → Add`
2. Fill in basic info
3. Select School (only superuser can do this)
4. Save
   - System sets `is_staff = True`
   - Creates a SchoolAdminMembership

If a user is logged in but sees no data:
- Ensure `is_staff = True`
- Confirm SchoolAdminMembership links user to the correct school

---

## � Plans & Feature Flags

Every school has a **plan** that controls which features are available. Plans are cumulative — each higher tier includes everything from the tiers below it.

| Feature                  | Trial | Starter | Pro | Growth |
|:-------------------------|:-----:|:-------:|:---:|:------:|
| Submission Status        | ✅    | ✅     | ✅  | ✅     |
| CSV Export               | ✅    | ✅     | ✅  | ✅     |
| Audit Log                | ✅    | ✅     | ✅  | ✅     |
| Reports & Charts         | ❌    | ✅     | ✅  | ✅     |
| Email Notifications      | ❌    | ✅     | ✅  | ✅     |
| File Uploads             | ❌    | ✅     | ✅  | ✅     |
| Custom Branding (CSS/JS) | ❌    | ❌     | ✅  | ✅     |
| Multi-Form Support       | ❌    | ❌     | ✅  | ✅     |
| Custom Statuses          | ❌    | ❌     | ✅  | ✅     |

### How It Works

- Each school's `plan` field determines its baseline feature set.
- The `feature_flags` JSON field stores **per-school overrides only** (not the full set).
- At runtime, flags are computed: `plan defaults + overrides → effective flags`.
- Overrides let you enable a Pro feature on a Starter school (e.g., for early clients), or disable a feature for a specific school.

### Setting a School's Plan

1. Go to `/admin/ → Schools → (select school)`
2. Choose a **Plan** from the dropdown
3. (Optional) Add overrides in the **Feature flags** JSON editor
4. Save

### Flag Reference

| Flag Key                       | Minimum Plan | What It Gates                                         |
|:-------------------------------|:-------------|:------------------------------------------------------|
| `status_enabled`               | trial        | Status column in admin + status filter                 |
| `csv_export_enabled`           | trial        | "Export CSV" admin action                              |
| `audit_log_enabled`            | trial        | Admin audit log recording                              |
| `reports_enabled`              | starter      | `/schools/<slug>/admin/reports` page                   |
| `email_notifications_enabled`  | starter      | Submission confirmation email dispatch                 |
| `file_uploads_enabled`         | starter      | Saving uploaded files from application forms           |
| `custom_branding_enabled`      | pro          | Custom CSS/JS injection from YAML branding             |
| `multi_form_enabled`           | pro          | Multi-step / multi-form routing per school             |
| `custom_statuses_enabled`      | pro          | YAML-defined custom status choices in admin            |

---
## 🔄 Plan Changes & Downgrades

### Upgrade (e.g. Trial → Starter → Pro)

Upgrades take effect **immediately**. Change the plan in `/admin/ → Schools`, save, and the school gains access to all features in the new tier. No migration or restart required.

### Downgrade (e.g. Pro → Starter)

Downgrades also take effect **immediately**. The system uses an **"immediate disable + data preservation"** policy:

- **Features are gated at request time.** The moment a plan changes, the next page load respects the new tier.
- **No data is ever deleted.** Submissions, files, audit logs, and custom statuses remain in the database regardless of plan.
- **No grace period.** Because plan changes are manual (admin-only), the operator is expected to communicate with the school before downgrading.

### Edge Cases on Downgrade

| Scenario | What Happens | Recommended Action |
|:---------|:-------------|:-------------------|
| **Pro → Starter: school uses multi-form** | Multi-step routing disabled; form falls back to single-page `/apply/`. YAML step definitions are ignored but still valid if the school upgrades again. | Review the school's YAML before downgrading. If steps contain very different fields, the flat single-page form may be confusing to applicants. Consider pausing the form or simplifying the YAML first. |
| **Pro → Starter: school uses custom statuses** | Admin dropdown reverts to default statuses (`new`, `reviewed`, `accepted`, `rejected`). Existing submissions keep their custom status *value* in the database, but it won't appear in the dropdown filter. | Export or bulk-update submissions with custom statuses to a default value before downgrading. |
| **Pro → Starter: school uses custom branding** | Custom CSS/JS stops injecting. Form renders with the default theme. | No action needed — form remains fully functional, just unstyled. |
| **Starter → Trial: school uses file uploads** | File upload fields from YAML still render, but the backend silently skips saving the file. Existing files remain on disk and downloadable. | Remove `type: file` fields from the school's YAML before downgrading, or the applicant will see a broken experience (upload appears to work but file is not saved). |
| **Starter → Trial: school uses email notifications** | Confirmation emails silently stop sending. No error is shown to the applicant. | Inform the school that applicants will no longer receive confirmation emails. |
| **Starter → Trial: school uses reports** | Reports page shows "Feature not available on your current plan." | No action needed — data is still in the DB and will reappear on upgrade. |

### Override Exceptions

If a school needs **one** Pro feature on a Starter plan (e.g., an early client who was promised multi-form), use the `feature_flags` JSON field to grant an override:

```json
{"multi_form_enabled": true}
```

---

# Stripe Billing — Operations (concise)

Required environment variables
- `STRIPE_SECRET_KEY` — Secret API key (Stripe Dashboard → Developers → API keys). Server-side only.
- `STRIPE_PUBLISHABLE_KEY` — Publishable key (Stripe Dashboard → Developers → API keys). Client-side checkout.
- `STRIPE_WEBHOOK_SECRET` — Webhook signing secret (Stripe Dashboard → Developers → Webhooks → click endpoint → Reveal signing secret).
- `STRIPE_PRICE_STARTER_MONTHLY` — Price ID for monthly Starter plan (Stripe Dashboard → Products → [Product] → Prices → ID).
- `STRIPE_PRICE_STARTER_ANNUAL` — Price ID for annual Starter plan (same location).

What happens when vars are missing
- Missing publishable/secret keys: billing page shows a warning and checkout/portal actions are blocked.
- Missing webhook secret: incoming webhooks cannot be verified and will be ignored (logged).
- Missing price IDs: UI shows “No pricing options configured.”

Local testing with Stripe CLI (quick)
- Install: https://stripe.com/docs/stripe-cli#install
- Login: `stripe login`
- Forward webhooks to local server (assumes `python manage.py runserver` on port 8000):
	- `stripe listen --forward-to http://localhost:8000/stripe/webhook/`
- Trigger common events:
	- `stripe trigger checkout.session.completed`
	- `stripe trigger customer.subscription.updated`
	- `stripe trigger customer.subscription.deleted`
- Verify in DB (school record updates): `stripe_customer_id`, `stripe_subscription_id`, `stripe_subscription_status`, `plan`.

Production (Render) checklist
- Add env vars in Render: `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_STARTER_MONTHLY`, `STRIPE_PRICE_STARTER_ANNUAL`.
- Create a webhook endpoint in Stripe: `https://<your-render-host>/stripe/webhook/` and subscribe to the three events above.
- Copy the endpoint signing secret into `STRIPE_WEBHOOK_SECRET`.
- Deploy and verify webhook deliveries return HTTP 200 and logs show handler processing.
- Note: the webhook path is intentionally outside admin and is CSRF-exempt.

Manual smoke checklist
- As superuser: visit `/admin/reports/` → confirm Billing link visible.
- As school admin: visit `/admin/reports/` → confirm hub shows your school + Billing link.
- Open Billing page: confirm current plan and pricing appear (if prices set).
- Start checkout (test keys): complete checkout; verify webhook updated `School.plan` and `stripe_*` fields.
- If `stripe_customer_id` exists: use Manage Billing → portal should open.

Quick troubleshooting
- Webhook signature errors: ensure `STRIPE_WEBHOOK_SECRET` matches the endpoint signing secret exactly.
- No pricing shown: verify `STRIPE_PRICE_STARTER_*` values are set to Price IDs (not product IDs).

---

## Billing States

The billing system implements **Option A**: after a paid subscription ends, the school becomes **inactive (locked)** and cannot revert to trial usage. Trial is onboarding-only. A locked school must re-subscribe to reactivate.

### State Grid

| State                | Conditions                                                                                     | School Access | Billing UI Behavior                                                                                  | Webhooks                                                                                                      |
|:---------------------|:-----------------------------------------------------------------------------------------------|:-------------|:-----------------------------------------------------------------------------------------------------|:--------------------------------------------------------------------------------------------------------------|
| **Trial**            | `stripe_subscription_id=""`, `plan="trial"`, `is_active=True`                                  | Active       | - Show "Upgrade Your Plan" pricing cards<br>- Hide "Manage Subscription" section                      | N/A                                                                                                           |
| **Active**           | `stripe_subscription_status` in `["active", "trialing", "past_due", "unpaid"]`, subscription exists | Active       | - Show "Manage Subscription" (Portal button)<br>- Hide upgrade cards<br>- Show note: "To change plans or billing cycles, use Manage Billing." | `checkout.session.completed`: Sets `stripe_*` fields, `plan`, `is_active=True`, clears cancel fields<br>`customer.subscription.updated`: Syncs status, plan, cancel fields |
| **Scheduled Cancel** | Subscription exists, `stripe_cancel_at` set OR `stripe_cancel_at_period_end=True`, status still active-ish | Active       | - Show "Manage Subscription" (Portal button)<br>- Show banner: "Your subscription will cancel on [date]"<br>- Hide upgrade cards | `customer.subscription.updated`: Sets `stripe_cancel_at`, `stripe_cancel_at_period_end`, `stripe_current_period_end` from Stripe data |
| **Ended/Locked**     | `is_active=False` (set by webhook when subscription deleted or canceled with no active period)  | **Locked**   | - Show "Your subscription ended and this account is now inactive" banner<br>- Show upgrade cards with copy: "Re-subscribe to reactivate"<br>- Hide "Manage Subscription" | `customer.subscription.deleted`: Sets `is_active=False`, `stripe_subscription_status="canceled"`, `plan="trial"`, clears cancel fields |

### Key Implementation Details

#### Webhook Handlers

**`handle_checkout_completed(session_data)`**
- **Purpose:** Link Stripe customer + subscription to school on successful checkout
- **Actions:**
  - Set `stripe_customer_id`, `stripe_subscription_id`, `stripe_subscription_status`
  - Determine `plan` from `price_id` (via `price_to_plan()`)
  - **Set `is_active=True`** (reactivate locked schools)
  - Clear cancel scheduling: `stripe_cancel_at=None`, `stripe_cancel_at_period_end=False`, `stripe_current_period_end=None`
- **Idempotent:** Safe to receive multiple times for same session

**`handle_subscription_updated(subscription_data)`**
- **Purpose:** Sync subscription status + plan changes from Stripe
- **Actions:**
  - Update `stripe_subscription_status` (e.g., `active` → `past_due`)
  - Update `plan` from subscription line items
  - Sync cancel scheduling: `stripe_cancel_at`, `stripe_cancel_at_period_end`, `stripe_current_period_end`
  - **If status is `canceled` AND no active period remains:** set `is_active=False`
- **Does NOT deactivate** if subscription is scheduled to cancel (still has active period)

**`handle_subscription_deleted(subscription_data)`**
- **Purpose:** Definitive end of subscription
- **Actions:**
  - Set `stripe_subscription_status="canceled"`
  - **Set `is_active=False`** (LOCK school)
  - Revert `plan="trial"` (locked, not usable as trial)
  - Clear all cancel scheduling fields

#### School Access Gating

Helper functions in `core/services/school_access.py`:

```python
is_school_active(school) -> bool
# Returns True if school.is_active is True

require_school_active(request, school)
# Returns lockout page (403) if school is inactive
# Returns None if school is active (view continues normally)
```

**Usage:**
- Billing page allows access even when locked (so users can re-subscribe)
- Other admin/school entrypoints should call `require_school_active()` to enforce lock

### Test Checklist

Use this checklist to verify billing state transitions work correctly:

#### 1. Trial → Active (Upgrade)
- [ ] Start with a school on `plan="trial"`, no Stripe subscription
- [ ] Visit Billing page, verify upgrade cards are shown
- [ ] Complete checkout (use Stripe test mode or `stripe trigger checkout.session.completed`)
- [ ] Verify webhook received and school updated:
  - `stripe_customer_id`, `stripe_subscription_id`, `stripe_subscription_status` set
  - `plan` changed to `"starter"` (or appropriate paid plan)
  - `is_active=True`
  - Cancel fields cleared
- [ ] Visit Billing page again, verify "Manage Subscription" shown, upgrade cards hidden

#### 2. Active → Scheduled Cancel
- [ ] Start with a school on active subscription
- [ ] Use Stripe Portal or Dashboard to schedule cancellation (cancel at period end)
- [ ] Trigger `customer.subscription.updated` event
- [ ] Verify school updated:
  - `stripe_cancel_at` or `stripe_cancel_at_period_end=True` set
  - `stripe_current_period_end` set
  - `is_active` still `True` (not locked yet)
- [ ] Visit Billing page, verify banner shows "Your subscription will cancel on [date]"
- [ ] Verify "Manage Subscription" still shown (can resume via Portal)

#### 3. Scheduled Cancel → Ended/Locked
- [ ] Wait for subscription period to end, or trigger `customer.subscription.deleted`
- [ ] Verify school updated:
  - `stripe_subscription_status="canceled"`
  - `is_active=False` (LOCKED)
  - `plan="trial"` (but locked, not usable)
  - Cancel fields cleared
- [ ] Visit Billing page, verify:
  - "Your subscription ended and this account is now inactive" banner shown
  - Upgrade cards shown
  - "Manage Subscription" hidden
- [ ] Attempt to access other admin pages (if gating implemented), verify lockout message

#### 4. Ended/Locked → Active (Re-subscribe)
- [ ] Start with a locked school (`is_active=False`)
- [ ] Complete checkout again (trigger `checkout.session.completed`)
- [ ] Verify school reactivated:
  - `is_active=True`
  - `stripe_subscription_id`, `stripe_customer_id`, `plan` updated
  - Cancel fields cleared
- [ ] Visit Billing page, verify back to "Active" state UI

#### 5. Regression: Multi-School Isolation
- [ ] Create two schools: School A (trial), School B (active subscription)
- [ ] Upgrade School A
- [ ] Verify School B unchanged (plan, status, IDs all intact)
- [ ] Cancel School B's subscription
- [ ] Verify School A unchanged

#### 6. Edge Cases
- [ ] Checkout completed webhook with missing `school_slug` metadata → logs warning, does not crash
- [ ] Subscription updated webhook for unknown subscription ID → logs warning, does not crash
- [ ] Subscription deleted webhook for unknown subscription ID → logs warning, does not crash
- [ ] Superuser can view billing page for any school (via `?school=<slug>` param)
- [ ] School admin can only view billing page for their own school (ignores `?school=` param)

### Quick Commands for Testing Webhooks Locally

```bash
# 1. Start local server
python manage.py runserver

# 2. In another terminal, forward Stripe events
stripe listen --forward-to http://localhost:8000/stripe/webhook/

# 3. Trigger events
stripe trigger checkout.session.completed
stripe trigger customer.subscription.updated
stripe trigger customer.subscription.deleted

# 4. Check database
python manage.py shell
>>> from core.models import School
>>> s = School.objects.get(slug="<your-test-school>")
>>> s.is_active, s.stripe_subscription_status, s.plan
```

---

This override is stored per-school and survives plan changes. To revoke it, remove the key or set it to `false`.

### Operator Checklist: Before Downgrading a School

1. **Check current feature usage** — open the school in admin, review plan and any `feature_flags` overrides
2. **Review YAML config** — does it use `steps:` (multi-form), `type: file` (uploads), `branding:` (custom CSS/JS)?
3. **Communicate with the school** — confirm they understand which features will be disabled
4. **Clean up edge cases** — bulk-update custom statuses, remove file fields from YAML if needed
5. **Change the plan** — save in admin; takes effect immediately
6. **Verify** — visit the school's public form and admin to confirm expected behavior

---
## �📩 Submissions Admin

What is displayed:
- Student / Applicant name
- Program / Class name
- Timestamp
- School (for superusers)

Features:
- Search by name, program, or school (if superuser)
- Export selected submissions to CSV
- View attachments from file uploads

---

## 📊 Reporting Access

Accessible from the admin sidebar:

/schools//admin/reports

Features:
- filter by date range
- program breakdown
- recent submissions

School admins may only view their own school reports.

---

## 🗃 File Upload Handling

Uploaded files are stored on disk under:

media/uploads/<school_slug>/<submission_id>/

Files uploaded via form are available for secure admin download:

/admin/uploads/<file_id>/

This route:
- requires staff login
- enforces school-scoped access
- streams files (works with local or remote storage)

**Important (Production):**  
Local disk storage is ephemeral on many hosts (e.g., Render without a persistent disk). Attach a persistent disk or use S3/remote storage if you need uploads to persist.

---

## 🧪 Testing

To run tests locally:

```bash
python -m pytest -q

With coverage:

python -m pytest --cov=core --cov-report=term-missing

CI:
GitHub Actions runs:
	•	dependency install
	•	migrations
	•	test suite

Deploy environments do not automatically run tests — CI protects the main branch.

⸻

⚠️ Known MVP Limitations
	•	No email backend (SMTP) configured by default
	•	Submission detail is stored as JSON
	•	No custom domain per school yet
	•	File preview only via download (no inline preview)

⸻

🧠 Troubleshooting Checklist

Upload fails / admin shows 404:
	•	Confirm the upload route exists: /admin/uploads/<file_id>/
	•	Confirm MEDIA_ROOT and storage are reachable
	•	Confirm file exists in media/

User logs in but sees no data:
	•	Check SchoolAdminMembership exists
	•	User must have is_staff = True

Form fields not saving:
	•	Confirm YAML field keys are unique and required fields are present
	•	Restart server after YAML save

⸻

🧾 Deployment Notes (Non-Technical)

Avoid losing uploads:
	•	Attach a persistent disk on your host OR
	•	Move to remote storage backend (S3) when ready

Static vs Media Files
	•	static: shipped with app
	•	media: uploaded by users
Settings control where these reside (STATIC_ROOT, MEDIA_ROOT, MEDIA_URL)

⸻

🛠 End-of-Day Checklist

Before handing off to schools:
	•	Confirm branding loads
	•	Submit a test application
	•	Verify attachment download
	•	Verify CSV export
	•	Verify school admin scoping

---
