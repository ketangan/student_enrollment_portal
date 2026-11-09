# School Workspace Cleanup: November 9, 2026

## Scope

Uses the existing `scheduling.navigation.simplified` presentation setting.
The Enrollment Request Demo opts in; SBMC and Plushie's configurations were not
changed. No customer-name branches, new workflows, migrations or payment changes.

- Session and camp details keep Schedule active and return to Schedule.
- Converted leads have one View application action beside the heading.
- Contact editing, account metadata, family-status tools and historical visit
  information use expandable sections; data and authorized actions remain available.
- Follow-up controls show the date and Save instead of several shortcut buttons.
- Teacher login configuration is optional and collapsed, with errors opening it.
  Single-location presentation omits travel-gap editing without resetting its value.
- Rescheduling starts with date, time and optional note; teacher/room remain
  available. Existing conflict checks and confirmation remain authoritative.
- Staff trial booking has one date picker, available times and contact fields.
  The public booking flow and its delivery/payment limitations are unchanged.
- Payment/history headings use the shared session hierarchy.

## Shared Correctness Fix

The existing lead update handler now preserves omitted phone, program-interest
and follow-up values. Previously the follow-up mini-form could clear unrelated
fields. Explicit blanks still clear values. This applies to all callers; existing
authorization, audit and field validation are retained. Lead form labels now have
matching control IDs for keyboard and assistive-technology use.

## Verification

Evidence and revision-linked run status are in the task artifact directory:
`workflow-simplicity-20261109/`.

- First focused run: 22 tests passed.
- Expanded run: 1,037 tests passed, including the label and mobile refinements.
  A final 44px minimum-height adjustment to the read-only copy-link inputs is
  verified separately in the browser and final focused run (`final-status.json`).
- Inline-editor JavaScript tests: 7 passed.
- Browser checks: Chromium desktop 1280x900, tablet 768x1024 and 1024x768,
  phone 390x844. Includes opening/closing inline editors, teacher save/reopen,
  follow-up save preserving contact/interest, and blank-note reschedule preview.
- A local-only lead, Harper Example (`harper@example.invalid`), was created to
  check the unconverted flow. No customer email or payment was sent.
- Physical Android Chrome, virtual keyboard and tablet printing are not verified.
- Full regression is a separate background run; consult `status.json` for its
  actual completion and source fingerprint. Do not release on a pending run.
