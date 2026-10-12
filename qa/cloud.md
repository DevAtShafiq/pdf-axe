# QA: accounts, cloud and Office sharing

Scope: `server/` (app.py, office.py, billing.py, db.py, config.py, paths.py), `cloud_client.py`,
`cloud_bridge.py`, `office_bridge.py`, `secure_store.py`, `ui/js/account.js`, `ui/js/office.js`.

How to test:
- pytest: `PYTHONUTF8=1 python -m pytest -q -p no:cacheprovider tests server/tests`
  (QA regressions are in `server/tests/test_qa_cloud.py` and `server/tests/test_qa_bridge.py`)
- live API: `python qa/e2e/api_smoke.py http://127.0.0.1:8045` (server with `SFM_FREE_PLAN=1`, 57 checks)
  and `python qa/e2e/api_smoke.py http://127.0.0.1:8046 --paid` (server without it, 32 checks, gating)
- UI: two app windows (`--remote-debugging-port` 9345 and 9346, separate working copies so each has
  its own `sfm_settings.json`), then `python qa/e2e/ui_office.py 9345 9346 <settingsA> <settingsB>` (32 checks)
- Stripe is never called: billing is not configured (MOCK); `FakeBilling` in tests.

Legend: **PY** = pytest, **API** = api_smoke.py, **UI** = ui_office.py, **R** = read/reviewed only.
Result: PASS, FIXED (bug found and fixed, commit), or NOTE.

## Server: accounts, billing, files (server/app.py, db.py, paths.py, config.py, billing.py)

| Function / route | How tested | Result |
|---|---|---|
| hash_password / verify_password / _DUMMY_HASH | PY | PASS |
| RateLimiter (login per email/IP, register per IP) | PY | PASS |
| EventHub publish/subscribe, GET /events (SSE, header auth, revocation) | PY, UI (live updates) | PASS |
| GET /health | PY, API | PASS |
| POST /auth/register (validation, duplicate, length) | PY, API, UI | PASS |
| POST /auth/login (wrong pw, case-insensitive email, throttling) | PY, API, UI | PASS |
| POST /auth/logout | PY, API, UI | PASS |
| GET /auth/sessions, POST /auth/sessions/{id}/revoke | PY, API, UI | PASS |
| GET /me, _user_json, _sub_json | PY, API | PASS |
| _is_active / _row_active / _grace_until (free plan, office plan, past_due) | PY, API --paid | PASS |
| POST /billing/checkout, /billing/portal (MOCK, 400/503) | PY, API | PASS |
| POST /billing/webhook, _apply_update, _apply_office_update (idempotent, stale) | PY | PASS |
| StripeProvider.parse_webhook, _from_subscription, _from_failed_invoice | PY (no network) | PASS |
| GET /billing/success, /billing/cancel | R | PASS |
| GET /usage | PY, API | PASS |
| GET /files, POST /files (quota, size limit, base_sha) | PY, API | PASS |
| GET /files/{id}/download | PY, API | PASS |
| POST /files/{id}/move, /trash, /restore | PY, API | PASS |
| POST /folders/move | PY, API | FIXED 9f7de1c (case-insensitive match took other folder; false clash moving a/a → a) |
| POST /folders/trash | PY, API | FIXED 9f7de1c (trashing "Work" also trashed "work/…") |
| Database.files_under / move_many | PY | FIXED 9f7de1c |
| Database (users, sessions, offices, webhook events) | PY | PASS |
| clean_remote_path / check_name / clean_name / optional_path | PY, API | PASS |
| upload-size middleware (413 before body) | PY | PASS |
| Settings (env + .env) | PY | PASS |

## Server: offices and shared drive (server/office.py)

| Function / route | How tested | Result |
|---|---|---|
| new_invite_code / normalise_code | PY, API | PASS |
| load_settings / validate_settings / _name_list / office_name | PY, API | PASS |
| GET /office, POST /office, POST /office/rename | PY, API, UI | PASS |
| POST /office/join | PY, API, UI | FIXED 31afbb3 (code of an email invite let any account join with that role) |
| POST /office/invites/{id}/accept, /decline | PY, API, UI | PASS |
| POST /office/leave (owner blocked, last member archives) | PY, API | PASS |
| POST /office/transfer | PY, API | PASS |
| GET /office/members | PY, API, UI | PASS |
| POST /office/invites, GET /office/invites, /revoke | PY, API, UI | PASS |
| POST /office/members/{id}/role | PY, API | FIXED 31afbb3 (admin could demote another admin, then remove them) |
| POST /office/members/{id}/remove | PY, API | PASS |
| GET/POST /office/settings | PY, API, UI | PASS |
| GET /shared/list, /shared/tree | PY, API, UI | PASS |
| POST /shared/mkdir, /ensure-dir, /new-student (hierarchy, subfolders) | PY, API, UI | PASS |
| POST /shared/upload (conflict modes, depth rule, quota) | PY, API, UI | PASS |
| GET /shared/download | PY, API | PASS |
| POST /shared/rename, /shared/move (staff limits, into-itself) | PY, API | PASS |
| POST /shared/trash, GET /shared/trash, POST /shared/restore | PY, API, UI | PASS |
| POST /shared/purge (server-side erase, owner/admin) | PY only (not run by hand: erases data) | PASS |
| GET /shared/search, /shared/activity (capped), /shared/usage | PY, API | PASS |
| per-office isolation, path traversal | PY | PASS |

