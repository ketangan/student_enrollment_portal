# Enrollment and Teacher Booking Setup

Updated: November 9, 2026. Local demonstration, not a production rollout.

## Current Three-Journey Demo

This section supersedes the November 7 setup notes below. Those notes remain as
implementation history, not current instructions for the simplified demo.

- Owner: `demo_admin` / `admin` at the school admin link below.
- Teacher: `school_teacher_3` / `admin` (Alex), or `school_teacher_4` / `admin`
  (Morgan). These are teacher-only local accounts, not school administrators.
- Owner menu: Dashboard, Enrollment, Leads, Schedule, Reports, Settings.
- The demo uses Main Campus only. No location filters or public school-page
  shortcut are shown in the admin. Programs is managed from Settings.
- Public entry: <http://127.0.0.1:8002/bookings/enrollment-request-demo/school/>.
- Teacher entry: <http://127.0.0.1:8002/bookings/enrollment-request-demo/teacher/>.

The enabled journeys are direct enrollment, a teacher trial, and one-off classes
or seasonal programs. Tours, playdates, recurring-series creation, family access
provisioning, attendance, application linking, make-up setup, parties, inventory
and operator test controls are disabled in this demo. Family management and
attendance endpoints are also blocked; their historical data is retained.
New recurring-series creation and bulk lesson changes are blocked
in this profile. Existing staged admissions still use their original access rules.

### Session Workspace

- Lessons and trial bookings share a session-detail partial, using the established
  workshop/program heading, summary strip, sessions grid and icon actions.
- Lesson types, teachers, weekly hours and time off reuse their protected forms
  inline. Add targets the appropriate grid; narrow screens keep the editor outside
  the horizontal table scroller so fields and Save remain reachable.
- Only the Teachers tab shows Add teacher. Reschedule and cancellation retain
  signed preview/confirmation and concurrency checks. Optional reschedule notes
  do not skip those checks or change payment records.
- Staff trial creation has authenticated routes and uses the existing availability,
  booking, immutable quote and retry services. Audit records identify the staff
  member. Public trial URLs still render the customer flow.
- The simplified school layout shares back-arrow icons and heading sizes across
  enrollment and scheduling. Other layouts keep their previous presentation.
- Verification evidence for this iteration is in the local
  `session-consistency-20261109` artifact folder: editor screenshots and responsive
  checks at 1280x900, 768x1024, 1024x768 and 390x844. Physical Android Chrome and
  virtual-keyboard behavior remain an actual-device check, not proven by emulation.

### Configure Another School

Use the same scoped YAML convention, not a customer-name branch:

```yaml
scheduling:
  navigation:
    layout: school
    features: [lessons, workshops, camps, reservations]
    simplified: true
    single_location: true
  workflows:
    teacher_self_service: true
    direct_enrollment: true
    enrollment_linking: false
    lesson_changes: single
    reschedule_reason_required: false
    class_setup: requests
    optional_features: []
```

Boolean feature switches default off; `reschedule_reason_required` defaults true
to preserve existing schools' audit requirements. The demo opts out of requiring
a note, while changes still record the staff member and before/after schedule.
Omitted `optional_features` retains the existing
recurrence, make-up, family-booking, operator-tool, participant-choice, inventory,
attendance and family-management
presentation. This operator policy does not replace database capabilities, staff
roles, location restrictions, publication or environment restrictions.

`single_location` hides location selectors only when exactly one permitted
location exists. It does not alter authorization or discard a stale/invalid filter.
Adding a second location restores the selector. Leave this option off for a
multi-location school. Scheduling settings links to Rooms and Staff access; teacher
hours remain under Schedule, and trial/class/program setup remains under Programs.

1. Provision normal enrollment and scheduling memberships, capabilities, locations,
   rooms and services using the existing setup. Never grant superuser access to
   enable scheduling.
2. Add teachers and subjects. An owner may bind an existing login only after
   verifying identity. Binding does not create a scheduling-owner membership.
