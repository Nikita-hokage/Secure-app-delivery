"""Rehearse recovery on a synthetic multi-tenant service and its feature store."""

import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from .audit import STAGES, audit
from .contract import parse


TABLES = ("profiles", "events", "features")
ACTORS = {"erase": {"tenant": "north", "subject": "42"},
          "same_tenant": {"tenant": "north", "subject": "73"},
          "other_tenant": {"tenant": "south", "subject": "42"}}
CONTRACT = {"version": 1, "actors": ACTORS,
            "tables": [{"table": name, "tenant_column": "tenant", "subject_column": "subject"}
                       for name in TABLES]}
SCENARIOS = {
    "clean": ("Корректное восстановление", "Журнал удалений применяется к профилям, событиям и ML-признакам."),
    "stale-features": ("Забытые ML-признаки", "При восстановлении обработчик пропускает таблицу признаков."),
    "tenant-collision": ("Совпадение ID у клиентов", "Обработчик ищет профиль по локальному ID без учёта клиента."),
    "peer-rewrite": ("Тихая перезапись", "Число записей у соседа сохраняется, но значение ML-признака меняется."),
    "retry-drift": ("Ошибка при повторе", "Если основной профиль уже исчез, обработчик ошибочно расширяет поиск."),
}


def seed(db):
    db.executescript("""
        CREATE TABLE profiles(tenant TEXT, subject TEXT, nickname TEXT, PRIMARY KEY(tenant, subject));
        CREATE TABLE events(tenant TEXT, subject TEXT, seq INTEGER, kind TEXT);
        CREATE TABLE features(tenant TEXT, subject TEXT, feature TEXT, value REAL);
    """)
    for i, actor in enumerate(ACTORS.values(), start=1):
        identity = actor["tenant"], actor["subject"]
        db.execute("INSERT INTO profiles VALUES(?, ?, ?)", (*identity, f"fixture-{i}"))
        db.executemany("INSERT INTO events VALUES(?, ?, ?, ?)",
                       [(*identity, 1, "open"), (*identity, 2, "close")])
        db.execute("INSERT INTO features VALUES(?, ?, 'score', ?)", (*identity, i / 8))
    db.commit()


def replay(db, scenario, ledger):
    with db:
        for tenant, subject in ledger:
            profile = db.execute("SELECT 1 FROM profiles WHERE tenant=? AND subject=?",
                                 (tenant, subject)).fetchone()
            for table in TABLES:
                if scenario == "stale-features" and table == "features":
                    continue
                if scenario == "tenant-collision" or (scenario == "retry-drift" and not profile):
                    db.execute(f"DELETE FROM {table} WHERE subject=?", (subject,))
                else:
                    db.execute(f"DELETE FROM {table} WHERE tenant=? AND subject=?", (tenant, subject))
            if scenario == "peer-rewrite":
                db.execute("UPDATE features SET value=0 WHERE tenant=? AND subject<>?", (tenant, subject))


def save(db, path):
    with closing(sqlite3.connect(path)) as target:
        db.backup(target)


def write_fixture(directory, scenario):
    # Caller owns this empty directory. Run a real restore from the pre-erasure backup.
    paths = [directory / f"{stage}.sqlite" for stage in STAGES]
    with closing(sqlite3.connect(":memory:")) as live:
        seed(live)
        save(live, paths[0])
        ledger = [(ACTORS["erase"]["tenant"], ACTORS["erase"]["subject"])]
        replay(live, "clean", ledger)
        with closing(sqlite3.connect(paths[0].as_uri() + "?mode=ro", uri=True)) as backup:
            backup.backup(live)
        for path in paths[1:]:
            replay(live, scenario, ledger)
            save(live, path)
    (directory / "contract.json").write_text(json.dumps(CONTRACT, indent=2) + "\n", encoding="utf-8")
    return paths


def rehearse(scenario):
    if scenario not in SCENARIOS:
        raise ValueError("unknown rehearsal scenario")
    with tempfile.TemporaryDirectory(prefix="restorecheck-") as tmp:
        paths = write_fixture(Path(tmp).resolve(), scenario)
        report = audit(paths, parse(CONTRACT))
    report["scenario"] = scenario
    return report
