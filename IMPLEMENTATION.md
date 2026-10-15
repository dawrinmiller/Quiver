# Quiver profile and application improvements

Implemented biography and optional HTTPS LinkedIn profile links; private resume and
cover-letter uploads, viewing, downloads and deletion; application document
selection; Preparing, Online Interview and On-site Interview statuses; rejection
feedback; instant combined dashboard search/status filtering; unread-only active
notifications; and a dynamic, rule-based Job Search Health Score. Existing account,
event, reminder, recovery, and navigation features remain in place.

## Run on Windows PowerShell

The existing `venv` contains the project's dependencies. From the repository root:

```powershell
.\venv\Scripts\python.exe manage.py migrate
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py test
node --test tracker/test_dashboard_js.cjs
.\venv\Scripts\python.exe manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in. Node is only needed for the optional
JavaScript tests, not for running Quiver. No new dependencies were added.
For a fresh environment, install the existing `requirements.txt` into a Python
environment compatible with the project's pinned Django version.

## Reminder, timezone, and profile corrections

- Reminders now cover future events **today and tomorrow**, including events less
  than 24 hours away on the same day. Past events do not generate new reminders.
  Existing tomorrow-calendar-date coverage is preserved, including events more
  than 24 hours away that fall tomorrow. Reminder wording changes from Tomorrow
  to Today after midnight without duplicating records or resetting read status.
- The default `TIME_ZONE` is now `America/New_York` instead of UTC. Django still
  stores timezone-aware timestamps in UTC and converts them for display. Eastern
  daylight saving time is handled automatically: 03:12 UTC on October 15, 2026
  displays as 11:12 p.m. EDT on October 14. Application creation/update timestamps
  explicitly show EDT or EST. Event forms show the configured timezone and
  interpret entered times in that timezone.
- Restart the Django server to load the setting. An existing `DJANGO_TIME_ZONE`
  environment variable overrides the default; remove it or set it to
  `America/New_York` if it currently specifies UTC. For the current PowerShell:

  ```powershell
  $env:DJANGO_TIME_ZONE = 'America/New_York'
  .\venv\Scripts\python.exe manage.py runserver
  ```

- Existing automatic creation/update timestamps need no data conversion. Manually
  entered event times saved under the old UTC setting may represent the wrong
  instant; edit those events to their intended Eastern time. No bulk shift is
  applied because it could corrupt correctly recorded timestamps.
- The visible profile heading and form label now say **Bio**. The database field
  remains `biography`, preserving saved content without a migration.
- Resume lists on Profile and application details now show **Download** only;
  their View links were removed. Cover-letter View links remain available.
- Reminders are generated on authenticated page requests. Reload a page to pick
  up newly eligible reminders; there is no background delivery or live polling.
- No database migration or new dependency is required for these corrections.
  Changed: `quiver_project/settings.py`, `tracker/notifications.py`, `forms.py`,
  templates `profile_features.html`, `document_list.html`,
  `application_tracker.html`, `event_form.html`, `notifications.html`, and the
  notification tests. Added `tracker/test_reminder_timing.py` for regression tests.
  Verification: all 5 new regression tests passed; the complete Django suite
  passed all 34 tests; `manage.py check` reported no issues and `git diff --check`
  passed. Existing reminder assertions now allow refreshed wording and automatic
  same-day reminder creation.

## Models and migration

- New `Profile`: account one-to-one relationship, optional biography and LinkedIn URL.
- New `Document`: owner, kind, FileField, original filename, upload timestamp.
- Extended `Application`: nullable submission date, expanded choices, resume and
  cover-letter foreign keys, optional rejection feedback and update timestamp,
  and a boolean remembering an observed interview status.
- `0003_application_feedback_updated_at_and_more.py` adds these fields and tables.
  Existing generic `interviewing` values remain supported and are backfilled as
  interview evidence. Offers without evidence do not imply an interview.
- Deleted documents use `SET_NULL`; selecting documents never duplicates files.
  Preparing records stay visible but do not count as submitted applications.
  Non-Preparing forms require a real submission date and reject future dates.

The migration was applied to the local database after an SQLite online backup to
`private_media/backups/before_profile_features.sqlite3`. This directory is ignored
by Git. Never expose it through a web server. Original records were preserved.

## Health Score formula

`tracker/health.py` calculates the score when Profile is requested. No permanent
score is saved. All factors exclude Preparing records and clamp the final result
to 0–100. Ratings: 0–39 Needs Attention; 40–59 Developing; 60–79 Good;
80–100 Excellent. No submitted applications gives zero and a getting-started
recommendation rather than a conclusion about employability.

| Factor | Maximum | Calculation |
| --- | ---: | --- |
| Recent volume | 15 | 15 × min(submissions in the last 28 days / 8, 1) |
| Consistency | 15 | 15 × active trailing seven-day periods / 4 |
| Recency | 10 | 10 × max(0, 1 − days since latest submission / 28) |
| Interview conversion | 25 | 25 × min(((interview applications + 0.75) / (progressed or resolved applications + 5)) / 0.30, 1) |
| Offer conversion | 15 | 15 × min(((interviewed offers + 0.6) / (resolved interviewed applications + 3)) / 0.40, 1) |
| Follow-through | 20 | 20 × fraction with notes, feedback, events, interview status or offer × min(submitted / 5, 1) |

The 40-point activity weight favors behavior users can control. Interview and
offer estimates contribute 40 points together; recorded follow-through contributes
20. Conversion formulas start with neutral prior observations worth half of each
conversion factor, limiting conclusions from tiny samples. Pending replies are
excluded from unsuccessful outcome denominators. An interview is counted once per
application using the remembered status, current interview status, or a past
interview event. Multiple rounds do not add multiple applications. Offers count
toward interview-to-offer conversion only when that evidence exists.

Recommendations are simple independent rules: fewer than five submissions asks
for more data; low recent volume or a gap over two weeks suggests a weekly goal;
at least ten submissions and five progressed/resolved records with an interview
rate below 20% suggests tailoring resumes; at least three resolved interviews
with offer conversion below 25% suggests interview practice; at least two feedback
entries suggests reviewing them; sparse follow-through suggests next steps.
Recent activity plus multiple interviews and an offer earns positive feedback.
No AI or employer-feedback analysis is used.

## Documents, routes, and privacy

Files live under `private_media/documents/<user id>/<random name>.<extension>`.
SQLite stores metadata and paths. `MEDIA_ROOT` and `MEDIA_URL` are configured,
but no public media-serving URL is registered. Do not add unrestricted static
serving for this directory in development or production.

New routes:

- `GET /documents/<id>/`: authenticated owner-only attachment download.
- `GET /documents/<id>/?view=1`: inline PDF viewing; Office formats download.
  This route remains available, but resume lists use only the download link.
- `POST /documents/<id>/delete/`: authenticated owner-only deletion with CSRF.

Existing `/profile/` POST actions handle profile updates and uploads, alongside
the existing email and security-question actions. Existing application creation
and editing URLs now accept document choices, new statuses and optional feedback.
All document querysets are restricted by owner and kind. Downloads use private
no-store responses, nosniff headers and a sandbox policy. Deletion removes the
stored file after the database transaction commits.

Uploads must be nonempty and at most 5 MB. Validation checks the extension and
PDF header/trailer, DOC OLE signature and Word stream marker, or DOCX ZIP/XML
structure. DOCX validation limits expanded size to 25 MB and rejects VBA projects.
This is basic document validation, not antivirus scanning or a full format parser.
Encrypted or unusual documents may fail validation. Open only documents you trust.

Biography and feedback use normal Django escaping. LinkedIn validation allows
HTTPS `linkedin.com` or `www.linkedin.com` `/in/<profile>` links without query
strings or fragments. External links use `noopener noreferrer`.

## Dashboard, feedback, and notifications

All owned application rows load once; `dashboard.js` filters company or role
case-insensitively with partial matches and ANDs the result with the selected
current status. Clearing either control preserves the other. A no-match row
appears when needed. Dashboard totals always describe the whole account, with
all three interview statuses grouped together. There is no pagination.

Feedback lives on Application, is optional for Rejected, and remains editable
when a saved entry exists even after another status is selected. The detail page
shows saved feedback; Profile lists owned entries newest feedback update first.
An empty edit removes feedback. The timestamp changes only when feedback changes.

Notification read actions still store `is_read=True` and redirect to the active
list, which now queries unread records only. The normal page response immediately
removes the read item and updates the navigation count. No AJAX is needed.
Reminder uniqueness remains unchanged. Generation now covers upcoming events
today and tomorrow, with local-time wording that refreshes after midnight.

## Changed files

Created:

- `tracker/document_validation.py`, `tracker/health.py`
- `tracker/migrations/0003_application_feedback_updated_at_and_more.py`
- `tracker/templates/tracker/document_list.html`, `profile_features.html`
- `tracker/static/tracker/js/dashboard.js`, `application_form.js`
- `tracker/test_profile_features.py`, `tracker/test_dashboard_js.cjs`
- `IMPLEMENTATION.md`

Modified:

- `.gitignore`, `quiver_project/settings.py`
- `tracker/models.py`, `forms.py`, `views.py`, `urls.py`
- `tracker/templates/tracker/profile.html`, `dashboard.html`,
  `new_application.html`, `application_tracker.html`
- `tracker/static/tracker/css/base.css`, `dashboard.css`

## Manual test checklist

1. Sign in, open Profile, save a multiline Bio and LinkedIn link. Refresh,
   edit, and clear them. Try an HTTP or unrelated URL and check the error.
2. Upload a resume and cover letter, checking filename/date and download links.
   Confirm resumes have no View link on Profile or application details. Try
   a renamed text file and an upload over 5 MB; confirm validation errors.
3. Create a Preparing application without a date. Select both documents. Change
   to Applied and supply the actual submission date. Edit the document choices,
   including clearing either choice, then check detail-page links.
4. Delete an associated document on Profile. Confirm the application still opens
   and its association is cleared. Sign into a second account and try the first
   account's document URLs; expect 404. Sign out; expect a login redirect.
5. Try all statuses, including legacy Interviewing. Check current-status totals
   and the overall interviewing grouping. Preparing must not affect submitted
   health statistics. Check the health explanation and recommendations.
6. Type company fragments, mixed-case text and job-title fragments on Dashboard.
   Combine with each status. Clear controls separately. Try no matches and
   confirm overall totals stay unchanged.
7. Select Rejected; check that optional feedback appears. Save with no feedback,
   then add, edit and clear feedback. Change status while retaining feedback.
   Check detail and Profile lists, order, and second-account privacy.
8. Create an event 30 minutes from now and another tomorrow, using Eastern time.
   Confirm Today/Tomorrow reminders appear with the intended local times. Check
   the application creation timestamp and event-edit input against your clock.
   Open Notifications and mark a reminder read. Confirm
   disappearance, count decrement, and persistence after refresh. Repeat with
   Mark All as Read. Confirm another account's reminders are unaffected.
9. Exercise existing event editing, deletion, upcoming dates, email updates,
   security-question recovery, password changes, and logout.

## Verification and limitations

Automated verification covers authentication/ownership, CSRF, profile persistence,
invalid uploads, private downloads, document deletion and safe associations,
statuses/dates, feedback, reminders, score samples and recommendations. Node's
built-in runner exercises actual JavaScript for partial/case-insensitive search,
combined/cleared filters, no matches, feedback visibility and required dates.
Executed successfully: `manage.py check` (no issues), `manage.py test` (34 tests),
and `node --test tracker/test_dashboard_js.cjs` (2 tests). After the final
read-only profile-visit adjustment, all 11 feature tests passed again.
`manage.py makemigrations --check --dry-run` reported no changes.
`git diff --check` passed. The migrated database retained its original 2 users,
4 applications, 3 events, and 2 notifications with the original record IDs.

No browser visual review was performed. Instant filtering requires JavaScript;
ordinary form validation and private file access remain server-side. Scores are
estimates and cannot reconstruct unrecorded historical interviews. Deleting an
interview event removes that event evidence unless an interview status was also
recorded. Bulk ORM updates bypass Application.save(), so future bulk status tools
must set interview history explicitly. Document removal through the Profile UI
cleans up files; direct database/admin account deletion does not currently clean
up orphaned stored files. No commits, pushes or merges were performed.
