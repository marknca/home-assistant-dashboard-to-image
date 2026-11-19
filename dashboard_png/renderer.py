"""Render trusted HTML into one atomically replaced PNG in Home Assistant www."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import signal
import struct
import tempfile
import time
from dataclasses import dataclass, field
from urllib.parse import quote, unquote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOG = logging.getLogger("dashboard_png")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
LOCAL_PREFIX = "/__dashboard_png__/"


class ConfigError(ValueError):
    """An invalid option; messages deliberately exclude option values."""


class RenderError(RuntimeError):
    """A failed render with a safe, actionable error message."""


class RenderTimeoutError(RenderError):
    """A capture deadline with the stage that exhausted it."""


@dataclass
class CaptureProgress:
    """Record failure context without logging page content, URLs, or credentials."""

    stage: str = "validating the source file"
    http_errors: set[int] = field(default_factory=set)
    failed_requests: int = 0
    script_errors: int = 0
    recording: bool = True

    def response(self, response) -> None:
        if self.recording and response.status >= 400:
            self.http_errors.add(response.status)

    def request_failed(self, _request) -> None:
        if self.recording:
            self.failed_requests += 1

    def script_failed(self, _error) -> None:
        if self.recording:
            self.script_errors += 1

    def describe(self) -> str:
        details = [f"while {self.stage}."]
        if self.stage == "waiting for ready_selector":
            details.append(
                "The selector never became visible. Temporarily set ready_selector to an "
                "empty string to inspect the rendered page, then check the dashboard's data loading."
            )
        elif self.stage == "loading the page":
            details.append("Check source access and external scripts, styles, and images.")
        elif self.stage == "connecting to base_url":
            details.append("base_url must be reachable from this app and stay on the configured origin.")
        elif self.stage == "waiting for fonts":
            details.append("Check external font downloads.")
        elif self.stage == "waiting for images":
            details.append("Check image URLs and downloads.")
        if self.http_errors:
            details.append("Observed responses: " + ", ".join(f"HTTP {code}" for code in sorted(self.http_errors)) + ".")
        if 401 in self.http_errors or 403 in self.http_errors:
            details.append("Check the dashboard's API authentication and permissions.")
        if self.failed_requests:
            details.append(f"Failed network requests: {self.failed_requests}.")
        if self.script_errors:
            details.append(f"JavaScript errors: {self.script_errors}.")
        return " ".join(details)

    def timeout(self, seconds: int) -> RenderTimeoutError:
        return RenderTimeoutError(f"Render timed out after {seconds} seconds {self.describe()}")


def http_url(value: str) -> bool:
    """Accept only fully qualified HTTP(S) URLs without embedded credentials."""
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not any(char.isspace() for char in value)
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class Config:
    source: str
    base_url: str = "http://homeassistant:8123"
    width: int = 1600
    height: int = 1200
    filename: str = "dashboard.png"
    interval_seconds: int = 300
    render_delay_ms: int = 2000
    timeout_seconds: int = 60
    ready_selector: str = ""
    timezone: str = "America/Toronto"

    @classmethod
    def from_mapping(cls, options: dict, config_dir: Path) -> Config:
        if not isinstance(options, dict):
            raise ConfigError("Options must be a JSON object.")
        if set(options) - set(cls.__dataclass_fields__):
            raise ConfigError("Unknown option; check the documented option names.")
        values = dict(options)
        values.setdefault("source", str(config_dir / "dashboard/dashboard.html"))
        config = cls(**values)
        for key in ("source", "base_url", "filename", "ready_selector", "timezone"):
            if not isinstance(getattr(config, key), str):
                raise ConfigError(f"{key} must be a string.")
        limits = {
            "width": (1, 8192), "height": (1, 8192),
            "interval_seconds": (10, 86400), "render_delay_ms": (0, 60000),
            "timeout_seconds": (5, 300),
        }
        for key, (minimum, maximum) in limits.items():
            value = getattr(config, key)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ConfigError(f"{key} must be an integer from {minimum} to {maximum}.")
        if config.width * config.height > 33_554_432:
            raise ConfigError("width × height must not exceed 33,554,432 pixels.")
        if config.render_delay_ms >= config.timeout_seconds * 1000:
            raise ConfigError("render_delay_ms must be shorter than timeout_seconds.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.png", config.filename):
            raise ConfigError("filename must be a simple .png filename without directories.")
        if not http_url(config.base_url):
            raise ConfigError("base_url must be an HTTP(S) origin without credentials.")
        origin = urlsplit(config.base_url)
        if origin.path not in ("", "/") or origin.query or origin.fragment:
            raise ConfigError("base_url must be an origin, without a path, query, or fragment.")
        if not http_url(config.source):
            path = Path(config.source)
            if not path.is_absolute() or not path.resolve().is_relative_to(config_dir.resolve()):
                raise ConfigError("source must be an HTTP(S) URL or a file inside the configuration directory.")
        try:
            ZoneInfo(config.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError("timezone must be a valid IANA timezone.") from None
        return config


def local_asset(source_dir: Path, request_url: str) -> Path:
    """Confine relative HTML assets to the source's directory, including symlinks."""
    url_path = urlsplit(request_url).path
    if not url_path.startswith(LOCAL_PREFIX):
        raise RenderError("Invalid local asset path.")
    relative = unquote(url_path[len(LOCAL_PREFIX):])
    if "\x00" in relative or "\\" in relative:
        raise RenderError("Invalid local asset path.")
    path = (source_dir / relative).resolve()
    if not path.is_relative_to(source_dir.resolve()) or not path.is_file():
        raise RenderError("Local asset is missing or outside the dashboard directory.")
    return path


