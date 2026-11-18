"""Inspect three offline SQLite snapshots without writing to any input."""

import hashlib
import sqlite3
import struct
from collections import Counter
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

from .contract import DrillError, ROLES


STAGES = ("before", "restored", "retried")


@contextmanager
def snapshot(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise DrillError("a snapshot must be a regular SQLite file")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise DrillError("supply standalone snapshots made with SQLite backup, without live journals")
    # immutable=1 is safe only under the documented closed, static input contract.
    uri = path.as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute("PRAGMA trusted_schema = OFF")
        db.execute("PRAGMA query_only = ON")
        yield db


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def columns(db, probe):
    table = db.execute(
        "SELECT type FROM pragma_table_list WHERE schema='main' AND name=?",
        (probe.table,),
    ).fetchone()
    if table != ("table",) or probe.table.lower().startswith("sqlite_"):
        raise DrillError(f"{probe.table}: expected an ordinary user table")
    schema = db.execute(
        "SELECT name, type, hidden FROM pragma_table_xinfo(?) ORDER BY cid", (probe.table,),
    ).fetchall()
    if any(hidden for _, _, hidden in schema):
        raise DrillError(f"{probe.table}: generated and hidden columns are unsupported")
    names = {name for name, _, _ in schema}
    if not {probe.tenant_column, probe.subject_column} <= names:
        raise DrillError(f"{probe.table}: an identity column is missing (names are case-sensitive)")
    return schema


def row_digest(row):
    # Frame each value with its SQLite storage class and byte length.
    h = hashlib.sha256()
    for value in row:
        if value is None:
            tag, raw = b"n", b""
        elif isinstance(value, int):
            tag, raw = b"i", struct.pack(">q", value)
        elif isinstance(value, float):
            tag, raw = b"r", struct.pack(">d", value)
        elif isinstance(value, str):
            tag, raw = b"t", value.encode("utf-8")
        else:
            tag, raw = b"b", value
        h.update(tag + struct.pack(">Q", len(raw)))
        h.update(raw)
    return h.digest()


def observed(db, probe, schema, actor):
    tenant, subject = quoted(probe.tenant_column), quoted(probe.subject_column)
    projection = ", ".join(quoted(name) for name, _, _ in schema)
    sql = (f"SELECT {projection} FROM main.{quoted(probe.table)} "
           f"WHERE {tenant} COLLATE BINARY = ? AND typeof({tenant}) = typeof(?) "
           f"AND {subject} COLLATE BINARY = ? AND typeof({subject}) = typeof(?)")
    a, b = actor
    return Counter(row_digest(row) for row in db.execute(sql, (a, a, b, b)))


def check(dbs, contract):
    checks = []
    for probe in contract.probes:
        schema = columns(dbs[0], probe)
        if any(columns(db, probe) != schema for db in dbs[1:]):
            raise DrillError(f"{probe.table}: column layout or declared types changed between snapshots")
        for role in ROLES:
            actor = contract.actors[role]
            before, restored, retried = [observed(db, probe, schema, actor) for db in dbs]
            if not before:
                raise DrillError(f"{probe.table}/{role}: baseline has no witness rows")
            expected = Counter() if role == "erase" else before
            for phase, actual in (("restored", restored), ("retried", retried)):
                if expected == actual:
                    code = "OK"
                elif role == "erase":
                    code = "ERASED_ROWS_PRESENT"
                elif not actual:
                    code = "CONTROL_MISSING"
                else:
                    code = "CONTROL_CHANGED"
                checks.append(result(probe.table, role, phase, expected, actual, code))
            code = "OK" if restored == retried else "REPLAY_DRIFT"
            checks.append(result(probe.table, role, "repeatability", restored, retried, code))
    failed = sum(check["code"] != "OK" for check in checks)
    return {"version": 1, "status": "fail" if failed else "pass",
            "total": len(checks), "failed": failed, "checks": checks}


def result(table, role, stage, expected, actual, code):
    return {"table": table, "role": role, "stage": stage, "code": code,
            "expected": expected.total(), "actual": actual.total(),
            "removed": (expected - actual).total(), "added": (actual - expected).total()}


def audit(paths, contract):
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise DrillError("SQLite 3.37 or newer is required")
    files = [Path(p).resolve(strict=True) for p in paths]
    if len(files) != 3 or any(a.samefile(b) for i, a in enumerate(files) for b in files[i + 1:]):
        raise DrillError("provide three independently captured snapshot files")
    with ExitStack() as stack:
        dbs = [stack.enter_context(snapshot(p)) for p in files]
        return check(dbs, contract)
