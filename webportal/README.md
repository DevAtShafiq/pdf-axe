# Office Axe web portal

This is the website for Office Axe and the online home of license keys. It
replaces the desktop dashboard (`dashboard/`). Keep the desktop dashboard as
an offline backup until the portal is live.

| Part | Address | What it does |
|---|---|---|
| Landing page | `/` | What Office Axe does, screenshots, plans (৳999 / ৳9,999), Download button |
| Admin | `/admin` | Log in, make keys, search and filter, copy a key or a ready-to-send message, see the PCs each key is activated on, remove a PC, revoke or restore a key, add notes, export CSV, see activity, import the desktop dashboard's keys |
| License API | `/api/v1/activate`, `check`, `deactivate`, `revoked` | Office Axe asks here when a key is activated, then about once a day |

It uses only the Python standard library. Nothing needs installing to try it.
In production the Docker image adds `waitress` as the web server.

## Try it on this PC

```bash
python webportal/manage.py run
```

1. Open http://127.0.0.1:8090/admin/setup. On this computer only, the first
   visit asks you to create the admin password (at least 10 characters).
2. Log in. The user name is `admin`.
3. Use **Import** to bring in the keys from the desktop dashboard, including
   your employee's key.

Locally, the portal signs keys with the same signing key as the desktop
dashboard (`%USERPROFILE%\OfficeAxeAdmin\signing_key.json`). That means keys
made here activate in the current app. Data such as the database, admin login
and session secret is kept in `webportal/data/`, which git ignores.

## How it protects keys

- Keys use the same signed format as `license_keys.py`, so they also work
  offline.
- Device limit: the portal counts the PCs on each key across every computer.
  A key for 1 PC won't activate on a second PC until you click **Remove PC**.
- Revoke: the app checks about once a day, so a revoked key stops working at
  that PC's next check. Restoring a key works the same way.
- Offline: if a PC can't reach the portal, it keeps working for **7 days**
  after its last successful check. After that it asks the user to connect and
  click **Check again**. A key can also be activated without internet; the PC
  registers itself with the portal the next time it's online.
- Fake servers can't help. Every answer is signed with your signing key and
  echoes a fresh random number from the app.
- Admin login:
  - The password is stored as a PBKDF2 hash.
  - The session cookie is signed, HttpOnly, SameSite=Strict, and Secure in
    production. It lasts 12 hours.
  - Every form carries a CSRF token.
  - After 5 wrong passwords, that IP is locked out for 15 minutes.
  - Changing the password logs out every session.
- Pages send a strict Content-Security-Policy and never show error details.

## Turn it on in the app

The app checks online only when it knows the portal's address. When the portal
is live, set it in `license_client.py`:

```python
SERVER_URL = "https://portal.yourdomain.com"
```

Then rebuild the EXE. You can also test without rebuilding by setting the
environment variable `OFFICEAXE_LICENSE_SERVER`. Until then, keys work fully
offline, as they do today.

## Put it online (cheap VPS + your domain)

You'll need a small Linux VPS (1 GB RAM is enough) with Docker, and a domain
or subdomain whose DNS **A record** points at the VPS's IP.

```bash
git clone <this repo> && cd <repo>/webportal/deploy
cp .env.example .env                    # set OFFICEAXE_DOMAIN, and the download link
mkdir secrets                           # copy signing_key.json here (from your PC, by scp)
docker compose up -d --build            # Caddy gets the HTTPS certificate by itself
docker compose exec portal python webportal/manage.py set-password
docker compose exec portal python webportal/manage.py import-desktop /path/to/issued_keys.json
```

- Copy `signing_key.json` to the server yourself, for example with `scp`.
  Never commit it, email it or paste it anywhere. The
  `webportal/deploy/secrets/` folder is git-ignored.
- On a public server, `/admin/setup` is disabled. Set the password with
  `manage.py set-password`, which never echoes it.
- Back up the `portal-data` Docker volume. It holds the database (keys,
  devices, activity) and the admin login.
- Download button: set `OFFICEAXE_DOWNLOAD_URL` to a link to the installer
  (for example a GitHub release), or put the file in the data volume at
  `downloads/OfficeAxe.zip`.

