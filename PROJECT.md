# Student Enrollment Portal — Project Source of Truth

This file is the canonical reference for decisions made, work completed, work in progress,
and work planned. Update it after every meaningful commit or decision. It is written for
both human and AI readability — future sessions should read this before doing anything.

---

## What This Product Is

A multi-tenant SaaS student enrollment platform for schools. Schools sign up, get a YAML-driven
application form, and manage the full enrollment pipeline (leads → applications → enrolled) through
a modern web admin. Operators (superadmins) manage all schools from a separate ops portal.

**Stack**: Django 6, PostgreSQL, Stripe billing, Resend/Anymail email, Whitenoise static files,
deployed on Render. 854+ passing pytest tests.

---

## Architecture Decisions (locked — do not change without discussion)

| Decision | Choice | Reason |
|----------|--------|--------|
| Form structure | YAML per school in `configs/schools/<slug>.yaml` | Zero-code school onboarding |
| YAML field keys | **Immutable** once live | Changing key = data loss for saved submissions |
| Feature gating | Plan-based defaults + per-school JSON overrides (`School.feature_flags`) | Flexible without model changes |
| Multi-tenancy | slug → YAML config → DB queryset scoping → membership check | Every query scoped to school |
| Billing | Stripe Checkout + webhooks; dual test/live mode via env suffix | Standard SaaS pattern |
| Email | Anymail + Resend backend | Provider-agnostic |
| Static files | Whitenoise with CompressedManifest | Works on Render without S3 |
| Admin UI (school) | Custom Django views + templates at `/schools/<slug>/admin/` | Better UX than Django admin |
| Admin UI (ops) | Custom views at `/ops/` | See Ops Portal section below |
| Django admin | Kept alive at `/admin/` (or `ADMIN_URL` env var) as raw escape hatch | Do not remove |
| SMS | NOT implemented, NOT planned | Explicit out of scope |
| Scheduling calendar | NOT implemented, NOT planned | Explicit out of scope |

---

## Repository Layout (key paths)

```
config/           Django project settings, urls, wsgi
configs/schools/  Per-school YAML files (one per school slug)
core/
  admin/          Django admin registrations (split by concern)
  migrations/     DB migrations (currently through 0029)
  models.py       All models
  views.py        All school admin + public views (~5000 lines — refactor pending)
  urls.py         School admin + public URL patterns
  services/       billing_stripe.py, notifications.py, validation.py, config_loader.py, etc.
  tests/          All pytest tests
  templatetags/   Jazzmin patch + custom tags
templates/
  school_admin/   Modern school admin UI templates
  ops/            NEW — Ops superadmin UI templates (Phase 1)
  admin/          Django admin overrides (Jazzmin)
static/admin/     dashboard.css, reports.css, theme JS
```

---

## URL Structure

| Prefix | Who Uses It | What It Is |
|--------|-------------|------------|
| `/` | Public / families | Enrollment form, resume, confirmation |
| `/schools/<slug>/admin/` | School admins + superusers | Modern school management UI |
| `/ops/` | Superusers only | Ops portal (NEW — see below) |
| `/admin/` | Superusers only | Django admin — raw escape hatch, do not remove |

---

## Plans & Feature Flags

Plans: `trial`, `starter`, `pro`, `growth`

Key constants: `TRIAL_LENGTH_DAYS = 30` in `core/models.py`

Feature flags are defined in `core/services/feature_flags.py`. Plan defaults + per-school
JSON overrides in `School.feature_flags`. Superusers bypass most feature gates.

---

## Completed Features

### School Admin UI (`/schools/<slug>/admin/`)

