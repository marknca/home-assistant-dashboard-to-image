"""Named dashboards, durable configuration, and deterministic output selection."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from renderer import Config, ConfigError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def object_keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ConfigError(f"{label} has invalid or unknown fields.")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise ConfigError("IDs must contain 1–64 lowercase letters, numbers, hyphens, or underscores.")
    return value


def boolean(value, label):
    if type(value) is not bool:
        raise ConfigError(f"{label} must be true or false.")
    return value


def named(value):
    result = {"id": identifier(value.get("id")), "name": value.get("name", ""),
              "enabled": boolean(value.get("enabled", True), "enabled")}
    if not isinstance(result["name"], str) or not 1 <= len(result["name"].strip()) <= 80:
        raise ConfigError("Give each dashboard and shared output a name of 1–80 characters.")
    result["name"] = result["name"].strip()
    return result


def bounded_list(value, label, minimum=0, maximum=32):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ConfigError(f"{label} must contain {minimum}–{maximum} entries.")
    return value


def validate_settings(raw, config_dir: Path, previous=None, now=None):
    """Normalize all settings before accepting any changes. Never include sources in errors."""
    object_keys(raw, {"version", "revision", "dashboards", "outputs"}, "Settings")
    if type(raw.get("version", 1)) is not int or raw.get("version", 1) != 1:
        raise ConfigError("Unsupported settings version.")
    now = time.time() if now is None else now
    dashboards = []
    ids = set()
    filenames = set()

    def reserve(filename):
        # Case-insensitive uniqueness also works on case-insensitive development mounts.
        if filename.casefold() in filenames:
            raise ConfigError("Output filenames must be unique. Turn off a dashboard's own PNG before reusing its filename for a shared output.")
        filenames.add(filename.casefold())

    for item in bounded_list(raw.get("dashboards", []), "Dashboards"):
        object_keys(item, {"id", "name", "enabled", "publish", "config"}, "Dashboard")
        row = named(item)
        if row["id"] in ids:
            raise ConfigError("Dashboard IDs must be unique.")
        ids.add(row["id"])
        row["publish"] = boolean(item.get("publish", True), "publish")
        row["config"] = asdict(Config.from_mapping(item.get("config", {}), config_dir))
        if row["publish"]:
            reserve(row["config"]["filename"])
        dashboards.append(row)
    profiles = {row["id"]: row for row in dashboards}

    outputs = []
    output_ids = set()
    old_outputs = {row["id"]: row for row in (previous or {}).get("outputs", [])}
    for item in bounded_list(raw.get("outputs", []), "Shared outputs"):
        object_keys(item, {"id", "name", "enabled", "filename", "mode", "timezone", "rotation", "schedule", "anchor"}, "Shared output")
        row = named(item)
        if row["id"] in output_ids:
            raise ConfigError("Shared output IDs must be unique.")
        output_ids.add(row["id"])
        filename = item.get("filename", "")
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.png", filename):
            raise ConfigError("Shared output filename must be a simple .png filename.")
        reserve(filename)
        row["filename"] = filename
        if item.get("mode") not in ("rotation", "schedule"):
            raise ConfigError("Choose rotation or schedule for each shared output.")
        row["mode"] = item["mode"]
        row["timezone"] = item.get("timezone", "America/Toronto")
        try:
            if not isinstance(row["timezone"], str):
                raise ValueError()
            ZoneInfo(row["timezone"])
        except (ValueError, ZoneInfoNotFoundError):
            raise ConfigError("Shared output timezone must be an IANA timezone.") from None

        def reference(entry):
            key = entry.get("dashboard_id")
            if not isinstance(key, str) or key not in profiles:
                raise ConfigError("A shared output references a missing dashboard. Update its entries before deleting the dashboard.")
            if row["enabled"] and not profiles[key]["enabled"]:
                raise ConfigError("An enabled shared output cannot use a disabled dashboard.")
            return key

        row["rotation"] = []
        row["schedule"] = []
        if row["mode"] == "rotation":
            for entry in bounded_list(item.get("rotation", []), "Rotation", 1, 100):
                object_keys(entry, {"dashboard_id", "duration_seconds"}, "Rotation entry")
                duration = entry.get("duration_seconds")
                if type(duration) is not int or not 10 <= duration <= 86400:
                    raise ConfigError("Rotation durations must be 10–86400 seconds.")
                row["rotation"].append({"dashboard_id": reference(entry), "duration_seconds": duration})
        else:
            occupied = set()
            for entry in bounded_list(item.get("schedule", []), "Schedule", 1, 100):
                object_keys(entry, {"dashboard_id", "at", "days"}, "Schedule entry")
                at = entry.get("at", "")
                if not isinstance(at, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", at):
                    raise ConfigError("Schedule times must use HH:MM, in 24-hour format.")
                days = bounded_list(entry.get("days", list(DAYS)), "Days", 1, 7)
                if any(not isinstance(day, str) or day not in DAYS for day in days) or len(set(days)) != len(days):
                    raise ConfigError("Schedule days must be unique mon, tue, wed, thu, fri, sat, or sun values.")
                for day in days:
                    if (day, at) in occupied:
                        raise ConfigError("A shared output cannot switch to two dashboards at the same day and time.")
                    occupied.add((day, at))
                row["schedule"].append({"dashboard_id": reference(entry), "at": at,
                                        "days": [day for day in DAYS if day in days]})
        old = old_outputs.get(row["id"])
        if old and old["mode"] == row["mode"] and old["rotation"] == row["rotation"]:
            anchor = old["anchor"]
        elif previous is not None:
            anchor = now
        else:
            anchor = item.get("anchor", now)
        if type(anchor) not in (int, float) or not math.isfinite(anchor) or anchor < 0:
            raise ConfigError("Invalid rotation start time.")
        row["anchor"] = anchor
        outputs.append(row)
    revision = raw.get("revision", 0)
    if type(revision) is not int or revision < 0:
        raise ConfigError("Invalid configuration revision.")
    return {"version": 1, "revision": revision, "dashboards": dashboards, "outputs": outputs}


@dataclass(frozen=True)
class Selection:
    dashboard_id: str
    next_switch: float | None


def select_dashboard(output, now: float) -> Selection:
    """Select by wall time, including after restarts, without replaying missed switches."""
    if output["mode"] == "rotation":
        total = sum(row["duration_seconds"] for row in output["rotation"])
        elapsed = max(0, now - output["anchor"])
        cycle = math.floor(elapsed / total)
        position = elapsed % total
        end = 0
        for row in output["rotation"]:
            end += row["duration_seconds"]
            if position < end:
                return Selection(row["dashboard_id"], output["anchor"] + cycle * total + end)
        raise AssertionError("Invalid rotation")
    zone = ZoneInfo(output["timezone"])
    today = datetime.fromtimestamp(now, zone).date()
    transitions = []
    # Include a full prior and next week. A selection persists until the next switch.
    for offset in range(-8, 9):
        day = today + timedelta(days=offset)
        for row in output["schedule"]:
            if DAYS[day.weekday()] not in row["days"]:
                continue
            hour, minute = map(int, row["at"].split(":"))
            # fold=0 picks the first occurrence at fall-back. Missing spring times
            # normalize forward (02:30 becomes 03:30), rather than disappearing.
            stamp = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone, fold=0).timestamp()
            # If spring normalization coincides with a later clock time, that
            # later explicit time wins, independently of dashboard IDs/list order.
            transitions.append((stamp, row["at"], row["dashboard_id"]))
    transitions.sort()
    current = [entry for entry in transitions if entry[0] <= now][-1]
    following = next((entry[0] for entry in transitions if entry[0] > now), None)
    return Selection(current[2], following)


class Store:
    def __init__(self, path: Path, config_dir: Path):
        self.path, self.config_dir = path, config_dir

    def load(self, legacy_options: Path):
        if self.path.is_symlink():
            raise ConfigError("Settings file must not be a symbolic link.")
        if self.path.exists():
            raw = json.loads(self.path.read_text())
            settings = validate_settings(raw, self.config_dir)
            # Persist generated anchors/defaults when importing a hand-written
            # settings file, so subsequent restarts cannot reset a rotation.
            if settings != raw:
                self.write(settings)
            return settings
        options = json.loads(legacy_options.read_text())
        settings = validate_settings({"dashboards": [{"id": "default", "name": "Default",
                                      "config": options}], "outputs": []}, self.config_dir)
        self.write(settings)
        return settings

    def write(self, settings):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ConfigError("Settings file must not be a symbolic link.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, prefix=".dashboards-", delete=False) as handle:
                temporary = Path(handle.name)
                os.fchmod(handle.fileno(), 0o600)
                json.dump(settings, handle, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