On cPanel ("Setup Python App"), point the app at `webportal/wsgi.py`, with
`application` as the entry point. Set the same environment variables in
cPanel, including `OFFICEAXE_SIGNING_KEY_FILE`, which should point to a file
outside `public_html`. Also set `OFFICEAXE_COOKIE_SECURE=1`.

## Settings (environment variables)

| Variable | Meaning |
|---|---|
| `OFFICEAXE_PORTAL_DATA` | Data folder (default `webportal/data`) |
| `OFFICEAXE_SIGNING_KEY` / `OFFICEAXE_SIGNING_KEY_FILE` | The private signing key (hex), or a file holding it. The desktop dashboard's `signing_key.json` works. |
| `OFFICEAXE_ADMIN_USER` | Admin user name (default `admin`) |
| `OFFICEAXE_COOKIE_SECURE` | `1` = cookies only over HTTPS (set by the Docker image) |
| `OFFICEAXE_TRUST_PROXY` | `1` = take the visitor's IP from Caddy (set by the Docker image) |
| `OFFICEAXE_DOWNLOAD_URL` / `OFFICEAXE_DOWNLOAD_FILE` | Where the Download button goes |

## Tests

```bash
python -m pytest tests/test_webportal.py tests/test_license_keys.py
```

These cover login, CSRF, the rate limit, key pages, import, the API (device
limit, revoke, expiry, forged keys) and the app's online checks (device limit
across PCs, revocation at the next check, the 7-day offline grace, forged
server answers). The tests never touch the network.

## Deploy on Firebase (Hosting + Cloud Functions 2nd gen + Firestore)

Everything for Firebase is in `webportal/firebase/`.

- **Hosting** serves `/static/**` (pages, images, captions) and `/media/office-axe-demo.mp4`.
- Every other address goes to the **`portal` function**: the same portal code, running on Cloud Run under the hood, in region asia-southeast1.
- **Data** lives in **Firestore** (collections `oa_*`). Locally and in the tests, the portal keeps using SQLite.
- **Secrets** come from **Secret Manager**, never from the code or git:

| Secret | What it is |
|---|---|
| `OFFICEAXE_SIGNING_KEY` | your license signing key (the desktop dashboard's `signing_key.json`) |
| `OFFICEAXE_SESSION_SECRET` | random, signs the admin login cookie |
| `OFFICEAXE_ADMIN_PASSWORD_HASH` | the first admin password (a hash). A changed password is then kept in Firestore. |

One-time setup:

1. In the Firebase console, create the Firestore database in **production mode**, location `asia-southeast1`. The project needs the Blaze plan.
2. Sign in to the Firebase CLI once:
   ```
   firebase login
   ```
3. Copy `.firebaserc.example` to `.firebaserc` and put in your project ID (`firebase projects:list` shows it).
4. Upload the secrets, from `webportal/firebase`:
   ```
   firebase functions:secrets:set OFFICEAXE_SIGNING_KEY --data-file "%USERPROFILE%\OfficeAxeAdmin\signing_key.json"
   python -c "import secrets; print(secrets.token_hex(32))" > %TEMP%\oa_session.txt
   firebase functions:secrets:set OFFICEAXE_SESSION_SECRET --data-file %TEMP%\oa_session.txt
   python ..\manage.py hash-password > %TEMP%\oa_admin_hash.txt
   firebase functions:secrets:set OFFICEAXE_ADMIN_PASSWORD_HASH --data-file %TEMP%\oa_admin_hash.txt
   ```
   (`hash-password` asks for the password twice and doesn't show it. Afterwards, remove the two temp files yourself.)

Each deploy:

```
python webportal/firebase/prepare.py --venv --download-url "https://…/OfficeAxe.zip"
cd webportal/firebase
firebase deploy --only hosting,functions:officeaxe
```

- `prepare.py` builds `webportal/firebase/build/`, which git ignores.
- `firestore.rules` holds strict rules (no browser access). They are **not** in `firebase.json`, because on the OFFICEAXE project the separate release manager uses Firestore from the browser (`releases`, `meta/current`). Only deploy them if nothing else uses Firestore from a browser.
- The Download button follows the release published in that release manager (`meta/current` → `downloadUrl`) unless `OFFICEAXE_DOWNLOAD_URL` is set.
