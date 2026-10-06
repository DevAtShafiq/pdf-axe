# Office Axe account server

A small FastAPI + SQLite service that the Office Axe desktop app (`cloud_client.py`)
signs in to. It provides:

- **Accounts**: email + password registration and sign-in, with per-device session tokens
  (PBKDF2 password hashes; only token hashes are stored). Sign-in attempts are throttled per
  email and per client IP, sign-out revokes the session on the server, and users can list
  and sign out their other devices.
- **Monthly subscription**: Stripe Checkout to subscribe, the Stripe customer portal
  to cancel or change the card, and a webhook that keeps subscription status in sync.
- **Cloud storage**: a private file area for each subscriber, with a storage quota,
  a maximum upload size, rename/move (files and folders), and trash/restore. Files in the
  trash do not count against the quota. Nothing is ever erased: trashing sets a flag and
  replacing a file keeps the old blob on disk (clean up old blobs yourself if disk space
  matters).
- **Offices (shared workspaces)**: staff of one office share a central drive organised as
  **Country → Program → Student** folders, with roles, invites, an activity log and an
  office-wide monthly plan. See [Offices and the shared drive](#offices-and-the-shared-drive).
- **Live updates**: a Server-Sent Events stream at `/events`. Every signed-in device
  is told straight away when a file or the subscription changes, and every member of an
  office is told when anything in the office's shared drive changes.

Billing is optional. If `STRIPE_SECRET_KEY` and `STRIPE_PRICE_ID` are not set, the
server still runs, `/health` reports `"billing": "none"` and the `/billing/*` routes
return 503.

## Configuration

Settings are read from environment variables. Outside Docker they can also come from
`server/.env`, which is git-ignored, and real environment variables take precedence.
Copy `server/.env.example` and fill it in. That file describes every variable:

| Variable | Default | Purpose |
|---|---|---|
| `SFM_DB_PATH` | `server/data/pdfaxe.sqlite3` | SQLite database |
| `SFM_STORAGE_DIR` | `server/data/storage` | Uploaded files |
| `SFM_PUBLIC_URL` | `http://127.0.0.1:8000` | Public HTTPS URL, used for Stripe return pages |
| `SFM_SESSION_DAYS` | `30` | Sign-in lifetime |
| `SFM_STORAGE_QUOTA_MB` | `5120` | Per-user quota |
| `SFM_MAX_UPLOAD_MB` | `200` | Largest single upload (personal and shared drive) |
| `SFM_OFFICE_QUOTA_MB` | `51200` | Shared-drive quota per office (all members together; trash not counted) |
| `SFM_OFFICE_ACTIVITY_KEEP` | `5000` | Activity-log entries kept per office |
| `SFM_INVITE_DAYS` | `7` | Days an office invite code stays valid |
| `STRIPE_SECRET_KEY` | (none) | Stripe secret API key |
| `STRIPE_WEBHOOK_SECRET` | (none) | Signing secret of the webhook endpoint |
| `STRIPE_PRICE_ID` | (none) | Monthly recurring Price ID |
| `SFM_PLAN_LABEL` | `Monthly plan` | Plan text shown in the app, e.g. `$9.99 / month` |
| `SFM_PAST_DUE_GRACE_DAYS` | `7` | Days paid features keep working after a renewal payment fails (`past_due`) |
| `SFM_FREE_PLAN` | (off) | `1` = every signed-in user gets the paid features without Stripe (self-hosted/team servers) |
| `SFM_LOGIN_MAX_ATTEMPTS` | `8` | Failed sign-ins allowed per email per window, then HTTP 429 |
| `SFM_LOGIN_IP_MAX_ATTEMPTS` | `40` | Failed sign-ins allowed per client IP per window |
| `SFM_LOGIN_WINDOW_MINUTES` | `15` | Length of the sign-in throttling window |
| `SFM_REGISTER_PER_HOUR` | `10` | New accounts allowed per client IP per hour |

## Run locally

Run these from the **repository root**. The app is imported as the `server` package.

```bash
pip install -r server/requirements.txt
cp server/.env.example server/.env      # optional: then edit it
uvicorn server.app:app --reload
```

The server listens on http://127.0.0.1:8000. To check it, open http://127.0.0.1:8000/health.
Interactive API docs are at http://127.0.0.1:8000/docs.

## Run with Docker

Build from the **repository root**, because the image copies the `server/` package:

```bash
docker build -f server/Dockerfile -t pdfaxe-server .
docker run -d --name pdfaxe -p 8000:8000 \
  -v pdfaxe-data:/data \
  --env-file server/.env \
  pdfaxe-server
```

About the image:

- It is based on `python:3.11-slim` and runs as a non-root user (`pdfaxe`).
- `/data` is a `VOLUME`. The image sets `SFM_DB_PATH=/data/pdfaxe.sqlite3` and
  `SFM_STORAGE_DIR=/data/storage`, so the database and every uploaded file live there.
  Mount a persistent volume or disk at `/data`.
- It runs `uvicorn server.app:app --host 0.0.0.0 --port 8000 --proxy-headers` with one worker.
- It has a built-in `HEALTHCHECK` that calls `/health`.
- `server/.env`, `server/data/` and the tests are not copied into the image. Pass settings
  with `--env-file` or `-e`.

## Stripe setup, step by step

1. **Create the product.** In the Stripe Dashboard go to **Product catalog → Add product**.
   Name it (for example "Office Axe Cloud"), then add a **recurring** price with a **monthly**
   billing period. Copy the Price ID (`price_...`) into `STRIPE_PRICE_ID`.
2. **Secret key.** Under **Developers → API keys**, copy the secret key (`sk_test_...` while
   testing, `sk_live_...` in production) into `STRIPE_SECRET_KEY`.
3. **Webhook endpoint.** Under **Developers → Webhooks → Add endpoint**:
   - Endpoint URL: `https://<your-host>/billing/webhook`
   - Events to send:
     - `checkout.session.completed`
     - `customer.subscription.created`
     - `customer.subscription.updated`
     - `customer.subscription.deleted`
     - `invoice.payment_failed` (starts the `past_due` grace period)

   After saving, reveal the **Signing secret** (`whsec_...`) and put it in
   `STRIPE_WEBHOOK_SECRET`.
4. **Customer portal.** Under **Settings → Billing → Customer portal**, activate the portal.
   Allow customers to cancel subscriptions and update payment methods. The app's
   "Manage subscription" button opens this portal.
5. **Public URL.** Set `SFM_PUBLIC_URL` to the server's public HTTPS address, for example
   `https://pdfaxe.example.com`. Stripe sends people back to `/billing/success` or
   `/billing/cancel` on this address.
6. Restart the server. `/health` should now report `"billing": "stripe"`.

**Local testing with the Stripe CLI.** Use test-mode keys and forward webhooks to your
machine:

```bash
stripe login
stripe listen --forward-to localhost:8000/billing/webhook
```

`stripe listen` prints a `whsec_...` secret for this session. Use it as
`STRIPE_WEBHOOK_SECRET` while testing locally. Pay with the test card
`4242 4242 4242 4242`, any future expiry date and any CVC.

## Hosting notes

- **HTTPS is required.** Passwords and session tokens travel in requests, and Stripe
  needs an HTTPS webhook URL. Put the server behind a TLS-terminating reverse proxy or a
  platform that provides HTTPS. Uvicorn runs with `--proxy-headers` so it trusts
  `X-Forwarded-*` headers.
- **Persistent disk.** The SQLite database and uploaded files are stored on local disk
  (`/data` in Docker). Use a persistent volume, and back it up.
- **Run a single instance.** Live events are delivered through an in-process hub, so every
  client must be connected to the same process. Run one instance with one uvicorn worker,
  and do not scale horizontally. SQLite also assumes a single writer host.
- **Do not buffer `/events`.** The reverse proxy must stream Server-Sent Events. The server
  already sends `X-Accel-Buffering: no` and `Cache-Control: no-cache`. On nginx, also set:

  ```nginx
  location /events {
      proxy_pass http://127.0.0.1:8000;
      proxy_http_version 1.1;
      proxy_set_header Connection "";
      proxy_buffering off;
      proxy_cache off;
      proxy_read_timeout 1h;
  }
  ```

  The server sends a heartbeat about every 15 seconds. Idle timeouts must be longer than that.
- **Rate limits are per process.** The sign-in throttle lives in memory, which matches the
  single-instance setup above. Make sure the proxy passes the real client IP
  (`X-Forwarded-For`) so per-IP limits apply to clients, not to the proxy.
- **Upload size.** Allow request bodies at least as large as `SFM_MAX_UPLOAD_MB` in the proxy,
  for example with `client_max_body_size 200m;` on nginx.

## Point the desktop app at the server

Use either of these:

- Set `SFM_SERVER_URL=https://<your-host>` in the desktop app's `.env` file, the one next to
  `main_webview.py` or next to the built EXE.
- Type the address into the **Server address** field on the app's sign-in screen.

`cloud_client.py` is bundled into the desktop build. See `StudentFolderMaker.spec`.

## API

All endpoints except `/health`, `/auth/register`, `/auth/login`, `/billing/webhook` and the
billing return pages need `Authorization: Bearer <token>`. That includes `/events`: a token in
the URL (`?token=`) is rejected with 400, because URLs end up in access logs. Endpoints marked
"subscriber" also need an active (or trialing) subscription, or a `past_due` one still inside
its grace period. Without one they return 402. Sign-in throttling answers 429 with a
`Retry-After` header. Webhook events are processed once (by Stripe event id) and an event older
than the last one applied is ignored.

| Method | Path | Description |
|---|---|---|
| GET  | `/health` | Liveness check plus the active billing provider |
| POST | `/auth/register` | `{email, password, device}` → `{token, user}` |
| POST | `/auth/login` | `{email, password, device}` → `{token, user}` |
| POST | `/auth/logout` | Ends the current session |
| GET  | `/me` | Current user, `office` (`{id, name, role}` or null) and the plan in effect (`status`, `active`, `current_period_end`, `grace_until`, `plan_label`, `billing_available`, `free_plan`, `source`, `office_id`, `can_manage_billing`) |
| GET  | `/auth/sessions` | This user's signed-in devices |
| POST | `/auth/sessions/{id}/revoke` | Sign out one device (its live stream closes) |
| POST | `/billing/checkout` | Returns a Stripe Checkout URL for subscribing (for an office member: the office plan, owner only) |
| POST | `/billing/portal` | Returns a Stripe customer portal URL (the office's for an office owner) |
| POST | `/billing/webhook` | Stripe webhook receiver (verified by signature) |
| GET  | `/billing/success` | Page shown after Checkout or the portal |
| GET  | `/billing/cancel` | Page shown when Checkout is cancelled |
| GET  | `/usage` | `{used, quota, max_upload}` in bytes (trash not counted) |
| GET  | `/files?trashed=false` | List files (subscriber) |
| POST | `/files` | Multipart upload: form `path` plus `file`, optional `base_sha256` (409 if the cloud copy changed since) (subscriber) |
| GET  | `/files/{id}/download` | Download a file (subscriber) |
| POST | `/files/{id}/trash` | Move a file to the trash (subscriber) |
| POST | `/files/{id}/restore` | Restore a file from the trash; 507 if it no longer fits the quota (subscriber) |
| POST | `/files/{id}/move` | `{path}` rename/move one file; 409 if the name is taken (subscriber) |
| POST | `/folders/move` | `{path, new_path}` rename/move a folder and everything in it (subscriber) |
| POST | `/folders/trash` | `{path}` move every file in a folder to the trash (subscriber) |
| GET  | `/events` | Server-Sent Events stream: `hello`, `file_updated`, `file_trashed`, `file_moved`, `folder_moved`, `folder_trashed`, `subscription_updated`, `session_revoked`, `session_expired`, `shared_changed`, `office_changed`, plus `: ping` heartbeats |

## Offices and the shared drive

**Office model.** An office has a name, an owner, settings and its own subscription. A user
belongs to at most one office. Roles:

| Role | Can |
|---|---|
| `owner` | everything; pays for the office plan (Checkout / portal); transfers ownership |
| `admin` | invite, revoke invites, change roles (admin/staff), remove staff, edit settings, create/rename/move/trash Country and Program folders, purge the trash |
| `staff` | create students and work inside them; trash/restore; search; activity |

**Invites.** An owner or admin invites by email (optional) and role. Each invite has a short
code such as `OFX-7K2P-9QD4`, valid for `SFM_INVITE_DAYS` days and usable once. A user joins
with the code, or accepts a pending invite addressed to their email address. Wrong codes are
throttled (10 per 15 minutes per user). The owner cannot leave while other members remain;
they transfer ownership first. When the last member leaves, the office is archived and its
files are kept on the server.

**Billing.** The monthly plan belongs to the office. `/billing/checkout` and `/billing/portal`
called by an office owner act on the office (Stripe metadata carries `office_id`). The
webhook finds the office by `office_id`, then by Stripe customer id, then by subscription id,
and otherwise falls back to the user (the per-user plan). Members get the paid features
(shared drive, personal cloud, AI) while the office plan is active, including the
`past_due` grace period. `/me` → `subscription` shows the plan in effect, with `source`
(`office` or `user`), `office_id` and `can_manage_billing`. A user who is not in an office keeps
the per-user plan exactly as before, and the private "My files" storage is unchanged.
`SFM_FREE_PLAN=1` still unlocks everything for everyone.

**Hierarchy.** In the shared drive, depth 1 is a **Country**, depth 2 a **Program**, depth 3 a
**Student**, and anything deeper belongs to that student. Office settings
(`/office/settings`):

| Key | Default | Meaning |
|---|---|---|
| `countries` | `[]` | Suggested Country names |
| `programs` | `[]` | Program names: a list for every country, or `{"India": [...], "*": [...]}` per country (`*` = all) |
| `student_subfolders` | `[]` | Folders created in every new student (nesting with `/` allowed, e.g. `Offer/Scans`) |
| `enforce_hierarchy` | `true` | Only owner/admin create, rename, move or trash Country and Program folders, and files must be inside a student (depth 4 or deeper) |

**Shared drive rules.** Names are checked so they are valid on Windows (no `< > : " | ? *`,
no reserved names, no trailing dot or space) and unique per folder ignoring case. Every item
records who created and last changed it. Uploads take `on_conflict` = `replace` (default; 409
when `base_sha256` is given and the stored file changed since), `rename` (keep both as
`name (2).ext`) or `error`. Trashing moves an item and everything below it to the office
trash; any member can restore (a clashing name becomes `name (restored)`), and owners/admins
can purge, which erases the content from server storage. Every create, upload, rename, move,
trash, restore and purge is written to the activity log. Content is stored under
`SFM_STORAGE_DIR/office_<id>/` by random blob names.

**Live events.** Every change sends `shared_changed {office_id, paths, actor, action}` to all
connected members; membership, invite, settings and plan changes send
`office_changed {office_id, reason}`.

### Office and shared-drive API

All need a signed-in user. "member" = must be in an office; "manager" = owner or admin;
"paid" = the office (or the user) needs an active plan, otherwise 402.

| Method | Path | Description |
|---|---|---|
| GET  | `/office` | `{office: {id, name, role, member_count, plan, settings, …} or null, pending_invites}` |
| POST | `/office` | `{name}` create; the creator is the owner (409 when already in an office) |
| POST | `/office/rename` | `{name}` (manager) |
| POST | `/office/join` | `{code}` join with an invite code |
| POST | `/office/invites/{id}/accept` · `/decline` | Answer an invite addressed to your email |
| POST | `/office/leave` | Leave (the owner only when alone) |
| POST | `/office/transfer` | `{user_id}` make another member the owner (owner) |
| GET  | `/office/members` | `{members: [{user_id, email, role, joined_at, last_active, is_you}]}` |
| POST | `/office/invites` | `{email?, role}` → `{invite: {id, code, email, role, expires_at, …}}` (manager) |
| GET  | `/office/invites` | Pending invites (manager) |
| POST | `/office/invites/{id}/revoke` | (manager) |
| POST | `/office/members/{user_id}/role` | `{role: admin or staff}` (manager) |
| POST | `/office/members/{user_id}/remove` | (manager; never the owner; only the owner removes admins) |
| GET/POST | `/office/settings` | Read (member) / `{settings: {...}}` partial update (manager) |
| GET  | `/shared/list?path=` | `{path, level, items, breadcrumb, child_level, can_create_folder, can_upload}` (paid) |
| GET  | `/shared/tree?path=` | The item and everything below it (paid) |
| POST | `/shared/mkdir` | `{parent, name}`; a new student gets `student_subfolders` (paid) |
| POST | `/shared/ensure-dir` | `{path}` create a folder and missing parents (paid) |
| POST | `/shared/new-student` | `{country, program, student}` → `{path, created, existed}` (paid) |
| POST | `/shared/upload` | Multipart `path`, `file`, optional `base_sha256`, `on_conflict` (paid) |
| GET  | `/shared/download?path=` | File content (paid) |
| POST | `/shared/rename` | `{path, new_name}` (paid) |
| POST | `/shared/move` | `{paths, dest}` → `{moved, errors}` (paid) |
| POST | `/shared/trash` | `{paths}` → `{trashed, errors}` (paid) |
| GET  | `/shared/trash` | `{items: [{id, name, path, is_dir, deleted_at, deleted_by, count, size}]}` (paid) |
| POST | `/shared/restore` | `{ids}` → `{restored, errors}` (paid) |
| POST | `/shared/purge` | `{ids}` erase from the trash (manager, paid) |
| GET  | `/shared/search?q=` | Name search across the office, up to 200 items with `level` (paid) |
| GET  | `/shared/activity?limit=50` | `{events: [{ts, user, action, path, detail}]}` (member) |
| GET  | `/shared/usage` | `{used, quota, max_upload}` (member) |

Existing databases are upgraded automatically on start (the office tables are created if
missing).

## Tests

From the repository root:

```bash
pip install -r server/requirements.txt pytest httpx
python -m pytest server/tests -q
```

The tests use a fake billing provider and need no Stripe account. GitHub Actions runs them
on every push and pull request (`.github/workflows/tests.yml`).