## Desktop client (cloud_client.py, secure_store.py)

| Function | How tested | Result |
|---|---|---|
| CloudClient._open / _json | PY, API | FIXED f8e97b8 (web page / non-HTTP service / bad address raised raw ValueError / http.client errors) |
| register/login/logout/me/health/sessions/revoke_session | API | PASS |
| checkout_url/portal_url | API | PASS |
| usage/list_files/upload/_upload_multipart (unicode names)/download/_download_to | PY, API | PASS |
| move/move_folder/trash/restore/trash_folder | API | PASS |
| office_* (15 methods) | API | PASS |
| shared_* (16 methods) | API | PASS |
| unique_path / place_without_overwrite | PY, API | PASS |
| EventStream (reconnect, session_expired) | PY, UI | PASS |
| SyncFolder (run_once, conflict, _to_review, mass-deletion hold), SyncWorker | PY | PASS |
| conflict_name / move_to_review / sha256_file / fmt_mb | PY | PASS |
| secure_store.protect / unprotect / is_encrypted (DPAPI) | PY, UI (dpapi: in settings) | PASS |

## Bridge (cloud_bridge.py, office_bridge.py)

| Method | How tested | Result |
|---|---|---|
| _cloud_server_url / _cloud_token (legacy migration) / _cloud_set_token (remember on/off) | PY, UI | PASS |
| _cloud_err (401/402/429/5xx/offline) | PY | FIXED f8e97b8 (clear "not an Office Axe server" message) |
| _cloud_is_active (offline trust window) / _require_plan | PY | PASS |
| _cloud_on_event / _cloud_on_status / stream + sync services | PY, UI | PASS |
| account_get_state | PY, UI | FIXED f8e97b8 (wrong server address gave a raw JSON error) |
| account_set_server | PY | FIXED f8e97b8 (LAN names / 172.16-31.x / .local defaulted to https) |
| account_register / account_login / account_logout | PY, UI | PASS |
| account_list_devices / account_revoke_device | PY, UI | PASS |
| billing_open_checkout / billing_open_portal | PY (browser stubbed) | PASS |
| cloud_list / cloud_usage / cloud_upload / cloud_upload_cancel / cloud_download | PY | PASS |
| cloud_trash / cloud_restore / cloud_move / cloud_move_folder / cloud_trash_folder | PY | PASS |
| cloud_pick_files / cloud_enable_drop | R (native dialog / Explorer drop) | NOTE: not automatable |
| cloud_sync_start / stop / pause / resume / now / status | PY | PASS |
| office_* bridge methods (16) | PY, UI | PASS |
| shared_* bridge methods incl. upload/download jobs, shared_cancel, shared_open | PY, UI | PASS |

## UI (account.js, office.js, bridge.js)

| Feature | How tested | Result |
|---|---|---|
| Sign-in overlay: tabs, field validation, duplicate email, wrong password, keep-signed-in | UI | PASS |
| Account panel, devices, sign out / sign in again | UI | PASS |
| Card form (MOCK billing, last 4 digits only) | R | PASS |
| Cloud panel (My files) list/rename/trash/restore/upload/download | R + bridge PY | PASS |
| Office onboarding: create, setup wizard (countries/programs/subfolders/invites) | UI | PASS |
| Invitation accept, staff permissions in toolbar, live member count | UI | PASS |
| New student dialog, upload into student, owner sees it live | UI | PASS |
| Upload/drop permission (canUploadInto) | UI | FIXED 12418fa (admins were offered uploads into Country/Program folders, which always fail) |
| Transfers: cancel | R | FIXED 12418fa (no Cancel; cancelled jobs toasted as failures) |
| Trash restore toast | UI | FIXED 12418fa (count ignored failures/renames) |
| Per-country programs with '*' list | R | FIXED 12418fa |
| Owner alone can leave | R | FIXED 12418fa |
| Upload done toast | UI | FIXED d43dd3a (showed the path list instead of a count) |
| People & settings: members, roles, invites | UI | PASS |
