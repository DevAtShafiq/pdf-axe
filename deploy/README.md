# Put the Office Axe server online

This runs the account and Office drive server on one rented Linux server, with
HTTPS, so every staff computer can reach it from anywhere.

## What you need

- **A small cloud server** running Ubuntu 24.04, with 2 GB RAM. Pick its disk
  for your files: 40 GB holds about 30 GB of student documents. Any provider
  works, for example:
  - Hetzner Cloud CX22, about €4 a month;
  - DigitalOcean Basic Droplet, about $6 a month.
- **A web address** for it, such as `files.youragency.com`. You need a domain
  you control, and you add an **A record** for that name pointing to the
  server's IP address.

## Set it up (about 15 minutes)

1. Connect to the server over SSH (`ssh root@SERVER_IP`), then install Docker
   and get the code:

   ```bash
   curl -fsSL https://get.docker.com | sh
   git clone https://github.com/DevAtShafiq/pdf-axe.git /opt/office-axe
   cd /opt/office-axe/deploy
   cp .env.example .env
   nano .env        # set OFFICE_AXE_DOMAIN=files.youragency.com
   ```

2. Start it:

   ```bash
   docker compose up -d --build
   ```

   The HTTPS certificate is fetched automatically on the first visit.

3. Check it from any browser: `https://files.youragency.com/health` should
   show `"ok": true`.

4. In Office Axe on each computer, open **Server settings** on the sign-in screen and enter
   `https://files.youragency.com`, then Save. Then sign in or create an account.

## Keep it safe

- **Backups:** `sh backup.sh` saves the database and every file into
  `deploy/backups/`. To run it every night at 3 AM, add this with `crontab -e`:

  ```
  0 3 * * * cd /opt/office-axe/deploy && sh backup.sh
  ```

  Copy the backups off the server now and then, or turn on your provider's
  server backups.
- **Updates:**

  ```bash
  cd /opt/office-axe && git pull && cd deploy && docker compose up -d --build
  ```

- **Payments:** while `SFM_FREE_PLAN=1`, every signed-in account has the paid
  features. To charge the monthly plan, fill in the three `STRIPE_*` values and
  remove `SFM_FREE_PLAN`, then run `docker compose up -d`. The Stripe webhook
  goes to `https://files.youragency.com/billing/webhook`. See
  `server/README.md` for the Stripe steps.

Only ports 80 and 443 need to be open. The server itself is never exposed
directly.
