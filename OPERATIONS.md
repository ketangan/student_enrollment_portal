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

## 📩 Submissions Admin

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
	•	No email backend (SMTP) configured
	•	Single form per school
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