def local_source(source: str, config_dir: Path) -> Path:
    """Check a local source before starting Chromium, with distinct path errors."""
    path = Path(source)
    root = config_dir.resolve()
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise RenderError(f"Local source {str(path)!r} resolves outside {str(root)!r}.")
        if not resolved.exists():
            raise RenderError(
                f"Local source file does not exist: {str(path)!r}. "
                f"The Home Assistant configuration directory is mounted at {str(root)!r} "
                "inside this app. Set source to the HTML file's path under that directory."
            )
        if not resolved.is_file():
            raise RenderError(f"Local source is not a regular file: {str(path)!r}.")
    except (OSError, RuntimeError) as error:
        if isinstance(error, RenderError):
            raise
        raise RenderError(f"Cannot access local source {str(path)!r} ({type(error).__name__}).") from None
    return resolved


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 33 or data[:8] != PNG_SIGNATURE or data[8:16] != b"\x00\x00\x00\rIHDR":
        raise RenderError("The browser did not return a PNG.")
    return struct.unpack(">II", data[16:24])


def write_png(config_dir: Path, filename: str, data: bytes) -> Path:
    """Publish only a complete file; a failed replacement keeps the prior image."""
    root = config_dir.resolve()
    directory = root / "www"
    if directory.is_symlink():
        raise RenderError("The www directory must not be a symbolic link.")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / filename
    if destination.is_symlink():
        raise RenderError("The output PNG must not be a symbolic link.")
    temporary = None
    try:
        # Same filesystem is essential for atomic os.replace.
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=".dashboard-png-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), 0o644)
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


