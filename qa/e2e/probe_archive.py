"""Edge-case probe for archive_tools: partial files after cancel / damaged member.

Usage:  PYTHONUTF8=1 python qa/e2e/probe_archive.py <empty scratch dir>
"""
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import archive_tools as at  # noqa: E402

D = sys.argv[1]
os.makedirs(D, exist_ok=True)


def tree(d):
    out = []
    for r, ds, fs in os.walk(d):
        for f in fs:
            p = os.path.join(r, f)
            out.append((os.path.relpath(p, D), os.path.getsize(p)))
    return sorted(out)


# 1. cancel in the middle of a big member
big = os.path.join(D, "big.bin")
with open(big, "wb") as f:
    f.write(os.urandom(3 * 1024 * 1024))
z = at.zip_paths([big], os.path.join(D, "big.zip"), compression="store")["out_path"]
calls = [0]


def cancel():
    calls[0] += 1
    return calls[0] > 3


try:
    at.extract_zip(z, os.path.join(D, "out_cancel"), cancel=cancel)
except at.ArchiveError as e:
    print("cancel:", e.code, e)
print("after cancel:", tree(os.path.join(D, "out_cancel")))

# 2. damaged member (CRC mismatch)
dz = os.path.join(D, "damaged.zip")
with zipfile.ZipFile(dz, "w", zipfile.ZIP_STORED) as zf:
    zf.writestr("good.txt", b"ok")
    zf.writestr("bad.txt", b"A" * 5000)
data = bytearray(open(dz, "rb").read())
i = data.find(b"A" * 100)
data[i + 10] = ord("B")
open(dz, "wb").write(bytes(data))
try:
    at.extract_zip(dz, os.path.join(D, "out_damaged"))
except at.ArchiveError as e:
    print("damaged:", e.code, e)
print("after damaged:", tree(os.path.join(D, "out_damaged")))

# 3. AES password zip, wrong password, set/remove
pz = at.zip_paths([big], os.path.join(D, "pw.zip"), password="secret1")["out_path"]
for pw in (None, "nope", "secret1"):
    try:
        r = at.extract_zip(pz, os.path.join(D, "out_pw"), password=pw)
        print("pw", pw, "->", r["files"])
    except at.ArchiveError as e:
        print("pw", pw, "->", e.code)
print(at.zip_set_password(pz, "new2", old_password="secret1")["out_path"])
print(at.zip_remove_password(pz, "secret1")["out_path"])
print(at.zip_remove_password(pz, "secret1")["out_path"])