3. Add weekly hours. Future edits require an effective date; history remains
   unchanged and edits that invalidate scheduled lessons are rejected. Teachers
   use My availability and can add time off for their own account only.
4. Configure trial offers under Programs, including notice period, horizon and price.
   Schedule > Book a trial stays in the staff workspace, reusing the public slot,
   quote, conflict and retry services. The public booking URLs remain separate.
   Public trials create their own booking records. Staff may explicitly link the
   booking to an application after verifying identity; email matching never merges
   people automatically.
5. For a class, set price, enable registration requests, add future sessions, then
   publish the sessions and offering. Review requests from Schedule > Class requests.
   Use the existing reservation action to confirm capacity. A request alone does
   not reserve seats; a paid reservation may remain held awaiting payment.
6. For a seasonal program, use the existing multi-session program setup. Set all
   dates and registration/payment policy. No material catalog is required.
7. Configure application fields/statuses for the school. Direct entry does not need
   a tour; explicitly resumed historical staged applications retain their gates.
8. Verify each journey, teacher/owner permissions, cross-school isolation and devices
   before enabling a real school.

### Refresh Local Examples

After applying migrations 0057 and 0058 to the existing preview database, run
`refresh_school_journey_demo` with the established external `preview_settings`.
The command refuses non-local databases, non-demo schools, external email backends
and connected school SMTP/Stripe settings. It adds future family-music classes and
a three-morning discovery program, preserves bookings, and creates restricted local
teacher accounts only when absent. It does not reset existing passwords. It is a
guarded fixture helper, not a production provisioner.

### Boundaries

This work is not solely configuration: additive account/availability/application-link
fields and opt-in shared views/services support it. SBMC and Plushie have not opted
in, and their configurations/records have not been changed. Regression evidence is
still required before release. The public trial engine remains a local preview;
real lesson email delivery and online lesson payment are separate work. Never turn
on production DEBUG to enable this demo. Prices shown for trials are payable at the
studio, not proof of collected money.

The older sections below record prior tests; they do not verify this revision.
Current checks and remaining audit items are recorded in the product audit.

## Open the Demo

