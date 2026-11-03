# -*- coding: utf-8 -*-
"""
minidb.py — a tiny stand-in for the part of google.cloud.firestore the portal
uses, so the release manager works on this PC and in tests without Google.

    db = MiniFirestore("data/minidb.json")   # or MiniFirestore() in memory
    db.collection("releases").document("abc").set({...})

Supports: collection / document / get / set / update / delete / add,
where("f", "==", v), order_by(f, direction), limit(n), stream(), count(),
batch(). Saved to a JSON file after every write when a path is given.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime


def _jsonable(v):
    if isinstance(v, datetime):
        return {"__dt__": v.isoformat()}
    return v


def _from_json(v):
    if isinstance(v, dict) and set(v) == {"__dt__"}:
        return datetime.fromisoformat(v["__dt__"])
    return v


class _Snap:
    def __init__(self, ref, data):
        self.reference, self._d, self.id = ref, data, ref.id

    @property
    def exists(self):
        return self._d is not None

    def to_dict(self):
        return dict(self._d) if self._d is not None else None


class _Doc:
    def __init__(self, coll, id_):
        self.coll, self.id = coll, id_

    def get(self, transaction=None):
        d = self.coll.data.get(self.id)
        return _Snap(self, dict(d) if d is not None else None)

    def set(self, d, merge=False):
        base = dict(self.coll.data.get(self.id) or {}) if merge else {}
        base.update(d)
        self.coll.data[self.id] = base
        self.coll.db._save()

    def update(self, d):
        if self.id not in self.coll.data:
            raise KeyError(f"No document to update: {self.coll.name}/{self.id}")
        self.coll.data[self.id].update(d)
        self.coll.db._save()

    def delete(self):
        self.coll.data.pop(self.id, None)
        self.coll.db._save()


class _Query:
    def __init__(self, coll, filters=(), order=None, lim=None):
        self.coll, self.filters, self.order, self.lim = coll, list(filters), order, lim

    def where(self, field, op, value):
        if op != "==":
            raise ValueError("only == filters are supported")
        return _Query(self.coll, self.filters + [(field, value)], self.order, self.lim)

    def order_by(self, field, direction="ASCENDING"):
        return _Query(self.coll, self.filters, (field, direction), self.lim)

    def limit(self, n):
        return _Query(self.coll, self.filters, self.order, n)

    def stream(self):
        rows = [(k, v) for k, v in list(self.coll.data.items())
                if all(v.get(f) == x for f, x in self.filters)]
        if self.order:
            f, d = self.order
            key = lambda kv: (kv[1].get(f) is None, str(kv[1].get(f)) if not isinstance(kv[1].get(f), (int, float))
                              else kv[1].get(f))
            rows.sort(key=key, reverse=(d == "DESCENDING"))
        if self.lim is not None:
            rows = rows[:self.lim]
        return [_Snap(_Doc(self.coll, k), dict(v)) for k, v in rows]

    def count(self):
        n = len(self.stream())

        class _Agg:
            def get(self_inner):
                return [[type("R", (), {"value": n})()]]
        return _Agg()


class _Coll(_Query):
    def __init__(self, db, name, data):
        self.db, self.name, self.data = db, name, data
        super().__init__(self)

    def document(self, id_=None):
        return _Doc(self, id_ or secrets.token_hex(10))

    def add(self, d):
        ref = self.document()
        ref.set(d)
        return None, ref


class _Batch:
    def __init__(self, db):
        self.db, self.ops = db, []

    def set(self, ref, d, merge=False):
        self.ops.append(lambda: ref.set(d, merge=merge))

    def update(self, ref, d):
        self.ops.append(lambda: ref.update(d))

    def delete(self, ref):
        self.ops.append(ref.delete)

    def commit(self):
        with self.db._lock:
            for op in self.ops:
                op()


class MiniFirestore:
    def __init__(self, path: str = ""):
        self.path = path
        self._lock = threading.RLock()
        self._colls: dict = {}
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self._colls = {c: {k: {f: _from_json(v) for f, v in doc.items()} for k, doc in docs.items()}
                           for c, docs in raw.items()}

    def collection(self, name: str):
        return _Coll(self, name, self._colls.setdefault(name, {}))

    def batch(self):
        return _Batch(self)

    def _save(self):
        if not self.path:
            return
        with self._lock:
            raw = {c: {k: {f: _jsonable(v) for f, v in doc.items()} for k, doc in docs.items()}
                   for c, docs in self._colls.items()}
            os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
