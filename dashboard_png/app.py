"""Ingress dashboard manager. Rendering settings are private, persistent app data."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import logging
from pathlib import Path

from aiohttp import web

from catalog import Store
from manager import ConflictError, Manager
from renderer import Config, ConfigError

STATIC = Path(__file__).parent / "static"


def create_app(manager, *, development=False, start_worker=True):
    @web.middleware
    async def guard(request, handler):
        allowed = {"127.0.0.1", "::1"} if development else {"172.30.32.2"}
        # Never trust X-Forwarded-For or a caller-supplied user header for access.
        if request.remote not in allowed:
            raise web.HTTPForbidden(text="Use Home Assistant ingress.")
        if request.method not in ("GET", "HEAD") and (
            request.content_type != "application/json" or request.headers.get("X-Dashboard-PNG") != "1"
        ):
            raise web.HTTPForbidden(text="JSON requests from the dashboard manager are required.")
        try:
            response = await handler(request)
        except ConflictError as error:
            response = web.json_response({"error": str(error)}, status=409)
        except ConfigError as error:
            response = web.json_response({"error": str(error)}, status=400)
        except (json.JSONDecodeError, UnicodeDecodeError):
            response = web.json_response({"error": "Request body must be valid JSON."}, status=400)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "base-uri 'self'; frame-ancestors 'self'; form-action 'self'"
        )
        return response

    app = web.Application(middlewares=[guard], client_max_size=1024 * 1024)

    async def index(request):
        return web.FileResponse(STATIC / "index.html")

    async def settings(request):
        return web.json_response({"settings": manager.settings,
                                  "defaults": asdict(Config.from_mapping({}, manager.store.config_dir))})

    async def save(request):
        body = await request.json()
        if not isinstance(body, dict):
            raise ConfigError("Settings must be a JSON object.")
        manager.save(body)
        return web.json_response({"settings": manager.settings})

    async def status(request):
        return web.json_response(manager.snapshot())

    async def render(request):
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("key"), str):
            raise ConfigError("Choose an output to render.")
        manager.request_render(body["key"])
        return web.json_response({"queued": True}, status=202)

    async def preview(request):
        kind, ident = request.query.get("kind"), request.query.get("id")
        if kind not in ("dashboard", "output"):
            raise web.HTTPNotFound()
        rows = manager.settings["dashboards" if kind == "dashboard" else "outputs"]
        row = next((row for row in rows if row["id"] == ident), None)
        if row is None or (kind == "dashboard" and not row["publish"]):
            raise web.HTTPNotFound()
        filename = row["config"]["filename"] if kind == "dashboard" else row["filename"]
        directory = manager.store.config_dir.resolve() / "www"
        path = directory / filename
        if directory.is_symlink() or path.is_symlink() or not path.is_file():
            raise web.HTTPNotFound()
        # Read a complete snapshot and close the descriptor before sending. A UI
        # navigation can cancel an image request while another render replaces it.
        return web.Response(body=await asyncio.to_thread(path.read_bytes), content_type="image/png")

    async def lifecycle(app):
        worker = asyncio.create_task(manager.run())
        yield
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    app.router.add_get("/", index)
    app.router.add_get("/index.html", index)
    app.router.add_get("/api/settings", settings)
    app.router.add_put("/api/settings", save)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/render", render)
    app.router.add_get("/api/preview", preview)
    app.router.add_static("/static/", STATIC, show_index=False, follow_symlinks=False)
    if start_worker:
        app.cleanup_ctx.append(lifecycle)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", type=Path, default=Path("/data/options.json"))
    parser.add_argument("--settings", type=Path, default=Path("/data/dashboards.json"))
    parser.add_argument("--config-dir", type=Path, default=Path("/homeassistant"))
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--development", action="store_true", help="Loopback-only UI for local development.")
    parser.add_argument("--browser-executable")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = Store(args.settings, args.config_dir)
    try:
        imported = not store.path.exists()
        settings = store.load(args.options)
    except (OSError, ValueError, TypeError) as error:
        logging.error("%s", str(error) if isinstance(error, ConfigError) else "Cannot read valid dashboard settings.")
        return 2
    if imported:
        logging.info("Imported existing app options as Default. Manage dashboards with Open Web UI.")
    manager = Manager(store, settings, args.browser_executable)
    app = create_app(manager, development=args.development)
    web.run_app(app, host="127.0.0.1" if args.development else "0.0.0.0", port=args.port,
                access_log=None, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
