Student Enrollment Portal (Multi-School Application MVP)

Overview

Student Enrollment Portal is a multi-tenant web application that allows multiple real-world schools and programs to collect applications online using YAML-defined forms, while sharing a single backend, database, and codebase.

This MVP is designed for small institutions (dance studios, cultural programs, academies, etc.) that currently rely on PDFs, email, or paper forms.

Key Goals
	•	One backend, many schools
	•	No custom code per school
	•	Forms defined in YAML (easy to change)
	•	Simple, school-scoped admin access
	•	Built-in reporting and CSV exports
	•	Production-ready architecture

⸻

How It Works (Mental Model)
	1.	Each school is identified by a school_slug
	2.	A YAML file at:

configs/schools/<school_slug>.yaml

defines:
	•	School metadata
	•	Form sections and fields
	•	Validation rules
	•	Branding defaults

	3.	Visiting:

/schools/<school_slug>/apply

dynamically renders the form

	4.	On submit:
	•	Data is validated
	•	Stored as JSON in PostgreSQL
	5.	Admins log in to view submissions and reports only for their school

⸻

Repository Structure

student_enrollment_portal/
├── config/                # Django settings, URLs, WSGI
├── core/                  # Main app (models, views, admin, services)
│   ├── services/          # YAML loading, validation, helpers
│   ├── migrations/
├── configs/
│   └── schools/           # One YAML file per school
├── templates/
│   ├── admin/             # Admin-only templates (reports hub)
│   └── reports.html       # School reports page
├── static/
│   ├── forms.css          # Shared form styles
│   └── admin/
│       └── reports.css    # Reports styling
├── docs/                  # Discovery notes
├── requirements.txt
├── manage.py
├── .env.example
└── README.md


⸻

Local Setup (Step-by-Step)

These steps assume macOS and no prior Django experience.

1. Clone the repository

git clone <your-repo-url>
cd student_enrollment_portal

2. Create and activate virtual environment

python3 -m venv venv
source venv/bin/activate

3. Install dependencies

pip install -r requirements.txt

Optional: install development/test dependencies

pip install -r requirements-dev.txt

4. Install PostgreSQL (Homebrew)

brew install postgresql@16
brew services start postgresql@16

5. Create database

createdb student_enrollment_portal

6. Environment variables

cp .env.example .env

Edit .env:

DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=True
DATABASE_URL=postgres://<your-username>@localhost:5432/student_enrollment_portal
ALLOWED_HOSTS=localhost,127.0.0.1

7. Run migrations

python manage.py migrate

8. Create superuser

python manage.py createsuperuser

9. Start the server

python manage.py runserver

Visit:
	•	App: http://127.0.0.1:8000/
	•	Admin: http://127.0.0.1:8000/admin/

⸻

Adding a New School (No Code Required)
	1.	Copy an existing YAML file:

configs/schools/example-school.yaml


	2.	Rename it:

configs/schools/my-new-school.yaml


	3.	Update:

school:
  slug: "my-new-school"
  display_name: "My New School"


	4.	Restart the server
	5.	Visit:

/schools/my-new-school/apply



⸻

Important: Schools in Admin vs YAML Configs

This system intentionally separates configuration from activation.
	•	YAML files define:
	•	Form fields
	•	Validation
	•	Branding
	•	School records in Admin define:
	•	Which schools are live
	•	Which schools appear in admin
	•	Which schools can have admin users

Adding a YAML file does not automatically create a School record.

How to Activate a School
	1.	Go to /admin/
	2.	Core → Schools → Add
	3.	Enter:
	•	Slug (must match YAML filename)
	•	Display name
	•	Website URL (optional)
	4.	Save

The form becomes live immediately:

/schools/<school_slug>/apply


⸻

Admin (Phase 7): School-Scoped Access

Roles
	•	Superuser
	•	Sees all schools, users, submissions, and reports
	•	School Admin
	•	Sees only their school’s data
	•	Cannot see or edit other schools

Creating a School Admin (Fast Flow)
	1.	Go to /admin/ → Users → Add user
	2.	Enter user info
	3.	Select School (superuser only)
	4.	Save

The system automatically:
	•	Sets is_staff = True
	•	Creates SchoolAdminMembership
	•	Restricts access to that school only

⸻

Submissions Admin

Submissions List

Shows:
	•	Student / Applicant name (best-effort from JSON)
	•	Program (derived from form config)
	•	Created date
	•	School column (superuser only)

Search

Case-insensitive partial search across:
	•	Student / Applicant
	•	Program
	•	School (superuser)

Editing
	•	JSON is formatted for readability
	•	Creating submissions in admin is disabled (to avoid invalid data)

CSV Export

From Submissions:
	•	Select rows
	•	Action → Export selected submissions to CSV

Includes:
	•	created_at
	•	student_name
	•	all discovered JSON fields

⸻

Reporting (Phase 10)

Where to Access
	•	From Admin sidebar → Reports
	•	Or directly:

/schools/<school_slug>/admin/reports



Permissions
	•	School admins → only their school
	•	Superusers → any school

Features
	•	Date range filter: Last 7 / 30 / 90 days
	•	Optional Program filter
	•	Program breakdown (count + %)
	•	Recent submissions table
	•	Student names link directly to admin submission detail
	•	Export filtered report to CSV

CSV Export

Exports only filtered rows with:
	•	created_at
	•	student_name
	•	program
	•	all JSON fields

⸻

Branding (Phase 9)

If branding keys are missing in YAML, defaults are applied automatically.

Supported:

branding:
  logo_url: /static/logos/example.png
  theme:
    primary_color: "#111827"
    accent_color: "#ea580c"
  custom_css: /static/custom.css
  custom_js: /static/custom.js

No code changes required.

⸻

Non-Technical Operations Guide

Edit Form Fields
	1.	Open:

configs/schools/<school_slug>.yaml


	2.	Modify labels, required fields, or options
	3.	Save
	4.	Restart server
	5.	Refresh browser

⸻

Troubleshooting
	•	CSS not updating: Hard refresh (Cmd + Shift + R)
	•	Reports empty: Check date range filter
	•	Can’t see data: Confirm school membership
	•	Program shows “No program selected”: Field left blank in submission

⸻

MVP Limitations & Future Phases

Phase 13 – Testing & Quality
	•	≥90% code coverage
	•	Unit tests for services and models
	•	Integration tests for views
	•	Admin permission tests
	•	UI tests (Playwright/Cypress)
	•	CI-ready test suite

Phase 14 – Leads & Lead Generation
	•	Lead capture forms
	•	Lead → submission conversion tracking
	•	Lead analytics and reporting
	•	School-scoped lead admin UI
	•	Export + marketing integrations

⸻

License

Internal MVP / Demo Use