- [School admin](http://127.0.0.1:8002/schools/enrollment-request-demo/admin/)
- [Lesson setup](http://127.0.0.1:8002/bookings/enrollment-request-demo/lessons/setup/)
- [Lesson calendar](http://127.0.0.1:8002/bookings/enrollment-request-demo/lessons/)
- [Customer teacher/time selection](http://127.0.0.1:8002/bookings/enrollment-request-demo/trials/)
- [Taylor's enrollment lead](http://127.0.0.1:8002/schools/enrollment-request-demo/admin/leads/9/)

Username: **demo_admin**. Password: **admin**. This deliberately simple password
is for this local demo only. Sign out of another school's account before testing.

The existing Enrollment Request Demo is displayed as **Riverside Learning Academy**
(`enrollment-request-demo`). One school-owner account can use enrollment and
Scheduling. No superuser access is needed. The existing local server uses port
8002 and database `pontora_bookings_m1a_preview`; no additional database or server
was created. Hosted demo, SBMC, Plushie records and live payment settings were not changed.

## What Is Ready

| Item | Current configuration |
| --- | --- |
| Location / room | Main Campus / Piano Room; America/Los_Angeles |
| Service | Piano lesson, 30 minutes |
| Alex Demo | Monday-Friday, 1-6 PM; 5-minute gap; 30-minute inter-location travel buffer |
| Morgan Demo | Tuesday/Thursday, 10 AM-2 PM; same buffers |
| Public offers | Alex: $30; Morgan: $35; payment collected by the studio |
| Recurring lessons | Riley Park with Alex: Mondays Nov 9-30 at 3 PM; Jamie Park with Morgan: Tuesdays Nov 10-Dec 1 at 11 AM |
| Additional staff booking | Avery Chen with Alex: Nov 9 at 4 PM |
| Customer-created booking | Sam Schedule Check with Morgan: Nov 10 at 10 AM |
| Inventory | No catalog or inventory-linked offering configured for this school |

Existing October records and other demo capabilities were preserved. Their menu
entries may still be visible: this is not a newly stripped-down school. Do not
delete historical bookings or bypass capability-removal guards just to simplify a demo.

## Repeatable Setup Order

1. **Establish the school and enrollment configuration.** Reuse the normal School
   provisioning process and scoped `configs/schools/<slug>.yaml` conventions. Set
   its programs, application fields and workflow. Use ordinary school-admin
   membership; do not grant superuser access as a shortcut.
2. **Enable Scheduling for the same school.** Use school **Settings > Scheduling**
   and the existing scoped `SchedulingProfile`/`SchedulingMembership` controls.
   For a new enrollment-plus-lessons school, enable the `lessons` capability; it
   does not require workshops, parties or inventory. Preserve required capabilities
   when augmenting an existing school with historical records. Enable lesson
   scheduling in **Lesson setup > Settings** (`lessons_enabled`).
3. **Create locations and rooms.** Set each location's actual timezone. A room
   shared with other bookings must be the same Room record, not a duplicate, so
   conflicts can be detected consistently.
4. **Create services.** In **Lesson setup**, add the subject and duration. Do not
   attach an inventory catalog for ordinary teacher lessons.
5. **Create teachers.** Assign the services each teacher can deliver, plus the
   required between-lesson and inter-location travel buffers.
6. **Add weekly availability and closures.** Availability belongs to a teacher
   and location; closures handle exceptions. Check that room, service and teacher
   are active and scoped to the same school.
7. **Add families and students.** Use **Lesson setup > Add family / Add student**.
   At present this is a manual handoff from enrollment, not an automatic conversion.
   Verify identity before associating students with a household. Do not merge
   records merely because an unauthenticated booking supplied the same email.
8. **Schedule recurring or single lessons.** Choose student, service, teacher,
   location, room, dates, start time and recurrence. Preview dates; resolve every
   conflict before confirming. Equal first/last dates create a single occurrence.
9. **Configure customer booking offers.** Set service, teacher, room, quoted price,
   notice period, booking horizon and start-time interval. Select public trials,
   family booking and make-up eligibility only where appropriate. Publish the
   offer after checking its availability. The current customer flow is local-only;
   see the restriction below before planning a real school's rollout.
10. **Configure private family access and make-up policy if required.** Use the
    existing family-access and make-up controls. Private access must be issued to
    the verified guardian, not inferred from a matching public-booking email.
11. **Verify both features under the ordinary school account.** Check enrollment,
    teacher availability, customer booking, duplicate/conflicting booking rejection,
    cancellations/rescheduling and school/family isolation before release.

No customer-name branches, duplicated booking engine or new application models
were needed for this demonstration. The existing forms, scheduling planner,
preview/confirmation service and offer service were reused.

## Changes Made to This Local Copy

1. Checked the prior full regression: 5,411 application tests and 23 migration tests
   passed (three skips), plus 157 JavaScript tests and coverage gates; source unchanged.
2. Reused the school's existing owner, profile, lesson capability, Main Campus,
   Piano Room, Piano lesson service, Alex Demo and Park Family.
3. Set only the school-scoped `demo_admin` password to the requested local value.
4. Enabled `BOOKINGS_LESSON_BOOKING_PREVIEW` and `BOOKINGS_MESSAGE_PREVIEW` in the
   existing external `preview_settings.py`, with DEBUG and in-memory email retained.
   Restarted the same 8002 server. No real customer email was sent.
5. Added Morgan, the Chen family/student, Morgan's offer and the two future recurring
   series using existing validated services and transactions. Repeating the local
   setup on the same date reused those records rather than duplicating them.
6. Used the UI to demonstrate conflict rejection and save Avery's available 4 PM
   lesson. Used the public form to book Sam's 10 AM lesson, then confirmed 10 AM
   disappeared from the available times.
7. Marked the existing fictional Taylor Walkthrough tour attended through the normal
   staff flow. Taylor's lead now has **Start Enrollment** available. The application
   was opened but not submitted, so it remains available for the owner's walkthrough.

This school already uses tour-first admissions. Its bare `/apply/` URL intentionally
requires the application link released after the tour. Use Taylor's lead above,
then **Start Enrollment**. Do not disable the admissions policy after historical
cases exist or bypass the release gate. For a new school that does not need tours,
do not configure the optional tour-first policy in the first place.

The guarded local fixture helper and evidence are in the local visualization
directory under `enrollment-lessons-20261107`. It is not a production provisioner:
its date-relative series can create a new cohort on a later run. Prefer the UI
steps above for future schools. Preserve history; use normal cancellation rather
than deleting bookings if a demonstration appointment is no longer wanted.

## Try It

1. Sign in and switch between **Submissions/Leads** and **Scheduling**.
2. Open Taylor's lead, select **Start Enrollment**, and try the existing application.
3. In **Scheduling > Teachers & availability**, inspect both teachers' services and weekly availability.
4. Open the customer page, select a teacher and a future available date/time.
   Morgan has Tuesday/Thursday availability; Alex has weekday afternoons.
5. In **Schedule lessons**, try Alex/Riley/Nov 9 at 3 PM to see the conflict, then
   choose an available slot. Review dates before confirming.
6. Inspect the saved lessons and existing cancellation/reschedule/make-up controls.

## Limits and Follow-ups

- **Shared school access is working; automatic student handoff is not implemented.**
  Enrollment submissions and lesson households/students remain distinct records.
  A real unified lifecycle needs an explicit, authorized linking/conversion design.
- **Customer lesson booking remains DEBUG-only preview.** The gate requires DEBUG
  and `BOOKINGS_LESSON_BOOKING_PREVIEW`; configuring an offer alone does not make
  this a production booking feature. Never enable production DEBUG to bypass this.
- **Prices are quotes for payment at the studio.** This is not online lesson tuition
  checkout, recurring billing or a change to SBMC subscriptions.
- **Public enrollment UI polish remains.** The enrollment
  form's multi-select instructions mention Ctrl/Command, and its Submit control
  measured 40px high rather than the preferred 44px. These were documented, not
  silently changed across existing schools during a configuration-only task.
- **Device acceptance remains.** Emulation is not actual Android Chrome testing;
  physical touch, native selects, virtual keyboard and printing remain unverified.

## Verification Record

- 262 targeted tests passed: integrated demo, lessons, lesson changes/views,
  appointment services/views, family access/views and enrollment application flow.
- Manual browser checks: ordinary admin login; shared navigation; tour release;
  existing application rendering; recurring schedule visibility; conflict rejection;
  successful staff and customer bookings; booked slot no longer offered.
- In-app desktop-browser emulation checked enrollment, lesson setup, customer offers,
  customer time selection and staff scheduling at desktop 1440x900, phone 390x844,
  tablet portrait 768x1024 and landscape 1024x768. No page-width overflow in those
  checks. Tablet navigation/calendar and customer field keyboard focus were exercised.
  This was not a complete responsive audit of every pre-existing school screen.
- Fresh full regression: tracked separately in `enrollment-lessons-20261107/status.json`.
  Treat it as pending until its completion status and unchanged fingerprint are checked.
- No commit, push or hosted configuration change was made for this setup.

## November 7 Navigation Follow-up

The configuration-only setup above exposed a navigation problem: opening lessons
switched to a studio menu. The follow-up adds an opt-in shared school layout using
the existing enrollment navigation and scheduling permissions, forms and services.
Configure another combined school in its operator-managed YAML:

```yaml
scheduling:
  navigation:
    layout: school
    features: [lessons, visits]
```

This is presentation configuration, not authorization or capability enablement.
The school and staff must still have the required scheduling capabilities and
memberships. Omit `navigation` to preserve the existing bookings layout. The school
layout currently supports lessons and visits; studio tools use the bookings layout.
Historical bookings are retained, not deleted or migrated by this setting.

- The enrollment dashboard remains the single home. Its top menu remains present
  throughout scheduling for staff authorized for enrollment.
- Scheduling groups lessons, bookings, students/families, teachers/availability,
  make-up credits and tours. Setup saves return to the relevant section.
- Lessons default to upcoming sessions, with family and past/all filters.
- School Settings > Scheduling groups lesson rules, admissions visits and shared
  school resources. It does not advertise workshops, camps or inventory here.
- Scoped responsive styles fix phone dashboard/email-form overflow and enlarge
  scheduling edit/date controls without changing the default school/studio layout.

Verification: 397 focused tests passed, including SBMC, integrated admissions,
lesson scheduling/changes, family access, booking and credit views; the new
navigation module reached 100% statement/branch coverage. Teacher setup query counts
remain constant after adding five teachers with services. Browser checks covered
54 discovered admin pages/form variants at 1440x900, 390x844, 768x1024 and 1024x768,
plus all six school settings tabs. No page-width overflow or primary-menu mismatch
remained. Reports uses a heading outside `main`, checked separately after the
automated DOM audit flagged the missing `main` heading. Manual interactions checked
mobile/tablet menus, teacher/student saves, filters and conflicting/available lesson
previews. These checks do not claim every settings mutation or physical Android QA.

The previous setup's full regression passed with unchanged source (5,411 application,
23 migration and 157 JavaScript tests, three skips, all coverage gates). It does not
verify this follow-up. The new detached full run is recorded under
`school-navigation-20261107/status.json` in the local visualization directory.
Reviewed November 8: failed with one Studio Prep overlapping-starts test failure;
5,455 application tests and 23 migration tests passed, with unchanged source.
Coverage/JavaScript gates were not reached. Do not release on this result.
See the [November 8 product audit](enrollment-demo-product-audit.md) for the approved
three-journey demo scope, remaining functional gaps and proposed simplification.

## November 8 Navigation and Calendar Update

For schools that opt into `scheduling.navigation.layout: school` and
`simplified: true`, the current navigation is Dashboard, Enrollment, Leads,
Schedule, Reports and Settings. Programs is under Settings, with existing
enrollment programs, trial offers, classes and seasonal programs. This supersedes
the earlier navigation inventory above; the default enrollment and studio layouts
remain unchanged. No school-name or slug conditional selects this behavior.

Schedule offers Calendar and List when the existing database-backed
`SchedulingProfile.calendar_enabled` setting is enabled and the school has an
authorized scheduling feature. Calendar setup accepts lesson-only schools; the
simplified layout keeps Dashboard as an overview, not a calendar replacement.
An authorized operator enabled this flag on the local Enrollment Request Demo
through the existing audited calendar-setting service. No other school or hosted
configuration was changed. Future school setup should use that same setting,
not a second YAML calendar flag or a demo-only code path.

Calendar reuses the bundled calendar library, existing lesson/session records,
location permissions and schedule queries. Day, week and month views preserve
teacher/location filters, and detail back links preserve the current view and
date. List and Calendar show the same selected period when switching views.
Initial List remains an upcoming-events list. The calendar is read-only; opening
an event uses the existing booking/class editing workflow. Week view scrolls
horizontally on narrow screens; Day and List remain available for smaller screens.

Primary commands use the existing blue action style. Edit, Cancel and Filter stay
secondary. Changed child pages have top-left back arrows; teacher hours/time-off
forms return to the selected teacher. Styling is scoped to the opted-in shell.

Verification for this update: 521 focused Python tests passed, including SBMC,
existing calendar behavior, program management, school permissions and navigation.
The calendar JavaScript tests cover safe rendering, missing-library fallback,
read-only configuration and click/keyboard navigation. Browser checks covered 17
admin pages at 1440x1000, 390x844, 768x1024 and 1024x768 (68 page/viewport checks),
plus calendar day/week/month, date picker, teacher filter/reset, event details/back,
mobile menu and invalid form submission/Cancel. No document-width overflow or
error page appeared in those checks. These are in-app Chromium emulation checks,
not physical Android, virtual-keyboard or printing verification.

The preceding full run passed 5,676 application and 24 migration tests but failed
the per-module coverage gate. Added regression cases now cover the demo refresh
command and teacher views at 100% in their focused coverage run. They also exposed
an overlapping synthetic workshop time, corrected for newly generated samples
without changing existing bookings. That earlier full result does not verify this
update. Its fresh full regression, revision, source fingerprint, logs and final
exit status are tracked in `school-ui-consistency-20261108/status.json` under the
local visualization evidence directory. Treat it as pending until reviewed.

No commit, push, production enablement or real email/payment was performed.

## Explicit Workflow Configuration

November 9 scheduling cleanup: the demo now sets `enrollment_linking: false`
and omits `attendance` and `family_management` from its explicit optional features.
This removes their controls and blocks direct feature endpoints, without deleting
students, households, attendance history or existing application associations.
`family_management` controls the family/student administration and private family
portal; it is distinct from `family_booking`, which controls booking offers.
Legacy configurations retain their previous attendance and family behavior.

In the simplified school layout, lesson rescheduling/cancellation and teacher,
lesson-type, hours and time-off editors reuse the existing forms inline. On a
narrow screen the editor sits below its scrollable grid instead of being clipped
inside it. Existing signed previews, validation, permission checks and writers
remain authoritative. Only one editor opens at a time; closing restores focus,
and an uncertain save blocks resubmission until the page is refreshed. Historical
make-up entitlements still appear in cancellation previews where required by the
saved booking terms, even when new make-up setup is hidden.

Verification evidence is in `schedule-cleanup-20261109` under the local
visualization directory: focused/full status files record revisions, working-tree
fingerprints and logs. Browser checks cover desktop 1440x1000, tablet 768x1024
and 1024x768, and phone 390x844. Physical Android Chrome and virtual-keyboard
checks remain pending. Full regression must pass before any release.

The November 8 policy cleanup separates presentation from behavior. New schools
should use two sections in their normal school YAML, alongside their existing
database-backed scheduling capabilities and permissions:

```yaml
scheduling:
  navigation:
    layout: school
    simplified: true
    single_location: true
    features: [lessons, workshops, camps, reservations]
  workflows:
    teacher_self_service: true
    direct_enrollment: true
    enrollment_linking: false
    lesson_changes: single
    class_setup: requests
    optional_features: []
```

This is the Enrollment Request Demo's current configuration. Its displayed
screens and existing bookings are unchanged by moving these settings.

| Setting | Purpose | Default in an explicit workflows section |
| --- | --- | --- |
| `teacher_self_service` | Teacher-bound access and future-effective availability editing, using existing authorization. | `false` |
| `direct_enrollment` | Permit a new application without staged tour/playdate entry; keep existing admissions cases intact. | `false` |
| `enrollment_linking` | Offer an authorized, explicit link between a trial and an enrollment record. Never merge by email alone. | `false` |
| `lesson_changes` | `single` limits staff changes to the selected occurrence; `series` permits the existing future-occurrence workflow. Both forms and writers enforce it. | `series` |
| `class_setup` | `requests` exposes the existing registration-request switch; `checkout` exposes existing payment/deposit controls. This selects setup controls, not a payment provider or a new public booking engine. | `checkout` |
| `optional_features` | Existing tools: `recurring_lessons`, `makeup_credits`, `family_booking`, `operator_tools`, `participant_choices`, `inventory`, `attendance`, `family_management`. | All eight for backwards-compatible full tool availability. |

An explicit empty `workflows: {}` uses these defaults regardless of layout.
Switching between simple/full school navigation must not change these rules.
Layout and menu labels remain small presentation presets, not a menu builder.
Calendar enablement and the existing studio dashboard preference remain in the
database; this cleanup does not introduce competing YAML values for them.

Compatibility: older configurations without a workflows section retain their
previous normalized behavior, including the old simple-layout lesson/class
defaults. The loader accepts their workflow flags under navigation as a migration
bridge. Migrate all such flags and `optional_features` together; defining both
sources is rejected even if their values agree. Unsupported keys and invalid
types are rejected, not silently ignored. For legacy non-simple layouts, optional
tools that were not previously restricted remain available.

Capabilities authorize product access; navigation features organize its entry
points; workflows control the supported behavior. None grants membership,
teacher binding, cross-school access or production availability. Optional tools
do not erase existing credits, bookings, guest selections or prices. Saved
booking policies remain authoritative for those commitments. Internal trial
booking is still preview-gated and is not enabled for production by this cleanup.

SBMC and Plushie configurations were not edited. They have no opt-in workflow
section and retain their existing enrollment/studio defaults. No data migration,
new database, alternative booking engine or customer-name conditional was added.

Verification: 806 focused tests passed across policy combinations, teacher access,
enrollment linking, calendars, reservations, SBMC and programs. The policy module
had 100% statement and branch coverage in that run. Manual testing then found a
class-form Cancel destination mismatch; Back and Cancel now share a destination.
The subsequent affected setup/party/policy run passed all 234 tests. These runs
overlap and should not be added together as a unique test count.

Browser checks covered six affected screens at 1440x1000, 390x844, 768x1024 and
1024x768 (24 page/viewport checks), plus validation, Cancel and mobile navigation.
The corrected class-form return was also checked on tablet. Physical Android,
virtual-keyboard and printing checks remain pending. The new full regression is
tracked separately in `school-workflow-policy-20261108/status.json` under the local
visualization evidence directory, with revision, fingerprint and step logs. Its
result must be reviewed before release; earlier passing runs do not verify it.

## Teacher Workspace Cleanup (November 9)

The preceding `school-workflow-policy-20261108` full run passed all steps with an
unchanged source fingerprint. The next iteration improves the existing simple
school teacher workspace; it does not add another scheduling engine or enable
new capabilities for SBMC or Plushie.

- Teachers & availability opens a weekly teacher/day grid, with lesson-type and
  teacher filters. Working hours, booked lessons/hosted visits and time off are
  distinct. This is a staffing overview, not a promise of bookable slots: booking
  eligibility still uses the existing conflict, gap, room and notice checks.
- Teachers and Lesson types use the standard admin tables. Lesson types can be
  added and edited through the existing service form; teacher assignments use
  checkboxes. Back, Cancel and successful saves return to the relevant list.
- The shared teacher editor permits changing gap defaults. Existing series retain
  their saved gap values, and conflict checks continue honoring those snapshots.
  This is a shared lesson-editor behavior change, not a school-specific branch.
- Teacher access links an existing active user account to one teacher. It does
  not create accounts or grant school administration. New bindings still require
  verification; existing role, school, environment and teacher boundaries remain.
- The overview performs bulk reads rather than a query per teacher/day. Invalid
  filters do not broaden results. School-wide time off also appears in a selected
  teacher's detail view. Tablet/phone grids scroll within their own region.

The lead form does not currently collect a trial fee or reserve a teacher slot.
The existing trial flow discovers availability and creates a lesson payment
order, but remains preview-gated; online settlement and connection to lead capture
are not complete. A future paid-trial journey should reuse the current lead and
booking services, hold a valid slot before payment, confirm only after verified
payment, and recover/release failed or expired holds. Do not repurpose the
enrollment application fee as a trial fee or claim this is configuration-only.

Verification evidence for this iteration is in `teacher-workspace-20261109` under
the local visualization directory. Responsive checks cover eight screens on
desktop, phone and both tablet orientations. Physical Android, software keyboard
and printing remain unverified. The subsequent full-suite result is separate
from focused test results and must be reviewed before release.