| Phase | What Was Built | Test file |
|-------|---------------|-----------|
| 2 | Leads pipeline workflow — status transitions, `admin_lead_yaml.py` | `test_lead_workflow.py` |
| 3 | Lead → Submission conversion; `DraftSubmission.lead` FK; migration 0026 | `test_lead_start_enrollment.py` |
| 4 | Submission detail + Lead detail pages; Django admin fallback links | `test_detail_pages.py` |
| 5 | Lead edit: notes, follow-up date, Mark Contacted | `test_lead_update.py`, `test_lead_ux.py` |
| 6 | Submission internal notes (`Submission.internal_notes`); migration 0027 | `test_submission_update.py` |
| 7/8 | CSV export for submissions + leads; shared filter helpers | `test_export_csv.py` |
| 9 | Mobile hamburger nav, responsive detail grid, clipboard copy buttons | `test_polish.py` |
| 10 | Admin create/edit for leads and submissions; multi-form school support | `test_create_edit.py` |
| 11 | Follow-up system (`next_follow_up_at`, `last_contacted_at`); migration 0028; smart filter pills; bulk actions | `test_phase11.py`, `test_inbox_workflow.py` |
| 12 | Email: send message to family, resend confirmation, workflow notifications | `test_phase12.py` |
| 13 | Hardening: cross-school 404s, config=None resilience, mark-contacted idempotency, draft select_for_update | `test_phase13.py` |
| 14 | Conversion intelligence: funnel metrics, trend stats, pipeline gaps, stale counts; `not_converted`/`not_enrolled` filters | `test_phase14.py` |
| 15 | Family status page (token-based public URL); admin post-public-note; confirmation email status link | `test_phase15.py`, `test_family_status_page.py` |
| 17 | Billing alignment: `feature_disabled.html` with upgrade hints; billing nav fixes | `test_phase17.py` |
| 18 | Application fees (Stripe, school-direct); `apply_payment_view`; `Submission.payment_status` | `test_phase18.py` |
| 19 | Enrollment analytics reports: leads funnel, program mix, time-series, source rows | `test_phase19_reports.py` |
| — | Enrollment capacity limits: per-program YAML soft caps; waitlist banner; admin badge; capacity override with audit log | `test_capacity.py` |
| — | AI-generated submission summary | `test_ai_summary.py` |
| — | Generic CSV export profiles (YAML-configured field maps, e.g. Brightwheel) | `test_export_profiles.py` |

### Ops Portal (`/ops/`)

| Phase | What Was Built | Test file |
|-------|---------------|-----------|
| Ops 1 | Auth guard, dashboard, schools CRUD, memberships inline, users CRUD, login/logout redirects | `test_ops_phase1.py` |
| Ops 2 | Cross-school submissions list, leads list, aggregate reports | `test_ops_phase2.py` |

### Platform / Infrastructure

| What | Detail |
|------|--------|
| Trial system | `School.trial_started_at` + `TRIAL_LENGTH_DAYS=30`; trial banner in school admin (hidden from superusers) |
| Trial end date override | `School.trial_end_date` DateField (migration 0029); superadmin can extend/clear |
| Stripe billing | Checkout, webhooks, subscription lifecycle; `billing_stripe.py` |
| Rate limiting | `django_ratelimit` on public form endpoints |
| Password change | Custom school admin page at `/schools/<slug>/admin/password/` |
| Views refactor | `views.py` split into `views_public.py`, `views_school_common.py`, `views_school_dashboard.py`, `views_school_submissions.py`, `views_school_leads.py`; `views.py` kept as re-export facade |
| Custom login | Standalone `/login/` page; superuser → `/ops/`, school admin → school dashboard |

---

## Current State

- **974 passing tests**
- All views refactored — `core/views.py` is now a thin re-export facade
- Ops Portal (Phases 1 + 2) complete — Django admin kept as raw escape hatch only
- Custom login/logout flow in place (no longer through Django admin)

---

## Ops Portal — Architecture Notes

- `/ops/` manages the **business** (schools, plans, billing, users, memberships)
- `/schools/<slug>/admin/` manages the **work** within a school (submissions, leads, reports)
- Django admin at `/admin/` stays alive as raw data escape hatch — do not remove
- `ops_required` decorator guards all `/ops/` views: `is_superuser` check, redirect to `/login/`

---

## Planned Features (Prioritised)

### Program Management — DB-Driven Programs (next to implement)

**Why we are building this:**
- Schools cannot self-manage programs today — every change (new program, rename, capacity edit)
  requires an operator to edit YAML and redeploy. This is a hard blocker for high-churn schools.
- Some schools add 10+ new programs every week. YAML cannot support that workflow.
- Small schools (karate, music) want students automatically enrolled or waitlisted at submission
  time — with no admin present. That requires reliable DB-level slot tracking per program.
- Program renames break string matching against JSON data — a FK solves this permanently.
- Slot count adjustments must be self-service for school admins, not require operator involvement.

**Key design decisions:**

