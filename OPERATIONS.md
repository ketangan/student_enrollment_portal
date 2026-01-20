	•	README.md → Sales / Demo / Product overview
	•	OPERATIONS.md → Internal + operator runbook

(Internal / Admin / Technical Runbook)

# Student Enrollment Portal  
## Operations & Administration Guide

This document is for:
- Platform operators
- Developers
- Admin users onboarding schools
- Internal maintainers

---

## Core Concepts

### Schools Are Defined in Two Places

This is intentional.

#### YAML Config
Defines:
- Form structure
- Validation rules
- Branding

Location:

configs/schools/<school_slug>.yaml

#### Admin (Database)
Defines:
- Which schools are active
- Which schools appear in admin
- Which schools can have admins

A YAML file alone does **not** activate a school.

---

## Activating a New School

### Step 1: Add YAML
1. Copy an existing YAML file
2. Rename to match the school slug
3. Update:

```yaml
school:
  slug: "my-school"
  display_name: "My School"

Step 2: Activate in Admin
	1.	Go to /admin/
	2.	Core → Schools → Add
	3.	Enter:
	•	Slug (must match YAML filename)
	•	Display name
	4.	Save

The form becomes live immediately at:

/schools/my-school/apply


⸻

Admin Roles

Superuser
	•	Access to all schools
	•	Manage users and memberships
	•	View all submissions and reports

School Admin
	•	Access limited to one school
	•	View submissions and reports
	•	Cannot see other schools

⸻

Creating School Admin Users
	1.	/admin/ → Users → Add user
	2.	Fill in:
	•	Username
	•	Email (recommended)
	3.	Select School (superuser only)
	4.	Save

System automatically:
	•	Sets is_staff = True
	•	Creates SchoolAdminMembership
	•	Scopes access to that school

Password Setup (Current MVP)
	•	Admin can log in
	•	User sets or changes password via:
	•	Admin → Change password

Email-based invites are planned post-MVP.

⸻

Submissions Admin

What You See
	•	Student / applicant name (derived from JSON)
	•	Program (derived from form config)
	•	Timestamp
	•	School column (superuser only)

Search
	•	Student name
	•	Program
	•	School (superuser)

CSV Export
	•	Select rows
	•	Action → Export selected submissions

Includes:
	•	created_at
	•	student_name
	•	all JSON fields

⸻

Reporting

Access
	•	Admin sidebar → Reports
	•	Or:

/schools/<school_slug>/admin/reports

Features
	•	Date range filter (7 / 30 / 90 days)
	•	Program breakdown with charts
	•	Recent submissions list
	•	CSV export (filtered)

Permissions
	•	School admins → own school only
	•	Superusers → any school

⸻

Branding

Branding lives in YAML and is optional.

branding:
  logo_url: /static/logos/example.png
  theme:
    primary_color: "#111827"
    accent_color: "#ea580c"

Defaults are applied automatically if missing.

⸻

Testing

Unit & Integration Tests

python -m pytest -q

With coverage:

python -m pytest --cov=core --cov-report=term-missing

Target coverage: ≥ 90%

⸻

End-to-End (Playwright)

Requirements:
	•	Node.js + npm
	•	Django server running locally

npx playwright test

Credentials are supplied via environment variables (recommended) or .env.e2e.

⸻

CI
	•	GitHub Actions runs:
	•	Dependency install
	•	Migrations
	•	pytest test suite

Render deployments do not run tests automatically — CI protects main.

⸻

Known MVP Limitations
	•	No email backend (password reset, invites)
	•	Single form per school
	•	No lead capture
	•	No file uploads

⸻

Planned Enhancements

Phase 13 – Testing & Quality
	•	Harden UI tests
	•	Permission regression coverage

Phase 14 – Leads
	•	Lead capture forms
	•	Conversion tracking
	•	Lead reporting

Post-MVP
	•	SMTP/email integration
	•	Admin invites
	•	E-signatures
	•	Multi-form schools

⸻

Support

If something breaks:
	1.	Check logs
	2.	Verify school slug matches YAML
	3.	Confirm SchoolAdminMembership
	4.	Run tests locally

This document should stay updated as the platform evolves.

---

If you want next, we can:
- Add **screenshots placeholders** to README  
- Create a **pitch-deck version** of README  
- Add **“School Owner Quick Start”** (1-page doc)  
- Add diagrams (data flow, permissions)
