Student Enrollment Portal (Multi‑School Application MVP)

Overview

Student Enrollment Portal is a multi‑tenant web application that allows multiple real‑world schools/programs to collect applications online using YAML‑defined forms, while sharing a single backend, database, and codebase.

This MVP is designed for small institutions (dance studios, cultural programs, academies, etc.) that currently rely on PDF or email‑based applications.

Key goals:
	•	One backend, many schools
	•	No custom code per school
	•	Forms defined in YAML (easy to change)
	•	Simple admin access per school
	•	Production‑ready architecture

⸻

How It Works (Mental Model)
	1.	Each school is identified by a school_slug
	2.	A YAML file at configs/schools/<school_slug>.yaml defines:
	•	School metadata
	•	Form sections and fields
	3.	Visiting /schools/<school_slug>/apply:
	•	Loads the YAML config
	•	Dynamically renders the form
	4.	On submit:
	•	Data is validated
	•	Stored as JSON in PostgreSQL
	5.	Admins log in to view submissions for their school

⸻

Repository Structure

student_enrollment_portal/
├── config/                # Django settings, URLs, WSGI
├── core/                  # Main app (models, views, services)
│   ├── services/          # YAML loading, validation
│   ├── templatetags/      # Template helpers
│   └── migrations/
├── configs/
│   └── schools/           # One YAML file per school
├── templates/             # Shared HTML templates
├── static/
│   └── logos/             # Optional school logos
├── docs/                  # Discovery notes
├── requirements.txt
├── manage.py
├── .env.example
└── README.md


⸻

Local Setup (Step‑by‑Step)

These steps assume macOS and no prior Django experience.

1. Clone the repository

git clone <your-repo-url>
cd student_enrollment_portal

2. Create and activate virtual environment

python3 -m venv venv
source venv/bin/activate

3. Install dependencies

pip install -r requirements.txt

4. Install PostgreSQL (Homebrew)

brew install postgresql@16
brew services start postgresql@16

5. Create database

createdb student_enrollment_portal

6. Environment variables

Copy the example file:

cp .env.example .env

Edit .env:

DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=True
DATABASE_URL=postgres://<your-mac-username>@localhost:5432/student_enrollment_portal
ALLOWED_HOSTS=localhost,127.0.0.1

7. Run migrations

python manage.py migrate

8. Create admin user

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

	3.	Edit:

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
	•	Easy exports (coming next)

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

How to Add a New School
	1.	Copy an existing YAML file
	2.	Rename it to the new school slug
	3.	Update:

school:
  slug: "new-school"
  display_name: "New School"


	4.	Restart server
	5.	Visit:

/schools/new-school/apply



⸻

How to View Applications
	1.	Go to /admin/
	2.	Log in
	3.	Click Submissions
	4.	Filter by school

⸻

What NOT to Touch
	•	Do not edit files in venv/
	•	Do not edit Django migration files
	•	Do not delete school_slug values once live

⸻

MVP Limitations (Planned Improvements)

License

Internal MVP / demo use
