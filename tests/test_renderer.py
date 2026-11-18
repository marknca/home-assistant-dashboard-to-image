import asyncio
from dataclasses import replace
import importlib.util
import gc
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

MODULE = Path(__file__).resolve().parents[1] / "dashboard_png" / "renderer.py"
spec = importlib.util.spec_from_file_location("renderer", MODULE)
renderer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = renderer
spec.loader.exec_module(renderer)


class ConfigAndPublicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()

    def config(self, **options):
        return renderer.Config.from_mapping(options, self.root)

    def test_defaults_and_exact_dimensions(self):
        config = self.config()
        self.assertEqual((config.width, config.height), (1600, 1200))
        self.assertEqual(config.source, str(self.root / "dashboard/dashboard.html"))

    def test_rejects_invalid_settings(self):
        for options in [
            {"width": 0}, {"height": 8193}, {"width": True},
            {"width": 8192, "height": 8192}, {"interval_seconds": 0},
            {"filename": "../configuration.yaml"}, {"filename": "/tmp/image.png"},
            {"filename": "image.jpg"}, {"filename": "a\\b.png"},
            {"source": "file:///etc/passwd"}, {"source": "/etc/passwd"},
            {"source": "ftp://example.com"}, {"source": "http://u:p@example.com"},
            {"timezone": "not/a/timezone"}, {"base_url": "http://example.com/path"},
            {"base_url": "http://example.com?key=secret"},
            {"timeout_seconds": 5, "render_delay_ms": 5000},
            {"typo": "value"}, {"ready_selector": None},
        ]:
            with self.subTest(options=options), self.assertRaises(renderer.ConfigError):
                self.config(**options)

    def test_local_assets_reject_traversal_and_symlinks(self):
        source = self.root / "dashboard"
        source.mkdir()
        (source / "style.css").write_text("body { color: red; }")
        (self.root / "private.txt").write_text("private")
        (source / "link.txt").symlink_to(self.root / "private.txt")
        url = "http://ha:8123/__dashboard_png__/"
        self.assertEqual(renderer.local_asset(source, url + "style.css?v=1"), source / "style.css")
        for path in ("../private.txt", "%2e%2e/private.txt", "link.txt", "%2Fetc/passwd", "%00"):
            with self.subTest(path=path), self.assertRaises(renderer.RenderError):
                renderer.local_asset(source, url + path)

    def test_replacement_is_atomic_and_readable(self):
        path = renderer.write_png(self.root, "dashboard.png", b"first")
        renderer.write_png(self.root, "dashboard.png", b"second")
        self.assertEqual(path.read_bytes(), b"second")
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_replace_failure_preserves_old_file_and_cleans_temporary(self):
        path = renderer.write_png(self.root, "dashboard.png", b"first")
        with patch.object(renderer.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                renderer.write_png(self.root, "dashboard.png", b"second")
        self.assertEqual(path.read_bytes(), b"first")
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_output_symlink_is_rejected(self):
        (self.root / "www").symlink_to(self.root / "elsewhere")
        with self.assertRaises(renderer.RenderError):
            renderer.write_png(self.root, "dashboard.png", b"data")

    def test_png_header_validation(self):
        with self.assertRaises(renderer.RenderError):
            renderer.png_size(b"not an image")
        header = renderer.PNG_SIGNATURE + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 640, 480) + b"\x00" * 9
        self.assertEqual(renderer.png_size(header), (640, 480))

    def test_local_source_distinguishes_missing_files_and_directories(self):
        missing = self.root / "dashboard/dashboard.html"
        with self.assertRaisesRegex(renderer.RenderError, "file does not exist") as caught:
            renderer.local_source(str(missing), self.root)
        self.assertIn(str(missing), str(caught.exception))
        with self.assertRaisesRegex(renderer.RenderError, "not a regular file"):
            renderer.local_source(str(self.root), self.root)
        missing.parent.mkdir()
        missing.write_text("<body>Dashboard</body>")
        self.assertEqual(renderer.local_source(str(missing), self.root), missing)

    def test_local_source_rejects_symlink_outside_config(self):
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory).resolve() / "private.html"
            outside.write_text("private")
            link = self.root / "dashboard.html"
            link.symlink_to(outside)
            with self.assertRaisesRegex(renderer.RenderError, "resolves outside"):
                renderer.local_source(str(link), self.root)

    def test_diagnostics_exclude_credentials_and_teardown_failures(self):
        progress = renderer.CaptureProgress(stage="waiting for ready_selector")
        progress.response(SimpleNamespace(status=401, url="http://example.test/?token=secret"))
        progress.script_failed(RuntimeError("secret"))
        progress.recording = False
        progress.request_failed(SimpleNamespace(failure="secret"))
        message = str(progress.timeout(60))
        self.assertIn("waiting for ready_selector", message)
        self.assertIn("HTTP 401", message)
        self.assertIn("JavaScript errors: 1", message)
        self.assertNotIn("Failed network requests", message)
        self.assertNotIn("secret", message)


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_failure_without_overlapping(self):
        stop = asyncio.Event()
        calls = 0
        active = 0

        async def render(*args):
            nonlocal calls, active
            calls += 1
            active += 1
            self.assertEqual(active, 1)
            await asyncio.sleep(0)
            active -= 1
            if calls == 1:
                raise renderer.RenderError("Temporary failure")
            stop.set()
            return Path("dashboard.png")

        config = renderer.Config(source="http://example.test", interval_seconds=10)
        with patch.object(renderer, "render_once", side_effect=render), self.assertLogs(
            "dashboard_png", level="ERROR"
        ):
            await asyncio.wait_for(
                renderer.run(replace(config, interval_seconds=0), Path("/unused"), stop),
                timeout=4,
            )
        self.assertEqual(calls, 2)

    async def test_stop_cancels_active_render(self):
        stop = asyncio.Event()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def render(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch.object(renderer, "render_once", side_effect=render):
            task = asyncio.create_task(renderer.run(renderer.Config(source="http://example.test"), Path("/unused"), stop))
            await started.wait()
            stop.set()
            await asyncio.wait_for(task, 1)
        self.assertTrue(cancelled.is_set())

    async def test_bad_capture_never_replaces_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = renderer.write_png(root, "dashboard.png", b"previous")
            with patch.object(renderer, "capture", new=AsyncMock(return_value=b"not png")):
                with self.assertRaises(renderer.RenderError):
                    await renderer.render_once(renderer.Config(source="http://example.test"), root)
            self.assertEqual(path.read_bytes(), b"previous")


@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Set RUN_BROWSER_TESTS=1 for Chromium tests")
class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loop_errors = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: self.loop_errors.append(context))
        self.addCleanup(loop.set_exception_handler, previous_handler)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.source = self.root / "dashboard.html"
        self.executable = os.environ.get("BROWSER_EXECUTABLE")
        self.config = renderer.Config.from_mapping({
            "source": str(self.source), "width": 640, "height": 480,
            "render_delay_ms": 0, "ready_selector": 'body[data-ready="true"]',
        }, self.root)

    async def asyncTearDown(self):
        gc.collect()
        await asyncio.sleep(0)
        self.assertEqual(self.loop_errors, [], "Browser cleanup leaked an asynchronous exception")

    async def render(self, config=None):
        return await renderer.render_once(config or self.config, self.root, self.executable)

    async def test_actual_png_exact_dimensions_refresh_and_readiness(self):
        (self.root / "style.css").write_text("html,body {margin:0; height:2000px; background:red;}")
        self.source.write_text("""
            <link rel="stylesheet" href="style.css"><body>
            <script>
            Promise.all([fetch("style.css").then(r => r.text())]).then(([css]) => {
              if (location.origin === "http://homeassistant:8123" && css.includes("red"))
                setTimeout(() => document.body.dataset.ready = "true", 100);
            });
            </script>
        """)
        path = await self.render()
        first = path.read_bytes()
        self.assertEqual(renderer.png_size(first), (640, 480))
        (self.root / "style.css").write_text("html,body {margin:0; height:2000px; background:blue;}")
        self.source.write_text('<link rel="stylesheet" href="style.css"><body data-ready="true">Updated</body>')
        self.assertEqual(await self.render(), path)
        self.assertNotEqual(first, path.read_bytes())
        self.assertEqual(renderer.png_size(path.read_bytes()), (640, 480))
        self.assertEqual(len(list(path.parent.iterdir())), 1)

    async def test_missing_readiness_preserves_prior_png(self):
        self.source.write_text('<body data-ready="true">Good</body>')
        path = await self.render()
        previous = path.read_bytes()
        self.source.write_text("<body>Still loading</body>")
        with self.assertRaisesRegex(renderer.RenderTimeoutError, "waiting for ready_selector"):
            await self.render(replace(self.config, timeout_seconds=5))
        self.assertEqual(path.read_bytes(), previous)

    async def test_readiness_timeout_reports_api_failure_without_secrets(self):
        async def respond(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(respond, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            self.source.write_text('''<body><script>
                fetch('/api/data?token=test-secret');
                throw new Error('test-secret');
                </script></body>''')
            path = renderer.write_png(self.root, "dashboard.png", b"previous")
            config = replace(self.config, base_url=f"http://127.0.0.1:{port}", timeout_seconds=5)
            with self.assertRaises(renderer.RenderTimeoutError) as caught:
                await self.render(config)
            message = str(caught.exception)
            self.assertIn("waiting for ready_selector", message)
            self.assertIn("HTTP 401", message)
            self.assertIn("JavaScript errors: 1", message)
            self.assertNotIn("test-secret", message)
            self.assertEqual(path.read_bytes(), b"previous")

    async def test_url_source_and_http_error(self):
        async def respond(reader, writer):
            first = await reader.readline()
            await reader.readuntil(b"\r\n\r\n")
            if b"/bad " in first:
                status, body = b"503 Service Unavailable", b"Unavailable"
            else:
                status, body = b"200 OK", b'<body data-ready="true">URL dashboard</body>'
            writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Type: text/html\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(respond, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            config = replace(self.config, source=f"http://127.0.0.1:{port}/")
            path = await self.render(config)
            previous = path.read_bytes()
            with self.assertRaisesRegex(renderer.RenderError, "HTTP 503"):
                await self.render(replace(config, source=f"http://127.0.0.1:{port}/bad"))
            self.assertEqual(path.read_bytes(), previous)

    async def test_broken_image_preserves_prior_png(self):
        self.source.write_text('<body data-ready="true">Good</body>')
        path = await self.render()
        previous = path.read_bytes()
        self.source.write_text('<body data-ready="true"><img src="missing.png"></body>')
        with self.assertRaisesRegex(renderer.RenderError, "image failed"):
            await self.render()
        self.assertEqual(path.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