async def capture_page(browser, config: Config, source: Path | None, progress: CaptureProgress) -> bytes:
    """Capture in a separate task so cancellation can close the browser cleanly."""
    from playwright.async_api import Error as BrowserError

    progress.stage = "creating the browser context"
    context = await browser.new_context(
        viewport={"width": config.width, "height": config.height},
        device_scale_factor=1,
        timezone_id=config.timezone,
        color_scheme="light",
        reduced_motion="reduce",
        service_workers="block",
        accept_downloads=False,
    )
    page = await context.new_page()
    page.set_default_timeout(config.timeout_seconds * 1000)
    target = config.source
    if source is not None:
        origin = config.base_url.rstrip("/")
        target = origin + LOCAL_PREFIX + quote(source.name)

        async def serve_asset(route):
            try:
                path = local_asset(source.parent, route.request.url)
                body = await asyncio.to_thread(path.read_bytes)
            except (OSError, RenderError):
                await route.fulfill(status=404, body="Dashboard asset unavailable")
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            await route.fulfill(status=200, content_type=content_type, body=body)

        # A fulfilled main-document request has no real remote IP. Chromium can then
        # block even same-origin LAN API requests under Local Network Access checks.
        # Establish the real origin first; a 404 at this private path is expected.
        progress.stage = "connecting to base_url"
        try:
            await page.goto(target, wait_until="domcontentloaded")
        except BrowserError as error:
            # Chromium replaces empty HTTP error responses with an opaque error page.
            # In that case establish the origin through its normal landing page instead.
            if "net::ERR_HTTP_RESPONSE_CODE_FAILURE" not in str(error):
                raise
            # The failed navigation may still commit chrome-error://chromewebdata/.
            # A fresh page avoids racing that commit with the fallback navigation.
            await page.close()
            page = await context.new_page()
            page.set_default_timeout(config.timeout_seconds * 1000)
            await page.goto(origin + "/", wait_until="domcontentloaded")
        expected, actual = urlsplit(target), urlsplit(page.url)
        def origin_key(url):
            return (url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80))
        if origin_key(expected) != origin_key(actual):
            raise RenderError("base_url redirected to a different origin. Configure its final HTTP(S) origin directly.")
        await page.evaluate("url => history.replaceState(null, '', url)", target)
        # Only this browser sees the private HTML and assets. Nothing is published.
        await page.route(re.compile(re.escape(origin + LOCAL_PREFIX) + ".*"), serve_asset)

    # Do not report the expected bootstrap 404 as a dashboard resource failure.
    page.on("response", progress.response)
    page.on("requestfailed", progress.request_failed)
    page.on("pageerror", progress.script_failed)
    progress.stage = "loading the page"
    if source is not None:
        html = await asyncio.to_thread(source.read_text, encoding="utf-8-sig")
        await page.set_content(html, wait_until="load")
    else:
        response = await page.goto(target, wait_until="load")
        if response is not None and not response.ok:
            raise RenderError(f"The dashboard returned HTTP {response.status}.")
    if config.ready_selector:
        progress.stage = "waiting for ready_selector"
        await page.locator(config.ready_selector).first.wait_for(state="visible")
    # WebSocket dashboards never become network-idle. Use readiness + a bounded delay.
    progress.stage = "waiting for render_delay_ms"
    await page.wait_for_timeout(config.render_delay_ms)
    progress.stage = "waiting for fonts"
    await page.evaluate("() => document.fonts.ready")
    progress.stage = "waiting for images"
    await page.wait_for_function(
        "() => Array.from(document.images).every(image => image.complete)"
    )
    if not await page.evaluate(
        "() => Array.from(document.images).every(image => image.naturalWidth > 0)"
    ):
        raise RenderError("A dashboard image failed to load.")
    progress.stage = "taking the PNG screenshot"
    return await page.screenshot(
        type="png", full_page=False, scale="css",
        animations="disabled", caret="hide",
    )


async def capture(
    config: Config, config_dir: Path, executable_path: str | None = None,
    progress: CaptureProgress | None = None,
) -> bytes:
    progress = progress or CaptureProgress()
    source = None if http_url(config.source) else local_source(config.source, config_dir)
    # Lazy import keeps configuration and publication tests independent of Chromium.
    from playwright.async_api import Error as BrowserError, TimeoutError as BrowserTimeoutError, async_playwright

    try:
        progress.stage = "starting Chromium"
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True, executable_path=executable_path,
                timeout=config.timeout_seconds * 1000,
                args=["--disable-dev-shm-usage"],
            )
            page_task = asyncio.create_task(capture_page(browser, config, source, progress))
            try:
                # Let pending browser RPCs settle before collecting their errors on cancellation.
                await asyncio.wait((page_task,))
                data = page_task.result()
                progress.stage = "closing Chromium"
                return data
            finally:
                # Teardown-induced network failures are not causes of the render failure.
                progress.recording = False
                try:
                    await asyncio.wait_for(browser.close(), timeout=5)
                except TimeoutError:
                    page_task.cancel()
                    raise RenderTimeoutError("Chromium cleanup timed out after 5 seconds.") from None
                except BaseException:
                    page_task.cancel()
                    raise
                finally:
                    await asyncio.gather(page_task, return_exceptions=True)
    except BrowserTimeoutError:
        raise progress.timeout(config.timeout_seconds) from None
    except BrowserError:
        raise RenderError(f"Browser error {progress.describe()}") from None