1. **`SchoolProgram` model** (new) — authoritative source of truth for programs per school:
   - `school` ForeignKey(School)
   - `name` CharField (display label)
   - `code` CharField — matches the value stored in `Submission.data[program_field_key]`.
     **Editable while no submissions reference it; locked once any submission uses this code.**
     (Admins make typos during setup — don't make setup painful by locking immediately.)
   - `is_active` BooleanField (default True) — deactivate, never hard-delete if submissions exist
   - `display_order` PositiveIntegerField (default 0) — controls form dropdown order
   - `capacity` IntegerField (null=True, blank=True) — None = unlimited
   - `auto_enroll` BooleanField (default False) — master switch for auto-enrollment mode (see point 5)
   - `waitlist_enabled` BooleanField (default False) — only meaningful when `auto_enroll=True`
   - `form_keys` JSONField (default=[]) — empty = available on all forms; `["enrollment", "summer"]` = specific forms.
     **Expose only in Django admin for v1. Most schools will not need this.**
   - `created_at`, `updated_at` (auto timestamps)

2. **`School.program_field_key`** CharField (blank=True, default="") — names which YAML field key
   is the "program selector" for this school (e.g. `"dance_style"`, `"interested_in"`).
   When set, the form renderer replaces YAML options for that field with active `SchoolProgram` records.

3. **Form rendering rule — no heuristics, no silent fallback:**
   ```python
   if field.key == school.program_field_key:
       programs = SchoolProgram.objects.filter(school=school, is_active=True).order_by("display_order", "name")
       if programs.exists():
           field["options"] = [{"value": p.code, "label": p.name} for p in programs]
       else:
           # No active programs — explicit product behavior (not a silent degradation):
           # 1. School admin /programs/ page shows a warning banner.
           # 2. Public form renders the field with a single disabled option:
           #    "No programs currently available — contact the school"
           # 3. If the field is required, form submission is blocked (validation error).
           # YAML options for this field are NOT used as fallback. Ops must fix the data.
           field["options"] = []
           field["no_programs_warning"] = True
   else:
       # YAML options used as-is
   ```
   No silent YAML fallback once `program_field_key` is set. Ops must be notified and add active programs.

4. **`Submission.program`** ForeignKey(SchoolProgram, null=True, blank=True, on_delete=SET_NULL):
   - Set at submission time by matching `Submission.data[program_field_key]` → `SchoolProgram.code`
   - JSON field (`Submission.data`) remains unchanged as historical display snapshot
   - FK used for all capacity logic, reporting, and rename-safe queries
   - No FK = school hasn't migrated to DB programs yet (YAML path still works)

5. **Auto-enrollment at submission time** (concurrency-safe):

   Waitlisting is an extension of auto-enroll mode, not a standalone setting. Explicit behavior matrix:

   | `auto_enroll` | slots available | `waitlist_enabled` | result |
   |---|---|---|---|
   | True | Yes | any | `STATUS_ENROLLED` |
   | True | No | True | `STATUS_WAITLISTED` |
   | True | No | False | `STATUS_NEW` |
   | False | any | any | `STATUS_NEW` |

   `STATUS_WAITLISTED` must be a named constant (see below), not the string `"Waitlisted"`.

   ```python
   with transaction.atomic():
       program = SchoolProgram.objects.select_for_update().get(school=school, code=program_code)
       enrolled_count = Submission.objects.filter(
           school=school, program=program, status=STATUS_ENROLLED
       ).count()
       slots_available = program.capacity is None or enrolled_count < program.capacity
       if program.auto_enroll and slots_available:
           submission.status = STATUS_ENROLLED
       elif program.auto_enroll and not slots_available and program.waitlist_enabled:
           submission.status = STATUS_WAITLISTED
       else:
           submission.status = STATUS_NEW
   ```
   `select_for_update()` prevents two concurrent submissions both seeing "1 slot left" and both getting enrolled.

   **Status constant rule**: `STATUS_WAITLISTED` must come from a named constant in `core/models.py`
   (alongside existing `STATUS_ENROLLED`, `STATUS_NEW`, etc.), not from a hardcoded string anywhere
   in the codebase. If the school uses custom statuses, `STATUS_WAITLISTED` should be derivable from
   YAML config (e.g. `waitlist.status` key) — see Waitlist Management spec.

6. **Admin UI** (school admin at `/schools/<slug>/admin/programs/`):
   - Program list: name, code, capacity (or "Unlimited"), enrolled count, waitlist count, active badge, ↑↓ reorder
   - Add program form: name, code (auto-slugified, editable), capacity, auto_enroll, waitlist_enabled
   - Edit program: code field locked (read-only + tooltip) once any submission references it; all other fields editable;
     capacity decrease shows warning if current enrolled > new cap
   - Deactivate: if submissions exist, deactivate only (no DELETE); if no submissions, allow hard delete
   - Warning banner at top of list page when `program_field_key` is set but no active programs exist
   - `form_keys` not exposed in school admin UI for v1 (Django admin only)
   - URL names: `school_programs_list`, `school_program_create`, `school_program_edit`, `school_program_deactivate`

7. **Migration command** (for onboarding existing schools):
   ```
   python manage.py seed_school_programs_from_yaml --school <slug> [--backfill-submissions]
   ```
   Without `--backfill-submissions`: creates `SchoolProgram` records + sets `school.program_field_key`. Idempotent.

   With `--backfill-submissions`: also walks existing `Submission` rows and sets `submission.program` FK:
   - Match priority: exact `code` match → normalized name match
   - Only set FK when exactly one program matches (skip ambiguous)
   - Log a summary: matched N, skipped M (ambiguous), skipped K (no match)
   - Never overwrite an already-set FK

   Idempotent — safe to re-run. Always logs a summary of created / skipped / backfilled rows.

**Audit requirements — every program state change must be logged (no "I don't know when this changed" gaps):**

| Event | `extra["name"]` | Key fields logged |
|-------|----------------|-------------------|
| Program created | `program_created` | code, name, capacity, auto_enroll, waitlist_enabled |
| Program edited | `program_edited` | changed fields with old/new values |
| Program deactivated | `program_deactivated` | code, name, reason (has_submissions bool) |
| Capacity changed | `program_capacity_changed` | old_capacity, new_capacity, current_enrolled |
| auto_enroll toggled | `program_auto_enroll_changed` | old, new |
| Submission auto-enrolled | `auto_enrolled` | program_code, enrolled_count, capacity |
| Submission auto-waitlisted | `auto_waitlisted` | program_code, enrolled_count, capacity |

**DB changes**:
- New model `SchoolProgram` + migration
- `School.program_field_key` CharField + migration
- `Submission.program` ForeignKey + migration (nullable — existing rows have NULL FK until backfill command is run)

**Files touched**:
- `core/models.py` — `SchoolProgram`, `School.program_field_key`, `Submission.program` FK
- `core/migrations/` — 3 migrations (or 1 combined)
- `core/services/programs.py` (new) — `get_program_options`, `resolve_submission_program`,
  `apply_auto_enrollment`, `get_programs_summary`
- `core/views_school_submissions.py` — `apply_auto_enrollment` called on new submission save;
  form rendering uses `get_program_options` when `program_field_key` is set
- `core/views_school_programs.py` (new) — list, create, edit, deactivate views
- `core/urls.py` — 4 new program admin URLs
- `templates/school_admin/programs.html` (new) — program list + reorder + enrolled/waitlist counts
- `templates/school_admin/program_form.html` (new) — create/edit form
- `templates/school_admin/base.html` — "Programs" nav link (gated on `program_field_key` set)
- `core/admin/` — Django admin registration for `SchoolProgram`
- `core/management/commands/seed_school_programs_from_yaml.py` (new)
- `configs/settings.py` — no changes needed

**Out of scope (v1)**:
- `starts_on` / `ends_on` program date range (add later)
- Per-program description or rich text
- Public-facing "available programs" page
- Drag-and-drop reordering (up/down buttons for v1)
- Per-program fee amounts (that's Phase 18 scope)
- Automatic capacity decrease notifications to waitlisted families
- `form_keys` exposed in school admin UI (Django admin only for v1)

**Tests** (`core/tests/test_programs.py`, ~22 tests):
- `test_program_options_from_db_when_field_key_set`
- `test_program_options_from_yaml_when_no_field_key`
- `test_no_active_programs_renders_disabled_option_and_blocks_required_field`
- `test_inactive_program_hidden_from_form_but_existing_submission_still_displays`
- `test_auto_enroll_on_submission_when_slots_available`
- `test_auto_waitlist_on_submission_when_at_capacity_and_waitlist_enabled`
- `test_auto_enroll_status_new_when_auto_enroll_off` — auto_enroll=False must NOT waitlist
- `test_auto_waitlist_does_not_trigger_when_auto_enroll_false` — explicit: full program + auto_enroll=False → New
- `test_concurrent_submissions_only_one_auto_enrolled` (select_for_update race condition)
- `test_submission_program_fk_set_on_save`
- `test_admin_create_program`
- `test_admin_edit_program_logs_audit`
- `test_program_code_cannot_change_when_submissions_exist`
- `test_program_code_can_change_when_no_submissions`
- `test_admin_deactivate_program_with_submissions`
- `test_admin_deactivate_program_without_submissions_hard_deletes`
- `test_seed_command_creates_programs_from_yaml`
- `test_seed_command_idempotent`
- `test_backfill_matches_code_then_normalized_name`
- `test_backfill_skips_ambiguous_matches`
- `test_program_list_shows_enrolled_count`
- `test_capacity_change_audit_logged_with_old_new_values`

---

### Phase 18 — Application Fees (Stripe, school-direct)

**Goal**: Schools can optionally charge an application fee, paid by the parent at submission time.
Money goes directly to the school's own Stripe account. Platform never touches the funds.

**Key design decisions**:
- Payment is a **platform-injected step**, not a YAML form field. Configured in YAML as metadata only.
- Stripe keys (publishable + secret) stored on `School` model in DB — never in YAML.
- Fee is charged **at submission time**: applicant fills out form → clicks Submit → payment page → on success, Submission is created. Failed payment = no Submission.
- Synchronous PaymentIntent confirm (no webhook needed for this flow — school's own account, real-time response).
- Refunds are manual (school handles via their own Stripe dashboard).

**YAML config** (per school, metadata only — no keys in YAML):
```yaml
application_fee:
  enabled: true
  amount: 75           # USD, whole dollars only
  description: "Non-refundable application fee"
```

**DB changes** (new fields on `School` model, migration required):
- `app_fee_stripe_public_key` CharField (blank=True)
- `app_fee_stripe_secret_key` CharField (blank=True) — store encrypted or treat as sensitive
- Fee amount + enabled flag live in YAML; keys live in DB to keep secrets out of config files

**Files touched**:
- `core/models.py` — 2 new School fields + migration
- `core/services/config_loader.py` — parse `application_fee` block
- `core/views_public.py` — `apply_view` checks for fee; new `apply_payment_view` (GET+POST)
- `core/urls.py` — new URL `apply_payment` at `/schools/<slug>/apply/payment/`
- `templates/apply_payment.html` — Stripe Elements card form (new template)
- `templates/apply_form.html` — "Continue to payment" instead of "Submit" when fee enabled
- `core/services/billing_stripe.py` — new helper `create_application_fee_intent(school, amount_cents)`
- `core/models.py` — `Submission.payment_intent_id` (CharField blank=True), `Submission.payment_status` (CharField: `""`, `"paid"`, `"waived"`)
- `templates/school_admin/submission_detail.html` — payment status badge
- `templates/school_admin/submissions.html` — payment status column (optional, gated on school having fees)
- `templates/ops/school_detail.html` — show fee config fields (read-only display + link to edit)
- `core/forms_ops.py` — `OpsSchoolEditForm` gets Stripe key fields (write-only password inputs)

**Out of scope**:
- Refund UI (use school's Stripe dashboard)
- Fee waivers via admin (add later if needed)
- Per-form or per-program fee amounts (single fee per school)
- PayPal or any second payment processor

**Tests** (`core/tests/test_phase18.py`, ~12 tests):
- `test_fee_skipped_when_disabled` — form with no fee goes straight to Submission creation
- `test_fee_page_renders_with_school_pub_key` — payment page receives school's publishable key
- `test_successful_payment_creates_submission` — mock PaymentIntent success → Submission created with `payment_status="paid"`
- `test_failed_payment_no_submission` — mock PaymentIntent failure → no Submission, error shown
- `test_payment_page_requires_prior_form_completion` — cannot load payment page without completing form first (session check)
- `test_submission_detail_shows_paid_badge` — school admin sees payment status
- `test_ops_school_edit_stores_stripe_keys` — ops can save publishable + secret key
- `test_fee_amount_must_be_positive` — YAML validation rejects fee ≤ 0

---

### Phase 19 — Waitlist Management

**Goal**: Schools can place applicants on a per-program waitlist with an ordered position.
Admins can reorder the queue and promote families with a single action.

**Key design decisions**:
- Position is scoped to `(school, program)` — Ballet #1 and Jazz #1 are independent queues.
  Rationale: capacity limits are already per-program, waitlist must match.
- YAML-gated (like capacity): presence of `waitlist.status` activates the feature. No feature flag.
- Position auto-assigned on status transition to the configured waitlist status.
  Reuses `get_program_field_key` / `get_program_value` from `core/services/capacity.py`.
- Promote action: changes status to `promote_to`, clears position, resequences remaining positions
  in that program to close the gap, optionally sends email notification.
- Reorder: up/down swaps positions between adjacent submissions in the same `(school, program)`.
  Two-row UPDATE in a single transaction.
- Status changed away from Waitlisted via dropdown (bypassing Promote) also clears position and logs it.
- Program changed via edit form while submission is Waitlisted: position cleared, new position
  auto-assigned in destination program if it is also the waitlist status.

**YAML config**:
```yaml
waitlist:
  status: "Waitlisted"      # which status value triggers auto-assignment (required)
  promote_to: "In Review"   # status set on promotion (required)
  # notification_template: "waitlist_promoted"  # optional, falls back to default copy
```

**DB changes**:
- `Submission.waitlist_position` IntegerField (null=True, blank=True, db_index=True)
  NULL = not on waitlist. Value = position within `(school, program)` queue.

**Audit log — every position change must be logged (no "I don't know how this happened" gaps)**:

| Event | `extra["name"]` | Key fields logged |
|-------|----------------|-------------------|
| Status → Waitlisted (auto-assign) | `waitlist_position_assigned` | program, position |
| Reorder ↑/↓ | `waitlist_reorder` | program, old_position, new_position (logged on BOTH swapped submissions) |
| Promote | `waitlist_promote` | program, old_position, new_status, email_sent, positions_resequenced |
| Status changed away from Waitlisted (not via Promote) | `waitlist_position_cleared` | program, old_position |
| Bulk status → Waitlisted | `waitlist_position_assigned` | program, position (one entry per submission) |
| Program changed via edit while Waitlisted | `waitlist_position_cleared` + `waitlist_position_assigned` | old_program, new_program, positions |

**Where admins see the waitlist**:
- **Submissions list**: Waitlisted rows show `#N` position badge inline with the status.
- **Dedicated page** `/schools/<slug>/admin/waitlist/`: primary management UI. Program tabs across
  the top (one per program with waitlisted submissions). Each tab: ordered table with position badge,
  student name, submitted date, parent contact, ↑ ↓ reorder buttons, Promote button per row.
- **Submission detail sidebar**: position badge + Promote button when submission is Waitlisted.
- **Nav**: "Waitlist" link appears in school admin sidebar only when ≥1 waitlisted submission exists.

**Files touched**:
- `core/models.py` + migration
- `core/services/waitlist.py` (new) — `get_waitlist_config`, `assign_waitlist_position`,
  `clear_waitlist_position`, `resequence_program_waitlist`, `promote_from_waitlist`
- `core/views_school_submissions.py` — status update views call `assign_waitlist_position` /
  `clear_waitlist_position`; new `school_waitlist_view` GET; new `school_waitlist_promote_view` POST;
  new `school_waitlist_reorder_view` POST; edit view handles program-change-while-waitlisted
- `core/urls.py` — `school_waitlist`, `school_waitlist_promote`, `school_waitlist_reorder`
- `templates/school_admin/waitlist.html` — program tabs, ordered table, ↑/↓, Promote
- `templates/school_admin/submissions.html` — `#N` badge on Waitlisted rows
- `templates/school_admin/submission_detail.html` — position badge + Promote in sidebar
- `templates/school_admin/base.html` — conditional Waitlist nav link
- `core/services/notifications.py` — `send_waitlist_promotion_notification`

**Out of scope**:
- Drag-and-drop reordering (up/down buttons for v1)
- Auto-promote when enrolled count drops below cap
- Public waitlist position display for families
- Per-form (multi-form school) waitlist — program-level is sufficient

**Tests** (`core/tests/test_waitlist.py`, ~14 tests):
- `test_position_assigned_on_status_change` — per program, sequential
- `test_position_not_assigned_when_waitlist_not_configured`
- `test_position_cleared_on_status_change_away` — dropdown bypass path
- `test_promote_changes_status_clears_position`
- `test_promote_resequences_remaining`
- `test_promote_sends_notification_when_email_enabled`
- `test_promote_audit_log_entry`
- `test_reorder_up_swaps_positions`
- `test_reorder_audit_logged_on_both_submissions`
- `test_bulk_status_assigns_positions`
- `test_program_change_while_waitlisted_clears_and_reassigns`
- `test_waitlist_view_grouped_by_program`
- `test_waitlist_nav_link_hidden_when_none`
- `test_position_scoped_per_program` — Ballet #1 and Jazz #1 are independent

---

### Phase 20 — Parent Portal / Application Status Page

**Goal**: Families can check the status of their application without calling the school.
Public URL, no login, lookup by email address + application ID (public_id).

**Key design decisions**:
- Auth: email + public_id (not DOB — schools don't always collect it). Both must match.
- Shows: current status, school-configured public message per status (optional), submitted date.
- Does NOT show: internal notes, audit log, staff comments.
- School can configure custom status messages in YAML per status value.
- Rate-limited to prevent enumeration attacks.

**YAML config** (optional):
```yaml
status_page:
  enabled: true
  messages:
    New: "We've received your application and will be in touch soon."
    Enrolled: "Congratulations! Your child has been enrolled."
    Waitlisted: "Your child is on our waitlist. We'll contact you if a spot opens."
```

**Files touched**:
- `core/views_public.py` — `application_status_view` (GET: lookup form; POST: show result)
- `core/urls.py` — `application_status` at `/schools/<slug>/status/`
- `templates/application_status.html` — lookup form + result card
- `core/services/config_loader.py` — parse `status_page` block
- Rate limiting: `@ratelimit(key="ip", rate="10/m")` on the view
- `School.status_page_enabled` — or gate purely on YAML flag

**Out of scope**:
- Login / account creation for parents
- Document upload or messaging from parent portal
- Mobile push notifications

**Tests** (`core/tests/test_phase20.py`, ~8 tests):
- `test_status_lookup_correct_credentials`
- `test_status_lookup_wrong_email`
- `test_status_lookup_wrong_public_id`
- `test_status_page_disabled_returns_404`
- `test_custom_yaml_message_shown`
- `test_rate_limit_enforced`

---

### Phase 21 — Multi-Child Household

**Goal**: One parent email can have multiple children applying to the same school.
Currently blocked by unique constraint on (school, normalized_email).

**Key design decisions**:
- **Approach**: Add `child_name` to `Lead` and change unique constraint from
  `(school, normalized_email)` to `(school, normalized_email, child_name)`.
  Simpler than a Household model; avoids a large data migration.
- Lead capture form gets an optional "Child's name" field (not YAML-driven — platform level).
- Admin lead list groups by email visually (CSS styling only, no DB grouping query).
- Dedup logic: when a new lead arrives, match on school + email + child_name. If child_name
  is blank, fall back to old dedup behavior (one lead per email).

**DB changes**:
- `Lead.child_name` CharField(max_length=200, blank=True, default="")
- Remove `unique_together = [("school", "normalized_email")]`
- Add `unique_together = [("school", "normalized_email", "child_name")]`
- Migration: data migration to backfill `child_name=""` for all existing leads (already the default, so no-op)

**Files touched**:
- `core/models.py` + migration
- `core/views_public.py` — lead capture form picks up `child_name`
- `core/views_school_leads.py` — lead create form adds child_name field
- `core/views_school_common.py` — `_build_lead_row` includes child_name in display
- `templates/lead_form.html` — child name field on public capture form
- `templates/school_admin/leads.html` — child name column
- `templates/school_admin/lead_detail.html` — child name in header
- `core/services/admin_lead_yaml.py` — export includes child_name column

**Out of scope**:
- Household model (overkill for v1)
- Linking siblings in the UI
- Per-child application tracking on the parent portal

**Tests** (`core/tests/test_phase21.py`, ~8 tests):
- `test_two_children_same_email_same_school_allowed`
- `test_duplicate_child_name_same_email_rejected`
- `test_blank_child_name_dedup_preserved`
- `test_lead_export_includes_child_name`
- `test_leads_list_shows_child_name`

---

### Phase 22 — Referral Source Analytics

**Goal**: School admins can see which referral sources and UTM campaigns bring the most leads
that actually convert to enrolled students. UTM data already captured on `Lead` model.

**Key design decisions**:
- No new models — all data already exists (`Lead.source`, `Lead.utm_source`,
  `Lead.utm_medium`, `Lead.utm_campaign`, `Lead.converted_submission`).
- Add a new section to the existing `school_reports_view` (not a separate page).
- Two tables: (1) by `source` field, (2) by `utm_campaign` (only when UTM data exists).
- Columns: Source, Total Leads, Converted, Conversion Rate, Enrolled (of converted).

**Files touched**:
- `core/views_school_dashboard.py` — `school_reports_view` adds `source_breakdown` + `utm_breakdown` context dicts
- `templates/reports.html` — two new table sections (gated on leads_enabled)
- No new URLs, no new models, no migrations

**Out of scope**:
- UTM link builder tool
- Attribution across multiple touches (first-touch only)
- Campaign cost / ROI tracking

**Tests** (`core/tests/test_phase22.py`, ~6 tests):
- `test_source_breakdown_correct_counts`
- `test_source_breakdown_conversion_rate`
- `test_utm_breakdown_hidden_when_no_utm_data`
- `test_source_breakdown_hidden_when_leads_disabled`

---

### Phase 23 — Application Deadline Enforcement (low priority)

**Goal**: Schools can set open/close dates for their application form. The form shows a
countdown when approaching the deadline and a custom message when closed.

**Key design decisions**:
- Configured entirely in YAML — no DB fields needed.
- `apply_view` checks date range before rendering form.
- Closed form shows a static page (not a 404).

**YAML config**:
```yaml
form:
  open_date: "2025-09-01"    # optional
  close_date: "2025-12-01"   # optional
  closed_message: "Applications for the 2025–26 school year are now closed."
```

**Files touched**:
- `core/services/config_loader.py` — parse open/close dates
- `core/views_public.py` — `apply_view` date check + redirect to closed page
- `templates/apply_closed.html` — new template (simple message + contact link)
- `core/urls.py` — no new URL needed (reuse apply URL, render different template)

**Out of scope**:
- Per-form deadlines (multi-form schools — add later)
- Auto-reopening
- Deadline enforcement on re-apply / edit

**Tests** (`core/tests/test_phase23.py`, ~4 tests):
- `test_form_accessible_within_date_range`
- `test_form_closed_before_open_date`
- `test_form_closed_after_close_date`
- `test_closed_message_from_yaml`

---

## Known Technical Debt

| Item | Priority | Notes |
|------|----------|-------|
| `core/views.py` split | Done | 5 focused files + re-export facade |
| nth-child CSS removed | Done | Replaced with semantic CSS classes |
| Django admin login as school admin login | Fixed in Ops Phase 1 | |
| `CSRF_COOKIE_SAMESITE = "None"` | Low | Required for iframe embed; document why |
| Lead unique constraint | Phase 21 | Will relax to allow multi-child households |

---

## Superadmin Features Reference

### Via Django Admin (`/admin/`)
- School model: full CRUD, plan, feature_flags JSON, all Stripe fields, list filter by plan/status
- SchoolAdminMembership: full CRUD
- Users: create/edit/delete, is_staff/is_superuser flags, password
- Submissions: cross-school access
- Leads: cross-school access
- DraftSubmissions: full access (school admins have none)
- AdminPreferences: read-only debug view
- Reports hub: school picker

### Via School Admin UI (`/schools/<slug>/admin/`) — superuser extras
- Can access any school (not just their own)
- Can access inactive school dashboards
- "Django admin ↗" links on submission/lead detail pages
- Reports always available (ignores `reports_enabled` flag)
- CSV export always available (ignores `csv_export_enabled` flag)
- Leads always available (ignores `leads_enabled` flag)
- Conversion data always shown
- Trial banner suppressed
- Trial end date override form on Settings page

### Via Ops Portal (`/ops/`) — Phase 1 target
- All of the above, in a modern UI, without needing Django admin

---

## Test Standards (non-negotiable)

- **90% code coverage** on all new code
- Tests must be **meaningful** — test behavior, not implementation. Assert real outcomes
  (DB state, response content, redirect URLs), not mock call counts.
- Every new view gets at minimum: auth guard test, happy-path test, error-path test
- Manual QA checklist must be completed before marking any phase done
- Run full suite after every commit: `pytest -q --tb=short` — must stay green

## Test Suite

Run with: `pytest -q --tb=short`

Current baseline: **854 passing, 0 failing**

Test files by feature:
- `test_lead_workflow.py` — Phase 2 (25 tests)
- `test_lead_start_enrollment.py` — Phase 3 (4 tests)
- `test_detail_pages.py` — Phase 4 (20 tests)
- `test_lead_update.py` / `test_lead_ux.py` — Phase 5 (14 tests)
- `test_submission_update.py` — Phase 6 (5 tests)
- `test_export_csv.py` — Phase 7/8 (4 tests)
- `test_create_edit.py` — Phase 10 (10 tests)
- `test_inbox_workflow.py` — Phase 11 (13 tests) [also named test_phase11.py]
- `test_phase12.py` — Phase 12 (8 tests)
- `test_phase14.py` — Phase 14 (10 tests)
- `test_school_admin.py` — general school admin
- `test_submission_status_workflow.py` — status workflow
- `test_polish.py` — Phase 9 (5 tests)
- `test_ops_phase1.py` — Phase 1 ops portal (TBD, 8 tests planned)
