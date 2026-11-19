import asyncio
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard_png"))
from catalog import Store, validate_settings
from manager import ConflictError, Manager
from renderer import RenderError, write_png
from test_catalog import profiles, rotation


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1000.0
        self.calls = []

    def manager(self, publish=True, outputs=True, capture=None):
        async def fake(config, *_):
            self.calls.append(config)
            return config.source.encode()
        raw = {"dashboards": profiles(publish), "outputs": [rotation()] if outputs else []}
        settings = validate_settings(raw, self.root, now=self.now)
        return Manager(Store(self.root / "settings.json", self.root), settings,
                       clock=lambda: self.now, capture=capture or fake)

    async def test_independent_outputs_and_shared_rotation_refresh_at_correct_cadence(self):
        manager = self.manager()
        for _ in range(3):
            self.assertTrue(await manager.step())
        self.assertFalse(await manager.step())
        for filename in ("a.png", "b.png", "display.png"):
            self.assertTrue((self.root / "www" / filename).exists())
        self.assertEqual((self.root / "www/display.png").read_bytes(), b"http://example.test/a")
        self.now = 1010
        for _ in range(3):
            await manager.step()
        self.assertEqual((self.root / "www/display.png").read_bytes(), b"http://example.test/b")
        self.assertEqual(manager.snapshot()["output:display"]["next_switch"], 1030)

    async def test_failure_preserves_previous_image_and_other_dashboard_still_renders(self):
        async def capture(config, *_):
            if config.filename == "a.png":
                raise RenderError("Not ready")
            return b"new b"
        manager = self.manager(outputs=False, capture=capture)
        write_png(self.root, "a.png", b"previous a")
        with self.assertLogs("dashboard_png", level="ERROR"):
            await manager.step()
        await manager.step()
        self.assertEqual((self.root / "www/a.png").read_bytes(), b"previous a")
        self.assertEqual((self.root / "www/b.png").read_bytes(), b"new b")
        self.assertIn("Not ready", manager.snapshot()["dashboard:a"]["last_error"])

    async def test_capture_crossing_switch_boundary_cannot_publish_old_dashboard(self):
        async def capture(config, *_):
            self.now = 1011
            return b"stale a"
        manager = self.manager(publish=False, capture=capture)
        write_png(self.root, "display.png", b"previous")
        await manager.step()
        self.assertEqual((self.root / "www/display.png").read_bytes(), b"previous")
        self.assertEqual(manager.snapshot()["output:display"]["selected_dashboard"], "b")

    async def test_edit_during_capture_cancels_work_and_blocks_late_result(self):
        started = asyncio.Event()
        async def capture(config, *_):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return b"late result"
        manager = self.manager(outputs=False, capture=capture)
        write_png(self.root, "a.png", b"previous")
        task = asyncio.create_task(manager.step())
        await started.wait()
        edited = copy.deepcopy(manager.settings)
        edited["dashboards"][0]["config"]["source"] = "http://example.test/changed"
        manager.save(edited)
        await task
        self.assertEqual((self.root / "www/a.png").read_bytes(), b"previous")
        self.assertEqual(manager.settings["revision"], 1)

    async def test_manual_render_keeps_rotation_anchor_and_conflicting_save_is_rejected(self):
        manager = self.manager(publish=False)
        await manager.step()
        self.now += 2
        manager.request_render("output:display")
        self.assertTrue(await manager.step())
        self.assertEqual(len(self.calls), 2)
        stale = copy.deepcopy(manager.settings)
        manager.save(copy.deepcopy(stale))
        self.assertEqual(manager.settings["outputs"][0]["anchor"], 1000)
        with self.assertRaises(ConflictError):
            manager.save(stale)

    async def test_shutdown_cancels_active_capture(self):
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def capture(*_):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        manager = self.manager(capture=capture)
        worker = asyncio.create_task(manager.run())
        await started.wait()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(manager.rendering)


if __name__ == "__main__":
    unittest.main()
