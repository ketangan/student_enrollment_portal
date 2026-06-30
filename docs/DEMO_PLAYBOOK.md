# School Demo Playbook

**Trigger phrase**: "Build a demo for [School Name] at [URL]"

This document is a complete, self-contained recipe for building a school demo from a website URL.
Read it top to bottom before starting. Every step is mandatory.

---

## What you're building

Five demo page variants that show how the enrollment form integrates into a school's real website:

| Template file | Demo slug route | What it shows |
|---|---|---|
| `dedicated-page.html` | `/dedicated-page/` | Full standalone page with form embedded below content |
| `bottom-section.html` | `/bottom-section/` | Program detail page with form at the bottom |
| `modal.html` | `/modal/` | Content page with "Register" button that opens a modal |
| `link-out.html` | `/link-out/` | Content page with a CTA block that links out to the form |
| `standalone-form.html` | `/standalone-form/` | The bare form, no surrounding content |

Plus: a YAML config, school CSS, seed script, and DB-backed programs.

---

## Inputs needed

- School name (e.g. "Beverly Hills Gymnastics Center")
- School website URL (e.g. `https://www.beverlyhillsgymnastics.org/`)
- School slug (lowercase, hyphenated, e.g. `beverly-hills-gymnastics`)
- Demo slug (usually `<short-name>-demo`, e.g. `bhg-demo`)

Derived automatically:
- Admin username: `<slug without hyphens>_admin` (e.g. `bhg_admin`)
- Admin password: `<PascalAbbrev>Admin@123` (e.g. `BhgAdmin@123`)
- Template dir: `templates/demo/<abbrev>/` (e.g. `templates/demo/bhg/`)
- Seed command: `seed_<slug_underscored>_demo` (e.g. `seed_bhg_demo`)

---

## Step 1 — Scrape the school website

**Do not guess colors.** Load the school's website and extract exact values.

### What to collect

**Brand colors** — inspect CSS on their homepage and programs/classes page:
- Primary color (buttons, headings, accents)
- Background color
- Text color
- Border/divider color
- Top bar / footer background (often darker than primary)

**Typography**:
- Body font (usually a sans-serif like Montserrat, Open Sans, etc.)
- Heading font (often a serif like Cormorant Garamond, Playfair Display, etc.)
- Google Fonts import URL if identifiable

**Content**:
- Logo URL (look for `<img>` in header, or SVG src)
- School tagline / sub-header text
- Navigation items and their URLs
- Address
- Phone number
- Email
- Business hours
- Programs offered: name, age range, open enrollment vs placement required

**Critical anti-pattern to avoid**: The BHG demo was initially built with `#111111` (black) hero banners
because that was the footer/top-bar color. The programs page hero was `#7F0200` (dark red). Always check
the actual programs/classes page, not just the homepage.

### Verification checklist (run before writing any code)
- [ ] Pulled primary color from the programs/classes page specifically (not homepage footer)
- [ ] Verified heading font by inspecting `<h1>` or `<h2>` elements directly
- [ ] Have the exact logo URL (test that it loads in a browser)
- [ ] Have phone, email, and hours for the footer

---

## Step 2 — Create the YAML config

**File**: `configs/schools/<slug>.yaml`

Copy `configs/schools/beverly-hills-gymnastics.yaml` as a starting template.

Required sections:
- `school` — slug, display_name, website_url, source_url
- `branding` — logo_url, custom_css path, theme (all 9 color/font keys)
- `program_field_key` — top-level key that specifies which form field holds the program value
- `form` — title, description, submit_button_text, sections with fields
- `success` — title, message, next_steps list, contact block, notifications
- `admin` — submission_statuses list, default_submission_status, submission_workflow, lead_workflow
- `leads` — form_title, form_description, cta_text, success_message
- `capacity` — waitlist_message, excluded_statuses, programs dict (program_code → max_int)

**Key rules**:
- `program_field_key` must match the `key:` of the select field that captures program choice
- Capacity `programs` keys must match the `SchoolProgram.code` values in the seed script
- YAML field keys are immutable after creation — choose them carefully

---

## Step 3 — Create the school CSS

**File**: `static/schools/<slug>.css`

Copy `static/schools/beverly-hills-gymnastics.css` as a starting template.

