"""Regressions for literal searches, run counters, and option catalog parsing."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from gallery_dl_app.core import parse_text_database
from gallery_dl_app.feature_store import FeatureStore
from gallery_dl_app.reports import ReportsMixin


class FeatureStoreBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = FeatureStore(Path(self.folder.name) / "features.sqlite3")

    def test_history_search_treats_sql_wildcards_as_literal_text(self):
        self.store.record_history({"url": "https://example.com/one", "status": "done"})
        self.store.record_history({"url": "https://example.com/percent%item", "status": "done"})
        self.assertEqual(
            [row["url"] for row in self.store.list_history(search="%")],
            ["https://example.com/percent%item"],
        )

    def test_library_search_treats_sql_wildcards_as_literal_text(self):
        self.store.add_library_entry(url="https://example.com/one")
        self.store.add_library_entry(url="https://example.com/under_score")
        self.assertEqual(
            [row["url"] for row in self.store.list_library(search="_")],
            ["https://example.com/under_score"],
        )

    def test_run_and_library_counters_are_sqlite_safe(self):
        jobs = parse_text_database("https://example.com/one")
        self.store.add_library_entry(url=jobs[0].url)
        run_id = self.store.begin_run(jobs, [0])
        self.store.mark_run_item(run_id, 0, "done", downloaded=1 << 80, skipped=1 << 80, return_code=1 << 80)
        self.store.update_library_result(jobs[0].url, "done", 1 << 80)
        self.assertEqual(self.store.list_library()[0]["last_downloaded"], (1 << 63) - 1)
        with self.store._connect() as db:
            row = db.execute("SELECT downloaded, skipped, return_code FROM run_items WHERE run_id=?", (run_id,)).fetchone()
        self.assertEqual(tuple(row), ((1 << 63) - 1, (1 << 63) - 1, 0))

    def test_library_metadata_does_not_persist_embedded_credentials(self):
        self.store.add_library_entry(
            url="https://example.com/one",
            title="project password=hunter2",
            tag="group token=hunter2",
            output_dir="C:/downloads/api_key=hunter2",
        )
        record = self.store.list_library()[0]
        for field in ("title", "tag", "output_dir"):
            self.assertNotIn("hunter2", record[field])

    def test_reimport_without_metadata_preserves_saved_annotations_and_account(self):
        url = "https://example.com/one"
        self.store.add_library_entry(
            url=url, title="Saved title", service="example", tag="saved tag",
            notes="saved note", output_dir="C:/saved folder", account_profile_id=7,
            command="gallery-dl -d 'C:/saved folder' " + url,
        )
        self.store.add_library_entry(url=url, service="example")
        record = self.store.list_library()[0]
        self.assertEqual(record["title"], "Saved title")
        self.assertEqual(record["tag"], "saved tag")
        self.assertEqual(record["notes"], "saved note")
        self.assertEqual(record["output_dir"], "C:/saved folder")
        self.assertEqual(record["account_profile_id"], 7)


class LogAnalyzerTests(unittest.TestCase):
    def test_successful_paths_and_imports_are_not_errors(self):
        target = SimpleNamespace(all_log_lines=[
            r"[W1] C:\Downloads\error-photo.jpg",
            "[W2] /downloads/warning.png",
            r"[input] imported: C:\warning-links.csv",
            "[W1] [gallery-dl][error] HTTP request failed: 404",
            "[W2] [gallery-dl][warning] retrying request",
        ])
        counts = ReportsMixin.analyze_log_lines(target)
        self.assertEqual(counts["errors"], 1)
        self.assertEqual(counts["warnings"], 1)
        self.assertEqual(counts["not-found"], 1)
