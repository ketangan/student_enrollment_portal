Yes — you should commit this stage. You just crossed a big “stable MVP milestone” (Phase 8 done + Phase 9 theming + Phase 10 admin improvements). A clean commit now makes it easy to roll back and keeps Render deploys predictable.

Below is an updated README.md that keeps your existing tone/structure, adds what’s missing from a non-technical perspective, and updates a couple sections to reflect how the system actually works now (especially Admin user creation + branding/theme + custom css/js + reporting + troubleshooting).

⸻

Student Enrollment Portal (Multi-School Application MVP)

Overview

Student Enrollment Portal is a multi-tenant web application that allows multiple real-world schools/programs to collect applications online using YAML-defined forms, while sharing a single backend, database, and codebase.

This MVP is designed for small institutions (dance studios, cultural programs, academies, etc.) that currently rely on PDF or email-based applications.

Key goals:
	•	One backend, many schools
	•	No custom code per school
	•	Forms defined in YAML (easy to change)
	•	Simple admin access per school
	•	Production-ready architecture

⸻

How It Works (Mental Model)
	1.	Each school is identified by a school_slug
	2.	A YAML file at configs/schools/<school_slug>.yaml defines:
	•	School metadata
	•	Branding + theme
	•	Form sections and fields
	•	Optional reporting config (Phase 10)
	3.	Visiting /schools/<school_slug>/apply:
	•	Loads the YAML config
	•	Dynamically renders the form
	4.	On submit:
	•	Data is validated
	•	Stored as JSON in PostgreSQL
	5.	Admins log in to view submissions for their school
	•	Superuser sees all schools
	•	School admins only see their own school data

⸻

Repository Structure

student_enrollment_portal/
├── config/                # Django settings, URLs, WSGI
├── core/                  # Main app (models, views, services, admin)
│   ├── services/          # YAML loading, validation, helpers
│   ├── templatetags/      # Template helpers
│   └── migrations/
├── configs/
│   └── schools/           # One YAML file per school
├── templates/             # Shared HTML templates
├── static/
│   ├── forms.css          # Shared base styling
│   ├── schools/           # Optional per-school CSS/JS overrides
│   └── logos/             # Optional school logos
├── docs/                  # Discovery notes
├── requirements.txt
├── manage.py
├── .env.example
└── README.md

⸻

Local Setup (Step-by-Step)

These steps assume macOS and no prior Django experience.
	1.	Clone the repository

git clone <your-repo-url>
cd student_enrollment_portal

	2.	Create and activate virtual environment

python3 -m venv venv
source venv/bin/activate

	3.	Install dependencies

pip install -r requirements.txt

	4.	Install PostgreSQL (Homebrew)

brew install postgresql@16
brew services start postgresql@16

	5.	Create database

createdb student_enrollment_portal

	6.	Environment variables

Copy the example file:

cp .env.example .env

Edit .env:

DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=True
DATABASE_URL=postgres://<your-mac-username>@localhost:5432/student_enrollment_portal
ALLOWED_HOSTS=localhost,127.0.0.1

	7.	Run migrations

python3 manage.py migrate

	8.	Create admin user (platform owner)

python3 manage.py createsuperuser

	9.	Start the server

python3 manage.py runserver

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

	4.	Update form fields as needed
	5.	Restart the server
	6.	Visit:

/schools/my-new-school/apply

⸻

GitHub Setup (Optional)

To push this project to GitHub:

git remote add origin <github_repo_url>
git branch -M main
git push -u origin main


⸻

Demo: How to Pitch This to a School (5 Minutes)

Use this exact flow when talking to a school owner or administrator.

Step 1: Show Their Branded Form (30 seconds)
	•	Open:

/schools/<school_slug>/apply
	•	Explain:
	•	“This replaces your PDF and email-based application.”
	•	“Parents can submit from phone or laptop.”

Step 2: Submit a Test Application (1 minute)
	•	Fill out the form with dummy data
	•	Click Submit
	•	Show the success page

Step 3: Show the Admin View (1 minute)
	•	Open:

/admin/
	•	Log in as the school admin
	•	Click Submissions
	•	Open the newly submitted entry

Step 4: Explain the Value (2 minutes)
	•	No PDFs
	•	No email back-and-forth
	•	One place for all applications
	•	Easy exports (CSV)

Step 5: Close (30 seconds)
	•	“We can customize fields in minutes.”
	•	“You get your own login and only see your data.”

⸻

Non-Technical Operations Guide

How to Add or Edit Form Fields
	1.	Open:

configs/schools/<school_slug>.yaml
	2.	Edit labels, required fields, or options
	3.	Save the file
	4.	Restart the server
	5.	Refresh the browser

No database or code changes required.

⸻

Branding + Theme (Phase 9)

Each school can have its own theme using YAML only (no code changes).

Theme variables

In your school YAML:

branding:
  theme:
    primary_color: "#111827"
    accent_color: "#7c3aed"
    background: "#f7f7fb"
    card: "#ffffff"
    text: "#111827"
    muted: "#6b7280"
    border: "#e5e7eb"
    radius: "16px"

