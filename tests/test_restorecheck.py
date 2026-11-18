import copy
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from app import app
from restorecheck.__main__ import main
from restorecheck.audit import audit, row_digest
from restorecheck.contract import DrillError, load, parse
from restorecheck.demo import CONTRACT, rehearse, write_fixture


class DrillTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.paths = write_fixture(self.root, "clean")
        self.contract = parse(CONTRACT)

    def change(self, index, sql):
        with closing(sqlite3.connect(self.paths[index])) as db:
            db.executescript(sql)

    def run_audit(self):
        return audit(self.paths, self.contract)

    def test_clean_and_read_only(self):
        def state():
            return {p.name: hashlib.sha256(p.read_bytes()).digest() for p in self.root.iterdir()}
        initial = state()
        for p in self.paths:
            p.chmod(0o444)
        self.assertEqual(self.run_audit()["failed"], 0)
        self.assertEqual(state(), initial)

    def test_scenarios(self):
        cases = {
            "clean": (0, set()),
            "stale-features": (2, {"ERASED_ROWS_PRESENT"}),
            "tenant-collision": (6, {"CONTROL_MISSING"}),
            "peer-rewrite": (2, {"CONTROL_CHANGED"}),
            "retry-drift": (6, {"CONTROL_MISSING", "REPLAY_DRIFT"}),
        }
        for scenario, (count, codes) in cases.items():
            with self.subTest(scenario=scenario):
                r = rehearse(scenario)
                self.assertEqual(r["total"], 27)
                self.assertEqual(r["failed"], count)
                self.assertEqual({c["code"] for c in r["checks"]} - {"OK"}, codes)

    def test_same_count_not_same_content(self):
        report = rehearse("peer-rewrite")
        c = next(c for c in report["checks"] if c["code"] == "CONTROL_CHANGED")
        self.assertEqual((c["expected"], c["actual"], c["removed"], c["added"]), (1, 1, 1, 1))
        self.assertEqual((c["table"], c["role"]), ("features", "same_tenant"))

    def test_report_withholds_values_and_ids(self):
        raw = json.dumps(rehearse("tenant-collision"))
        for sensitive in ("fixture-1", "north", "south", '"42"', '"73"'):
            self.assertNotIn(sensitive, raw)

    def test_missing_baseline_witness_inconclusive(self):
        self.change(0, "DELETE FROM events WHERE tenant='south'")
        with self.assertRaisesRegex(DrillError, "events/other_tenant"):
            self.run_audit()

    def test_schema_change_is_inconclusive(self):
        self.change(1, "ALTER TABLE events ADD COLUMN changed")
        with self.assertRaisesRegex(DrillError, "layout"):
            self.run_audit()

    def test_duplicate_rows_count(self):
        self.change(1, "INSERT INTO events SELECT * FROM events WHERE tenant='south'")
        result = self.run_audit()
        checks = [c for c in result["checks"] if c["code"] == "CONTROL_CHANGED"]
        self.assertEqual([(c["actual"], c["added"]) for c in checks], [(4, 2)])

    def test_reordering_rows_is_ok(self):
        self.change(1, """
            CREATE TEMP TABLE copy AS SELECT * FROM events ORDER BY seq DESC;
            DELETE FROM events;
            INSERT INTO events SELECT * FROM copy;
        """)
        self.assertEqual(self.run_audit()["status"], "pass")

    def test_types_and_boundaries_do_not_collide(self):
        samples = [[None], [""], [b""], [1], [1.0], ["1"], ["ab", "c"], ["a", "bc"]]
        self.assertEqual(len({row_digest(row) for row in samples}), len(samples))

    def test_rejects_live_wal(self):
        Path(str(self.paths[0]) + "-wal").write_bytes(b"not a standalone snapshot")
        with self.assertRaisesRegex(DrillError, "live journals"):
            self.run_audit()

    def test_rejects_same_file_via_link(self):
        alias = self.root / "other.sqlite"
        os.link(self.paths[1], alias)
        with self.assertRaisesRegex(DrillError, "independently captured"):
            audit([self.paths[0], self.paths[1], alias], self.contract)

    def test_missing_file_is_never_created(self):
        missing = self.root / "missing.sqlite"
        with self.assertRaises(FileNotFoundError):
            audit([self.paths[0], missing, self.paths[2]], self.contract)
        self.assertFalse(missing.exists())

    def test_view_and_unknown_columns_rejected(self):
        self.change(0, "ALTER TABLE events RENAME TO events_v1; CREATE VIEW events AS SELECT * FROM events_v1")
        with self.assertRaisesRegex(DrillError, "ordinary user table"):
            self.run_audit()
        data = copy.deepcopy(CONTRACT)
        data["tables"][0]["tenant_column"] = "missing"
        with self.assertRaisesRegex(DrillError, "identity column"):
            audit(self.paths, parse(data))

    def test_contract_validates_controls(self):
        for role, field, value in [("other_tenant", "tenant", "north"), ("same_tenant", "subject", "42"),
                                   ("erase", "subject", True), ("other_tenant", "subject", 42)]:
            data = copy.deepcopy(CONTRACT)
            data["actors"][role][field] = value
            with self.subTest(role=role, field=field, value=value), self.assertRaises(DrillError):
                parse(data)

    def test_contract_rejects_ambiguous_probes(self):
        for mutation in [lambda d: d["tables"].append(d["tables"][0]),
                         lambda d: d["tables"][0].update(table="unknown table"),
                         lambda d: d["tables"][0].update(subject_column="tenant")]:
            data = copy.deepcopy(CONTRACT)
            mutation(data)
            with self.assertRaises(DrillError):
                parse(data)
        bad = self.root / "duplicate.json"
        bad.write_text('{"version": 0, "version": 1}')
        with self.assertRaisesRegex(DrillError, "duplicate"):
            load(bad)

    def test_custom_schema_integer_ids_and_blob_content(self):
        data = copy.deepcopy(CONTRACT)
        for actor in data["actors"].values():
            actor["subject"] = int(actor["subject"])
        data["tables"] = [{"table": "accounts", "tenant_column": "org", "subject_column": "id"}]
        paths = [self.root / f"custom-{i} #?.db" for i in range(3)]
        for i, path in enumerate(paths):
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE accounts(org TEXT COLLATE NOCASE, id INTEGER, payload BLOB)")
                for role, actor in data["actors"].items():
                    if i == 0 or role != "erase":
                        db.execute("INSERT INTO accounts VALUES(?,?,?)", (actor["tenant"], actor["subject"], b"\x00A"))
                db.execute("INSERT INTO accounts VALUES('NORTH',42,NULL)")
        self.assertEqual(audit(paths, parse(data))["status"], "pass")

    def test_cli_and_new_directory_only(self):
        dest = self.root / "new"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["fixtures", str(dest), "--scenario", "stale-features"]), 0)
        existing = (dest / "before.sqlite").read_bytes()
        again = subprocess.run([sys.executable, "-m", "restorecheck", "fixtures", str(dest)], cwd=APP, capture_output=True)
        self.assertEqual(again.returncode, 2)
        self.assertEqual((dest / "before.sqlite").read_bytes(), existing)
        args = ["--contract", str(dest / "contract.json")]
        for stage in ("before", "restored", "retried"):
            args.extend(["--" + stage, str(dest / (stage + ".sqlite"))])
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["audit", "--json", *args]), 1)
        self.assertEqual(json.loads(out.getvalue())["failed"], 2)
        (dest / "before.sqlite").write_bytes(b"invalid database")
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["audit", "--json", *args]), 2)
        self.assertEqual(json.loads(out.getvalue())["status"], "inconclusive")


class WebTests(unittest.TestCase):
    def request(self, path, method="GET"):
        captured = []
        body = b"".join(app({"REQUEST_METHOD": method, "PATH_INFO": path}, lambda s, h: captured.extend([s, dict(h)])))
        return (*captured, body)

    def test_web_and_health(self):
        self.assertEqual(json.loads(self.request("/health")[2]), {"status": "ok"})
        for path in ("/", "/styles.css", "/app.js", "/api/scenarios"):
            status, headers, body = self.request(path)
            self.assertEqual(status, "200 OK")
            self.assertEqual(int(headers["Content-Length"]), len(body))
            self.assertEqual(self.request(path, "HEAD")[2], b"")

    def test_demo_matches_cli(self):
        r = json.loads(self.request("/api/drills/retry-drift")[2])
        self.assertEqual(r, rehearse("retry-drift"))

    def test_unknown_and_mutations(self):
        self.assertEqual(self.request("/api/drills/nope")[0], "404 Not Found")
        self.assertEqual(self.request("/api/drills/clean", "POST")[0], "405 Method Not Allowed")
        self.assertEqual(self.request("/../app.py")[0], "404 Not Found")


if __name__ == "__main__":
    unittest.main()