async def render_png(config: Config, config_dir: Path, executable_path: str | None = None) -> bytes:
    """Capture and validate without publishing, so schedulers can reject stale work."""
    progress = CaptureProgress()
    try:
        async with asyncio.timeout(config.timeout_seconds):
            data = await capture(config, config_dir, executable_path, progress)
    except TimeoutError:
        raise progress.timeout(config.timeout_seconds) from None
    if png_size(data) != (config.width, config.height):
        raise RenderError("PNG dimensions do not match the configured width and height.")
    return data


async def render_once(config: Config, config_dir: Path, executable_path: str | None = None) -> Path:
    data = await render_png(config, config_dir, executable_path)
    # Short synchronous commit: cancellation cannot leave a background writer publishing later.
    return write_png(config_dir, config.filename, data)


def log_failure(error: Exception) -> None:
    # Browser errors may embed source URLs, tokens, or page content.
    if isinstance(error, RenderError):
        LOG.error("%s Keeping the previous PNG; retrying on the next interval.", error)
    else:
        LOG.error(
            "Render failed (%s). Check source access, readiness, and timeout. "
            "Keeping the previous PNG; retrying on the next interval.",
            type(error).__name__,
        )


async def run(config: Config, config_dir: Path, stop: asyncio.Event, executable_path=None) -> None:
    """Render immediately, then at the configured cadence, with no overlapping jobs."""
    while not stop.is_set():
        started = time.monotonic()
        rendering = asyncio.create_task(render_once(config, config_dir, executable_path))
        stopping = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait((rendering, stopping), return_when=asyncio.FIRST_COMPLETED)
            if stop.is_set():
                return
            try:
                path = rendering.result()
            except Exception as error:
                log_failure(error)
            else:
                LOG.info("Updated %s (%s × %s)", path.name, config.width, config.height)
        finally:
            rendering.cancel()
            stopping.cancel()
            await asyncio.gather(rendering, stopping, return_exceptions=True)
        delay = max(1.0, config.interval_seconds - (time.monotonic() - started))
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass


async def async_main(args) -> int:
    try:
        options = json.loads(args.options.read_text())
        config = Config.from_mapping(options, args.config_dir)
    except (OSError, ValueError, TypeError) as error:
        message = str(error) if isinstance(error, ConfigError) else "Cannot read valid options JSON."
        LOG.error("%s", message)
        return 2
    if args.once:
        try:
            path = await render_once(config, args.config_dir, args.browser_executable)
        except Exception as error:
            log_failure(error)
            return 1
        LOG.info("Updated %s (%s × %s)", path.name, config.width, config.height)
        return 0
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    LOG.info(
        "Rendering %s × %s every %s seconds to www/%s",
        config.width, config.height, config.interval_seconds, config.filename,
    )
    if not http_url(config.source):
        LOG.info("Local source: %r", config.source)
    LOG.info("Capture timeout: %s seconds; ready_selector: %s", config.timeout_seconds,
             "enabled" if config.ready_selector else "disabled")
    await run(config, args.config_dir, stop, args.browser_executable)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", type=Path, default=Path("/data/options.json"))
    parser.add_argument("--config-dir", type=Path, default=Path("/homeassistant"))
    parser.add_argument("--once", action="store_true", help="Render once and exit.")
    parser.add_argument("--browser-executable", help="Optional installed Chromium executable for development.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