The form template injects these as CSS variables so the UI updates instantly.

Optional per-school custom CSS / JS

If a school needs custom tweaks beyond theme colors:

branding:
  custom_css: "schools/<school_slug>/forms.custom.css"
  custom_js: "schools/<school_slug>/forms.custom.js"

Files live under:

static/schools/<school_slug>/...

Notes:
	•	The YAML value must be static-relative (do not include /static/).
	•	If custom_css / custom_js is blank, nothing is loaded.

⸻

Waiver Acknowledgement (Phase 8 MVP Choice)

Some schools require a medical/liability waiver. For MVP, we support a simple acknowledgement checkbox on the registration form:

Example field:

- key: "medical_waiver_ack"
  type: "checkbox"
  required: true
  label: "I acknowledge that I will be required to complete a Medical & Liability Waiver before enrollment is finalized."

This approach:
	•	avoids e-signature complexity in MVP
	•	still captures agreement intent at submission time

⸻

How to View Applications
	1.	Go to /admin/
	2.	Log in
	3.	Click Submissions
	4.	Use the search bar to find a student/application
	•	Search is case-insensitive and supports partial matches

⸻

What NOT to Touch
	•	Do not edit files in venv/
	•	Do not edit Django migration files
	•	Do not delete school_slug values once live

⸻

Admin Access (Phase 10): School-Scoped Users + Submissions

This project supports per-school admin users so each school only sees their own data.

Roles
	•	Superuser (platform owner)
	•	sees all schools
	•	sees all submissions
	•	can create new users/admins
	•	School admin (staff user + membership)
	•	sees only their school’s submissions
	•	can edit submission JSON if needed (MVP)

How to Create a School Admin (Fast Flow)
	1.	Go to /admin/ → Users → Add user
	2.	Enter the basic user info
	3.	Choose the School from the dropdown (superuser only)
	4.	Save

The system automatically:
	•	sets is_staff = True so they can log in
	•	creates a SchoolAdminMembership linking them to that school

Important:
	•	The membership link is what scopes what they can see.
	•	If a user can log in but sees no submissions, verify they have the correct membership.

SchoolAdminMembership (Important)

Users are scoped to a school via SchoolAdminMembership.

If you ever need to fix a user’s school access:
	1.	Go to /admin/ → School admin memberships
	2.	Confirm the mapping is correct: user → school

(Keep this in mind when updating the README next — we’ve agreed this must be documented.)

⸻

Submissions Admin (Phase 10 Improvements)

List View
	•	School admins:
	•	see submissions for their school only
	•	Superuser:
	•	sees all submissions
	•	sees an extra “School” column to differentiate across schools

Search

Search supports partial, case-insensitive matches for:
	•	Student/Applicant
	•	Program
	•	(also supports school name/slug for superuser)

Program Meaning

In Phase 10, “Program” is treated as Interested In (where applicable).

⸻

Export CSV (Per-School)

In /admin/ → Submissions:
	1.	Select rows
	2.	Action → Export selected submissions to CSV

Export includes:
	•	created_at
	•	student/applicant name
	•	all JSON keys discovered in selected rows

⸻

Reporting (Phase 10)

Schools can have a basic “Reports” page accessible from the school admin dashboard:

/schools/<slug>/admin/reports

MVP intent:
	•	keep reporting simple for demo
	•	expand later with more charts/filters

(Reporting is school-scoped — admins only see their school data.)

⸻

Troubleshooting
	•	Python command not found (Mac):
Use python3 instead of python.
Example:

python3 manage.py runserver
python3 manage.py shell


	•	Static/CSS changes not showing:
Do a hard refresh: Cmd+Shift+R (Mac)
	•	Custom CSS not loading:
Verify the YAML branding.custom_css path is static-relative and the file exists:

python3 manage.py findstatic schools/<slug>/forms.custom.css -v 2


	•	DOB → Age behavior:
Age is client-calculated from date_of_birth on blur/change.
If DOB is incomplete or unrealistic, age stays blank (MVP choice; we can tighten validation later).
	•	User can log in but sees no submissions:
Ensure:
	•	is_staff = True
	•	A SchoolAdminMembership exists and points to the correct school

⸻

MVP Limitations (Planned Improvements)
	•	More user-friendly submission editing
Today submissions are stored as JSON; editing is possible but not ideal.
We may later render a friendly “edit form” experience for admins.
	•	Multi-form flows per school (future requirement)
Some schools may want multiple steps/forms (demographics → education → health → waivers).
Architecture should support multiple forms per school in later phases.
	•	Legal e-signatures (future requirement)
Add proper e-signatures for waivers/medical forms after MVP.
	•	Admin Users UI improvements (future enhancement)
	•	School-scoped user list (hide superuser from school admins)
	•	remove extra filters
	•	safer permissions (no delete for school admins)
	•	cleaner user detail page (hide password hash, simplify buttons)
	•	Reminder: revert temporary Kimberlas CSS tests
If any test-only CSS tweaks were added (border/button), revert them before shipping.

⸻

License

Internal MVP / demo use