At minimum, override:
- `@import` the school's Google Fonts
- Form label font-size, font-weight
- Button background, hover state
- Any school-specific font overrides (`.enroll-form .enroll-btn { background: <primary>; }`)

Remove `text-transform: uppercase` from labels unless the school actually uses that on their site.

---

## Step 4 — Create the five demo templates

**Directory**: `templates/demo/<abbrev>/`

### Structure for each template

All five share the same header/footer shell:
- Top bar (phone, email, hours) — background matches school's dark bar color (often `#111111`)
- Sticky site header (white/light, logo + nav)
- Page hero — background is the school's **primary** color (not footer color)
- Content section (varies per template — see below)
- Site footer — background matches top bar

### Template-specific content

**`dedicated-page.html`** — Full page with hero + description + embedded form
- Hero: school's program page title (e.g. "Class Registration")
- Below hero: brief intro paragraph
- Then: `<div class="form-section">` with `<iframe src="{{ embed_form_url }}">` at fixed `700px` height
- iframeResize script at bottom to auto-size on content change

**`bottom-section.html`** — Program detail page with form below
- Hero: specific program name (pick the most popular one for demo purposes)
- Breadcrumb: Home > Classes > [Program Name]
- Program detail grid (4 cards: Skills Covered, Class Format, Schedule, What to Bring)
- Placement evaluation note (amber callout) if applicable
- `<hr>` divider
- Form section at bottom

