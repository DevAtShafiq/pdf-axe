# Office Axe Admin dashboard

A small app for the owner's PC that makes license keys: free review keys for
employees and reviewers, trials, and full licences. It's never shipped with
Office Axe.

```bash
python dashboard/admin_app.py
```

From the window you can:
- make a key (type, name, days, devices, plan shown in the app, private note);
- copy the key, or copy a ready-to-send message with instructions;
- see every key you've made, with its status (active / expired / revoked) and expiry;
- search and filter the list, revoke or restore a key;
- export the list to CSV;
- write the revoked list for the next app build.

The same actions work from the command line (`python dashboard/admin_core.py --help`):

```bash
python dashboard/admin_core.py new --type review --name "Rahim" --days 30
```

## How keys work

- Each key holds its own details: id, type, holder name, expiry, plan and
  number of devices. It also carries an **Ed25519 signature** made with your
  private key.
- Office Axe has only the matching **public key** (`PUBLIC_KEY_HEX` in
  `license_keys.py`). It checks keys offline on any PC. A key that was edited,
  typed wrong, or signed by someone else is rejected.
- The user opens Office Axe, chooses **Activate with license key**, pastes the
  key and clicks **Activate on this PC**.

## The private key — keep it safe

The first run (or `admin_core.py init`) creates
`%USERPROFILE%\OfficeAxeAdmin\signing_key.json`. The list of issued keys
(`issued_keys.json`) is kept in the same folder. To use a different folder,
set `OFFICEAXE_ADMIN_DIR`.

- **Never** commit, email or share `signing_key.json`. Anyone who has it can
  make valid keys.
- **Back up** the folder. Without the private key you can't make new keys for
  the app you've already given out. A new signing key means a new public key
  in `license_keys.py` and an app update.
- An existing signing key is never overwritten.

## Limits without a server

| | What happens today |
|---|---|
| Device limit | Counted only on each PC. A key pasted on several PCs works on each of them. |
| Revoking | Works only after an app update. Click **Write revoked list**, which saves `revoked_keys.json` (ids only) in the project, then rebuild. PCs that don't update keep working until the key expires. |
| Expiry | Checked on each PC against its own clock. |

Because of this, giveaway keys default to **30 days**, so they run out on
their own. A license server can lift these limits later; the key format
won't need to change.
