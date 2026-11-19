import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard_png"))
from catalog import DAYS, Store, select_dashboard, validate_settings
from renderer import ConfigError


def profiles(publish=True):
    return [{"id": ident, "name": ident.upper(), "publish": publish,
             "config": {"source": f"http://example.test/{ident}", "filename": f"{ident}.png",
                        "width": 320, "height": 240, "interval_seconds": 10}}
            for ident in ("a", "b")]


def rotation():
    return {"id": "display", "name": "Wall display", "filename": "display.png", "mode": "rotation",
            "rotation": [{"dashboard_id": "a", "duration_seconds": 10},
                         {"dashboard_id": "b", "duration_seconds": 20}], "anchor": 1000}


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def settings(self, output=None, publish=True):
        return validate_settings({"dashboards": profiles(publish), "outputs": [output or rotation()]}, self.root, now=1000)

    def test_migration_preserves_all_existing_options_and_survives_restart(self):
        legacy = self.root / "options.json"
        legacy.write_text(json.dumps({"source": str(self.root / "private.html"), "width": 800,
            "height": 600, "filename": "dashboard.png", "interval_seconds": 600,
            "ready_selector": '#status:text-is("SYNCED")', "timezone": "Europe/London"}))
        store = Store(self.root / "data/dashboards.json", self.root)
        first = store.load(legacy)
        profile = first["dashboards"][0]
        self.assertEqual(profile["name"], "Default")
        self.assertEqual(profile["config"]["width"], 800)
        self.assertEqual(profile["config"]["ready_selector"], '#status:text-is("SYNCED")')
        self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
        legacy.unlink()
        self.assertEqual(store.load(legacy), first)

    def test_corrupt_settings_do_not_silently_reimport(self):
        path = self.root / "dashboards.json"
        path.write_text("{broken")
        with self.assertRaises(ValueError):
            Store(path, self.root).load(self.root / "missing-options.json")
        self.assertEqual(path.read_text(), "{broken")

    def test_hand_written_settings_persist_generated_rotation_anchor(self):
        output = rotation(); del output['anchor']
        path = self.root / 'settings.json'
        path.write_text(json.dumps({'dashboards': profiles(), 'outputs': [output]}))
        store = Store(path, self.root)
        with patch('catalog.time.time', return_value=1000):
            first = store.load(self.root / 'unused')
        with patch('catalog.time.time', return_value=2000):
            second = store.load(self.root / 'unused')
        self.assertEqual(first['outputs'][0]['anchor'], 1000)
        self.assertEqual(first, second)

    def test_unique_output_owners_and_rotation_only_profiles(self):
        output = rotation()
        output["filename"] = "A.png"
        with self.assertRaisesRegex(ConfigError, "filenames must be unique"):
            self.settings(output)
        self.settings(output, publish=False)

    def test_missing_disabled_and_duplicate_references_are_rejected(self):
        good = self.settings()
        cases = []
        bad = copy.deepcopy(good); bad["dashboards"].pop(); cases.append(bad)
        bad = copy.deepcopy(good); bad["dashboards"][0]["enabled"] = False; cases.append(bad)
        bad = copy.deepcopy(good); bad["dashboards"][1]["id"] = "a"; cases.append(bad)
        bad = copy.deepcopy(good); bad["outputs"].append(copy.deepcopy(bad["outputs"][0])); cases.append(bad)
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                validate_settings(bad, self.root)

    def test_rotation_boundaries_and_restart(self):
        output = self.settings()["outputs"][0]
        for now, expected in [(1000, "a"), (1009.99, "a"), (1010, "b"), (1029.99, "b"), (1030, "a"), (1081, "b")]:
            self.assertEqual(select_dashboard(output, now).dashboard_id, expected)
        self.assertEqual(select_dashboard(output, 1010).next_switch, 1030)
        store = Store(self.root / "settings.json", self.root)
        store.write(self.settings())
        loaded = store.load(self.root / "not-needed")
        self.assertEqual(select_dashboard(loaded["outputs"][0], 1081).dashboard_id, "b")

    def test_unrelated_save_preserves_rotation_but_edit_restarts_it(self):
        original = self.settings()
        edited = copy.deepcopy(original); edited["dashboards"][0]["name"] = "Renamed"
        normalized = validate_settings(edited, self.root, previous=original, now=2000)
        self.assertEqual(normalized["outputs"][0]["anchor"], 1000)
        edited["outputs"][0]["rotation"][0]["duration_seconds"] = 30
        normalized = validate_settings(edited, self.root, previous=original, now=2000)
        self.assertEqual(normalized["outputs"][0]["anchor"], 2000)

    def test_weekly_schedule_keeps_previous_selection_overnight_and_weekends(self):
        output = rotation()
        output.update(mode="schedule", timezone="America/Toronto", schedule=[
            {"at": "07:00", "days": ["mon"], "dashboard_id": "a"},
            {"at": "18:00", "days": ["fri"], "dashboard_id": "b"}])
        output = self.settings(output)["outputs"][0]
        for date, expected in [("2026-11-23T06:59:00-05:00", "b"),
                               ("2026-11-23T07:00:00-05:00", "a"),
                               ("2026-11-27T18:00:00-05:00", "b"),
                               ("2026-11-29T12:00:00-05:00", "b")]:
            self.assertEqual(select_dashboard(output, stamp(date)).dashboard_id, expected)

    def test_dst_missing_time_normalizes_forward_and_repeated_time_fires_once(self):
        output = rotation()
        output.update(mode="schedule", timezone="America/Toronto", schedule=[
            {"at": "02:30", "dashboard_id": "a"}, {"at": "12:00", "dashboard_id": "b"}])
        output = self.settings(output)["outputs"][0]
        self.assertEqual(select_dashboard(output, stamp("2026-03-08T03:29:00-04:00")).dashboard_id, "b")
        self.assertEqual(select_dashboard(output, stamp("2026-03-08T03:30:00-04:00")).dashboard_id, "a")
        output["schedule"][0]["at"] = "01:30"
        self.assertEqual(select_dashboard(output, stamp("2026-11-01T01:15:00-05:00")).dashboard_id, "a")
        self.assertEqual(select_dashboard(output, stamp("2026-11-01T01:15:00-05:00")).next_switch,
                         stamp("2026-11-01T12:00:00-05:00"))

    def test_schedule_conflicts_and_invalid_input(self):
        for entries in [[], [{"at": "25:00", "dashboard_id": "a"}],
                        [{"at": "07:00", "days": [], "dashboard_id": "a"}],
                        [{"at": "07:00", "dashboard_id": "a"}, {"at": "07:00", "dashboard_id": "b"}]]:
            output = rotation(); output.update(mode="schedule", schedule=entries)
            with self.subTest(entries=entries), self.assertRaises(ConfigError):
                self.settings(output)

    def test_invalid_filenames_durations_and_unknown_options(self):
        for changes in [{"filename": "../bad.png"}, {"rotation": []}, {"mode": "cron"},
                        {"rotation": [{"dashboard_id": "a", "duration_seconds": True}]},
                        {"timezone": "not/a/zone"}, {"unexpected": 123}]:
            output = rotation(); output.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ConfigError):
                self.settings(output)


if __name__ == "__main__":
    unittest.main()