**`modal.html`** — Content page with modal trigger
- Hero: general programs/classes page title
- Intro paragraph only (no program grid — let the school's existing content be the source of truth)
- CTA row: "Register for a Class →" button + "(Opens a quick registration form)" note
- Modal overlay with `<iframe data-src="{{ embed_form_url }}">` (lazy-loaded on open)
- JS: open/close on button, backdrop click, Escape key; close on `formSubmitted` postMessage

**`link-out.html`** — Content page with CTA block that links out
- Hero: general programs/classes page title
- Intro paragraph only (no program grid)
- `<div class="register-block">` with school's primary color background
  - Heading: "Ready to Register?"
  - Sub-text: follow-up timing
  - `<a href="{{ form_url }}" target="_blank">` button (white on primary, hover to dark)

**`standalone-form.html`** — Bare form, no surrounding site chrome
- Minimal header (logo + school name only, no nav)
- Centered form section with title and description
- Same iframe + iframeResize pattern

### Color verification (MANDATORY before committing)

For each template:
1. Load the school's website in a browser
2. Open devtools → computed styles on their `<h1>` banner/hero elements
3. Compare exact hex value against your `.page-hero { background: }` value
4. Do the same for buttons, links, and any colored text

**Do not rely on screenshot comparison** — it's how the BHG black/red mismatch happened.
Extract the actual CSS values from the DOM.

---

## Step 5 — Register in DEMO_REGISTRY

**File**: `core/views_demo.py`

Add an entry to `DEMO_REGISTRY`:

```python
"<demo-slug>": {
    "school_slug": "<school-slug>",
    "template_dir": "demo/<abbrev>",
    "demos": ["dedicated-page", "modal", "bottom-section", "link-out", "standalone-form"],
},
```

---

## Step 6 — Create the seed script

**File**: `core/management/commands/seed_<slug_underscored>_demo.py`

Copy `core/management/commands/seed_bhg_demo.py` as a starting template.

### What the seed script must create

**School**:
- `get_or_create` by slug
- `plan = "trial"`, `is_active = True`
- `trial_started_at = now() - 3 days` (prevents expiry during demo)
- `program_field_key` set to match the form field

**Programs** (`SchoolProgram`):
- One entry per program in the YAML `capacity.programs` block
- Set `auto_enroll = True` for open-enrollment programs (no placement needed)
- Set `waitlist_enabled = True` for programs that can fill up
- Use consistent codes that match the YAML capacity keys

**Admin user**:
- Username: `<slug_abbrev>_admin`
- Password: `<PascalAbbrev>Admin@123`
- `is_staff = True`, `is_superuser = False`
- `SchoolAdminMembership.get_or_create(user, school)`

**Submissions** — spread across 0–60 days to populate reports:

| Period | Purpose |
|---|---|
| 31–60 days ago | Fills the "previous period" comparison column in trend reports |
| 0–30 days ago | Default reports view range |

Status mix per period should include: Enrolled, Waitlisted, Placement Scheduled, In Review, Contacted, Needs Follow Up, New, Archived/Declined.

Include at minimum:
- 5+ Enrolled in current period (for conversion rate to be non-zero)
- 2+ Waitlisted (to show waitlist feature)
- 3+ with Placement Scheduled/Completed (for placement workflow)
- 3+ Needs Follow Up with backdated `next_follow_up_at` in the past (shows overdue badge)
- 5+ New (for the inbox)
- Some with notes (shows the purple dot indicator in list)

**Leads** — 5–8 leads with variety:
- At least one `enrolled` status with `converted_submission` and `converted_at` set (shows conversion flow)
- At least one `placement_scheduled`
- At least one `new` (shows inbox)
- At least one `contacted` with `next_follow_up_at` in the past (shows overdue)
- Mix of sources: `google`, `referral`, `social`, `drove_by`, `website`

**Idempotency**:
- Skip submission re-seeding if >= 5 exist unless `--force` passed
- Skip lead re-seeding if >= 3 exist unless `--force` passed
- All creates use `get_or_create` for school/programs/user

---

## Step 7 — Run seed locally

```bash
python manage.py seed_<slug>_demo
```

Verify the output shows all programs created and submission/lead counts.

---

## Step 8 — Manual QA checklist

**All 5 demo pages** at `http://127.0.0.1:8001/demo/<demo-slug>/`:

- [ ] Page loads without 404 or 500
- [ ] Hero banner matches the school's primary color (verify with devtools)
- [ ] Logo loads and is visible
- [ ] Nav links are correct
- [ ] Footer has correct phone, email, address
- [ ] Form embeds and is scrollable
- [ ] iframeResize works (form grows taller as sections expand)
- [ ] Modal opens and closes (for modal.html)
- [ ] Link-out button opens correct URL in new tab (for link-out.html)
- [ ] Mobile layout: hide nav/top-bar, stack columns (resize browser to 375px width)

**Admin portal** at `http://127.0.0.1:8001/schools/<slug>/admin/`:

- [ ] Login with demo credentials works
- [ ] Dashboard shows non-zero KPI cards
- [ ] Submissions list shows multiple statuses with correct program names (not "program:code" raw strings)
- [ ] Reports page shows Program Breakdown chart with data
- [ ] Reports page shows trend data (requires 31+ day range of seed data)
- [ ] Leads list shows leads with correct statuses
- [ ] One lead shows "Converted" status with linked submission

**Submit a real form**:
- [ ] Go to `/schools/<slug>/apply/` and submit a test registration
- [ ] Confirm it appears in the Submissions list with correct program display name
- [ ] If program has `waitlist_enabled = True` and is over capacity: verify waitlist message on success page

---

## Step 9 — Commit and push

```bash
git add configs/schools/<slug>.yaml static/schools/<slug>.css \
        templates/demo/<abbrev>/ core/views_demo.py \
        core/management/commands/seed_<slug>_demo.py
git commit -m "feat(demo): add <School Name> demo"
git push origin main
```

---

## Step 10 — Render deployment

After the push deploys:

```bash
# In the Render shell for your service:
python manage.py seed_<slug>_demo
```

---

## Outputs to hand off

After completing all steps, provide these to the prospect:

```
Demo pages:   https://<your-render-domain>/demo/<demo-slug>/
Admin portal: https://<your-render-domain>/schools/<slug>/admin/
Username:     <slug_abbrev>_admin
Password:     <PascalAbbrev>Admin@123
Form (direct): https://<your-render-domain>/schools/<slug>/apply/
```

---

## Reference: existing demos

| Demo slug | School slug | Abbrev | Seed command |
|---|---|---|---|
| `ymla-demo` | `ymla` | `ymla` | `seed_ymla_demo` |
| `duc-learning-center-demo` | `duc-learning-center` | `duc` | `seed_duc_demo` |
| `bhg-demo` | `beverly-hills-gymnastics` | `bhg` | `seed_bhg_demo` |

Use these seed scripts as working references when building a new one.
