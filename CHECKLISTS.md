Student Enrollment Portal — Master Checklists

This file is the single source of truth for onboarding schools, running pilots, and going live. All other checklists should be considered deprecated.

⸻

0) Platform Readiness (one-time per environment)

Do this once per repo / environment (local, staging, prod).
	•	Render service deployed and reachable (public URL works)
	•	Django admin reachable (/admin/)
	•	Superuser login works
	•	Database migrations applied (python manage.py showmigrations core looks sane)
	•	Seed script runs successfully on a fresh DB
	•	Email provider configured and verified (SendGrid / API)
	•	Sentry connected and capturing errors
	•	Static files load correctly (no broken CSS on apply or admin pages)

⸻

A) School Intake / Pre-Pilot (per school)

Goal: confirm what the school needs and what success looks like.

School basics
	•	School name confirmed
	•	School slug confirmed (URL-safe, permanent)
	•	Website URL captured
	•	Source URL captured

Current enrollment flow
	•	PDF only
	•	Email only
	•	Phone only
	•	Mixed

MVP scope confirmation
	•	Enrollment request
	•	Trial / evaluation request
	•	Mixed (single form)

Form requirements
	•	Student name fields
	•	DOB / Age (auto-calc if needed)
	•	Program selection
	•	Guardian info (required / optional)
	•	Schedule preferences
	•	Notes / free text

Branding
	•	Default theme acceptable
	•	Custom colors/logo required

Notifications
	•	Submission email recipients (to)
	•	Optional CC list
	•	Optional BCC list (include platform owner during pilot)

⸻

B) Build & Configuration (per school)

Goal: YAML + Admin setup are correct and consistent.

YAML configuration
	•	YAML file created
	•	school.slug matches URL and filename
	•	Form renders without errors
	•	Required fields enforced correctly
	•	Select/multiselect options have both label and value

Reporting / display alignment
	•	Student display name resolves correctly
	•	Program display resolves correctly
	•	Multi-form program mapping verified (if applicable)

Success + email config
	•	success block present
	•	notifications.submission_email present
	•	to list is non-empty
	•	from_email is verified sender
	•	Subject renders correctly

Admin setup
	•	School record exists in admin
	•	School admin user exists
	•	SchoolAdminMembership created
	•	School admin is properly scoped (cannot see other schools)

⸻

C) Demo Readiness

Goal: a clean 10–15 minute demo with no surprises.
	•	Apply page loads (/schools/<slug>/apply/)
	•	Submit test application (no files)
	•	Success page loads
	•	Submission appears in admin
	•	Application ID visible
	•	Student name populated
	•	Program populated
	•	Submission editable and saves correctly
	•	Audit log entry created (if enabled)
	•	Submission email received
	•	Reports page loads
	•	CSV export works

⸻

D) Go-Live Checklist (per school)

Goal: move from demo to real usage safely.

Final verification (15–20 min)
	•	Production URL confirmed
	•	Admin URL confirmed
	•	School admin credentials tested
	•	Email recipients finalized (remove owner BCC if desired)
	•	Mobile rendering verified
	•	No obvious permission leaks

Launch
	•	Apply page link sent to school
	•	Admin login instructions sent
	•	One real submission completed
	•	School confirms email notification received
	•	School confirms admin access

⸻

E) Post-Go-Live Monitoring (first 48h)
	•	Monitor Sentry for errors
	•	Confirm email delivery (not spam)
	•	Verify submissions appear as expected
	•	Collect initial feedback

⸻

F) Pilot Closeout
	•	Review pilot usage
	•	Confirm value delivered
	•	Share pricing / next steps
	•	Convert to paid OR close pilot

If closing:
	•	Mark as dead lead
	•	Optionally remove YAML + school config later

⸻

Notes
	•	YAML files used for early demos are disposable.
	•	Only production schools require long-term config hygiene.
	•	Application ID (public ID) is the school-facing identifier; internal DB IDs remain internal.