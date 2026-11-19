"""One bounded render worker for independent dashboards and shared image schedules."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, replace
import logging
import time

from catalog import Store, select_dashboard, validate_settings
from renderer import Config, ConfigError, RenderError, render_png, write_png

LOG = logging.getLogger("dashboard_png")


class ConflictError(ConfigError):
    pass


@dataclass(frozen=True)
class Job:
    key: str
    name: str
    dashboard_id: str
    config: Config
    next_switch: float | None = None


class Manager:
    def __init__(self, store: Store, settings, executable_path=None, clock=time.time, capture=None):
        self.store, self.settings = store, settings
        self.executable_path, self.clock = executable_path, clock
        self.capture = capture or render_png
        self.status = {}
        self.requested = set()
        self.wake = asyncio.Event()
        self.rendering = None

    def jobs(self, now=None):
        now = self.clock() if now is None else now
        profiles = {row["id"]: row for row in self.settings["dashboards"]}
        jobs = {}
        for row in profiles.values():
            if row["enabled"] and row["publish"]:
                key = "dashboard:" + row["id"]
                jobs[key] = Job(key, row["name"], row["id"], Config(**row["config"]))
        for row in self.settings["outputs"]:
            if not row["enabled"]:
                continue
            selection = select_dashboard(row, now)
            profile = profiles[selection.dashboard_id]
            key = "output:" + row["id"]
            config = replace(Config(**profile["config"]), filename=row["filename"])
            jobs[key] = Job(key, row["name"], profile["id"], config, selection.next_switch)
        return jobs

    def save(self, raw):
        if raw.get("revision") != self.settings["revision"]:
            raise ConflictError("Settings changed in another window. Reload before saving again.")
        updated = validate_settings(raw, self.store.config_dir, previous=self.settings, now=self.clock())
        updated["revision"] = self.settings["revision"] + 1
        self.store.write(updated)
        self.settings = updated
        self.status = {}
        self.requested.clear()
        if self.rendering is not None:
            self.rendering.cancel()
        self.wake.set()
        LOG.info("Saved %s dashboards and %s shared outputs", len(updated["dashboards"]), len(updated["outputs"]))

    def request_render(self, key):
        if key not in self.jobs():
            raise ConfigError("Enable this output and save it before rendering. Dashboards also need their own PNG enabled.")
        self.requested.add(key)
        self.wake.set()

    def snapshot(self):
        now = self.clock()
        active = self.jobs(now)
        result = {}
        for key, job in active.items():
            state = dict(self.status.get(key, {}))
            state.update(selected_dashboard=job.dashboard_id, next_switch=job.next_switch,
                         filename=job.config.filename, queued=key in self.requested)
            result[key] = state
        return result

    async def step(self):
        """Process at most one due output; used by the worker and deterministic tests."""
        started = self.clock()
        candidates = []
        for key, job in self.jobs(started).items():
            state = self.status.get(key, {})
            due = state.get("next_due", 0)
            if state.get("attempted_dashboard") != job.dashboard_id or key in self.requested:
                due = min(due, started)
            if due <= started:
                candidates.append((due, key, job))
        if not candidates:
            return False
        _, key, job = min(candidates, key=lambda row: (row[0], row[1]))
        revision = self.settings["revision"]
        self.requested.discard(key)
        state = self.status.setdefault(key, {})
        state.update(rendering=True, last_attempt=started, attempted_dashboard=job.dashboard_id,
                     next_due=started + job.config.interval_seconds)
        self.rendering = asyncio.create_task(self.capture(job.config, self.store.config_dir, self.executable_path))
        try:
            data = await self.rendering
            # A save, deletion, or schedule transition must never allow stale work
            # to replace the correct image. Check immediately before the atomic write.
            current = self.jobs().get(key)
            if revision != self.settings["revision"] or current is None or current.dashboard_id != job.dashboard_id:
                LOG.info("Discarded superseded capture for %s", key)
                return True
            write_png(self.store.config_dir, job.config.filename, data)
            state.update(last_success=self.clock(), published_dashboard=job.dashboard_id, last_error=None)
            LOG.info("Updated %s from dashboard %s (%s × %s)", job.config.filename,
                     job.dashboard_id, job.config.width, job.config.height)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            # Editing settings cancels only the capture, not the worker.
        except Exception as error:
            message = str(error) if isinstance(error, RenderError) else f"Render failed ({type(error).__name__})."
            state["last_error"] = message
            LOG.error("%s: %s Keeping the previous PNG.", key, message)
        finally:
            state["rendering"] = False
            self.rendering.cancel()
            await asyncio.gather(self.rendering, return_exceptions=True)
            self.rendering = None
            # A slow render gets a short breather instead of a busy retry loop.
            state["next_due"] = max(state["next_due"], self.clock() + 1)
        return True

    async def run(self):
        while True:
            self.wake.clear()
            if await self.step():
                continue
            try:
                await asyncio.wait_for(self.wake.wait(), 1)
            except TimeoutError:
                pass
