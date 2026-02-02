(Sales / Demo / Product Overview)

# Student Enrollment Portal  
### Multi-School Online Application Platform

Student Enrollment Portal is a **multi-tenant application platform** that allows schools, studios, and programs to collect applications online — without custom development per school.

It replaces PDFs, email, and paper forms with a modern, branded, and secure application experience, while sharing a single backend and database.

---

## Who This Is For

- Dance studios
- Music academies
- Cultural programs
- After-school programs
- Small schools and academies
- Any organization collecting student registrations or applications

---

## What Problems It Solves

- ❌ PDF forms and email chaos  
- ❌ Manual data entry  
- ❌ No reporting or visibility  
- ❌ Custom dev per school  

**→ One platform. Many schools. Zero custom code.**

---

## Key Features

### Multi-School, Single Platform
- One backend, many schools
- Each school has its own URL and admin access
- No data leakage between schools

### Dynamic Forms (No Code)
- Forms defined in simple YAML files
- Change fields, labels, and options without code
- New schools can be onboarded in minutes

### School-Scoped Admin Access
- School admins see **only their data**
- Superusers manage all schools
- Safe, permission-aware access model

### Built-In Reporting
- Date-range filters
- Program breakdowns
- Visual charts for quick insights
- CSV exports for offline analysis

### Branded Experience
- Per-school colors, logos, and themes
- Defaults provided if branding isn’t configured
- No frontend code changes required

---

## How It Works (High Level)

1. Each school gets a unique slug (e.g. `dancemaker-studio`)
2. Visiting:

/schools//apply

shows that school’s application form
3. Submissions are validated and stored securely
4. School admins log in to review submissions and reports

---

## Example Screens

- Student application form  
- School admin dashboard  
- Reports with charts and CSV export  

*(Screenshots can be added here later)*

---

## Security & Isolation

- Schools **cannot access each other’s data**
- Admin permissions are enforced at every layer
- JSON storage allows flexible schemas without migrations

---

## Deployment Ready

- Built with Django + PostgreSQL
- Cloud-ready (Render / Fly / Heroku-style platforms)
- CI-tested with unit and end-to-end coverage

---

## Status

**MVP complete**

Currently live-ready for:
- Application intake
- Admin review
- Reporting & exports

---

## Roadmap Highlights

- Lead capture (pre-application)
- Email invitations & password resets
- Advanced analytics
- Multi-form support per school
- E-signatures and waivers

---

## Contact / Demo

If you’re interested in a demo or pilot:
> Reach out to the project owner for access and walkthroughs.
