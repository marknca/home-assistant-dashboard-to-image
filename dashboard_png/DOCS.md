# Dashboard PNG

Render multiple trusted HTML dashboards with Chromium and publish PNGs at stable
URLs. Each dashboard has its own source, dimensions, readiness, and refresh interval.
Shared outputs can rotate between dashboards or switch at specified local times.
Defaults for the first dashboard: **1600 × 1200**, **every 300 seconds**, **dashboard.png**.

The app queues enabled outputs on startup, reloads the source for every update, and
replaces each image atomically. Failed renders leave that output's previous image
intact and retry at the next interval or dashboard switch. One render runs at a
time to bound Chromium's resource use. The viewport is captured at
one image pixel per CSS pixel; overflowing content is cropped, not scaled.

## Install on Home Assistant OS

### From a GitHub repository

1. Open **Settings → Apps → Install app** (older versions:
   **Settings → Add-ons → Add-on store**).
2. Select **⋮ → Repositories**, add the GitHub repository URL, and close the dialog.
3. Install **Dashboard PNG** from the **Dashboard PNG for Home Assistant** section.
   Home Assistant builds the image on your device; allow time for the first download.
4. Continue with **Configure the dashboard** below.

Use the repository URL, such as
`https://github.com/YOUR_USERNAME/home-assistant-dashboard-png`, rather than a
file, ZIP download, or subdirectory URL. The project must first be published with
`repository.yaml` at its root; see the project's README for publishing commands.

### From local files

1. Copy this entire `dashboard_png` folder into the local apps folder:
   `/local_apps/dashboard_png` (called `/addons/dashboard_png` on older systems).
   Use Terminal & SSH or the Samba share.
2. Open **Settings → Apps → Install app**, then **Check for updates** in the menu.
   Older versions label this **Add-ons → Add-on store**. Install **Dashboard PNG**
   from **Local apps**.
3. Continue with **Configure the dashboard** below.

### Configure the dashboard

1. Create `dashboard/dashboard.html` inside the Home Assistant configuration
   directory, beside `configuration.yaml`. Put any relative assets in the same
   dashboard directory. You can use your existing HTML or the included example.
2. Set the initial options below, save, and start the app. Enable **Start on boot**.
3. Select **Open Web UI** to manage dashboards and shared outputs.
   Choose a dashboard with **Select dashboard**, edit its settings, and **Save changes**.
4. Read the app logs for `Updated dashboard.png from dashboard default (1600 × 1200)`.

If moving from a local install to a repository install, copy your options to the
new app and stop the old one before starting the new one. Home Assistant treats
them as separate installations, but both would write the same PNG by default.

The configuration directory is mounted as `/homeassistant` inside this app,
even when other Home Assistant tools call it `/config`. The output is therefore
`/homeassistant/www/dashboard.png`.

Open:

```text
http://YOUR_HOME_ASSISTANT:8123/local/dashboard.png
```

If `www` did not previously exist, restart Home Assistant Core once after the
first render so it registers the static directory.

## Upgrading from 1.0.x

Version 1.1.0 imports the existing app options into a dashboard named **Default**
on its first start. It keeps the configured source, filename, dimensions, interval,
readiness selector, and timezone. Your existing image URL continues to work.

After that import, **Open Web UI** is the place to manage all settings. The app's
old Configuration tab supplies initial values only; changing it no longer edits
the imported dashboard. Settings are stored atomically in `/data/dashboards.json`
and survive restarts, updates, and normal app backups. Do not delete the app's
data directory when updating. Invalid saved settings stop startup rather than
silently resetting your dashboards.

## Multiple dashboards

In **Open Web UI → Dashboards**:

1. Select **Add dashboard**, or **Duplicate** an existing dashboard.
2. Give it a name and configure the same fields used by the original app.
3. Choose a unique PNG filename, such as `office.png` or `family.png`.
4. Save. Enabled dashboards with **Publish its own PNG** checked render independently.

Use **Select dashboard** to switch between saved configurations. **Render now**
queues an immediate capture of saved settings. Previews and last-success/error
status are shown for each output. Saving applies changes without restarting the app.
An edit cancels in-progress work so an old configuration cannot publish afterward.

Turn off **Publish its own PNG** to use a dashboard only in shared outputs. It
must still be enabled to appear in an enabled shared output. Turn off **Enabled**
to pause a dashboard; first remove it from any enabled shared outputs. Deleting
a referenced dashboard is rejected until its schedule entries are removed.
Pausing or deleting an output retains its last PNG on disk.

## One URL showing different dashboards

In **Shared outputs**, select **Add shared output**, choose a name and filename,
then choose one of the following modes. You can create multiple shared outputs,
each with its own mode and schedule.

### Repeating rotation

Add dashboards in display order and set each duration in seconds. For example:

| Dashboard | Duration |
| --- | --- |
| Office | 300 seconds |
| Family | 600 seconds |

With filename `display.png`, clients always request `/local/display.png`: Office
is selected for five minutes, then Family for ten minutes, repeating continuously.
Editing the rotation starts a new cycle at the first entry. Its start time is
persisted, so restarting resumes the position appropriate to the current time;
it does not start over or replay missed slots. Editing an unrelated field keeps
the rotation position. Durations must be 10–86400 seconds.

### Time of day / week

Set the output's IANA timezone, then add switch times and weekdays. For example,
select Office at 07:00 Monday–Friday and Family at 18:00 Monday–Friday. Family
then remains selected overnight and throughout the weekend until Monday at 07:00.
Use all seven days for a daily schedule.

Entries are activation times, not start/end windows. The most recent applicable
entry stays selected until the next one, including after a restart. Two entries
cannot share a weekday and time. Daylight saving uses the first occurrence of a
repeated time; a missing spring-forward time moves forward by the clock gap
(for example, 02:30 becomes 03:30). If that coincides with another switch, the later
explicit clock time wins. The output timezone controls switching; each
dashboard's browser timezone still controls the dates/times inside its HTML.

### Refresh and publication behavior

The selected dashboard's refresh interval controls updates within its slot. A
dashboard change queues a new capture immediately, even if the normal refresh
interval has not elapsed. Actual publication follows successful rendering; allow
for capture time and any other queued outputs. Choose durations long enough for
your dashboards to render. If a different dashboard is selected when a capture
finishes, that capture is discarded. Failures keep the last successful image, so a client may
see the previous dashboard until a capture succeeds.

Each shared image inherits the selected dashboard's width and height. Configure
the same dimensions on its participating dashboards if the client needs a fixed
size. Output filenames must have one owner. To rotate into the original
`dashboard.png`, uncheck **Publish its own PNG** on Default, then give the shared
output that filename. Private source HTML can be reused by multiple dashboards.

At most 32 dashboard configurations and 32 shared outputs are supported, with up
to 100 entries per rotation or time schedule. One worker processes due outputs;
these limits do not guarantee the hardware can meet every configured interval.

## Dashboard settings / initial options

```yaml
source: /homeassistant/dashboard/dashboard.html
base_url: http://homeassistant:8123
width: 1600
height: 1200
filename: dashboard.png
interval_seconds: 300
render_delay_ms: 2000
timeout_seconds: 60
ready_selector: ""
timezone: America/Toronto
```

| Option | Meaning |
| --- | --- |
| `source` | An absolute local HTML path inside `/homeassistant`, or a fully qualified HTTP(S) URL. |
| `base_url` | Reachable HTTP(S) origin used when rendering local HTML; use your HA address and port. No path, credentials, query, or fragment. Ignored for URL sources. |
| `width`, `height` | Exact output dimensions, 1–8192 each; at most 33,554,432 total pixels. |
| `filename` | Stable PNG basename, such as `dashboard.png`. No directories; always written in `www`. |
| `interval_seconds` | Start-to-start refresh interval, 10–86400 seconds. Slow renders skip missed intervals. |
| `render_delay_ms` | Extra settling time after page load and optional readiness, 0–60000 ms. |
| `timeout_seconds` | Total capture deadline including browser startup, 5–300 seconds. Must exceed the settling delay. |
| `ready_selector` | Optional CSS selector that must become visible before capture. Empty disables this check. |
| `timezone` | IANA timezone for browser dates and times, e.g. `America/Toronto` or `Europe/London`. |

These options initialize Default once. Thereafter, edit these fields through
**Open Web UI** and save; no restart is needed. Edits to source HTML and assets
are picked up automatically on the next render. **Render now** queues an immediate
render. Restarting queues all enabled outputs.

## Local HTML and Home Assistant APIs

The browser first connects to a private path under `base_url` to establish the real
network origin, then replaces that page with the local UTF-8 HTML. A 404 response
for this initial private path is expected and ignored. If Chromium rejects an empty
error response, the app uses the origin's landing page instead. The HTML is injected only
inside the browser; it is not uploaded or publicly hosted. This gives it the same
origin as Home Assistant, so `window.location.origin` and `fetch("/api/...")` work
as expected, including Chromium's Local Network Access checks. Relative assets
are read from the HTML's directory; absolute paths such as `/local/icon.svg` are
requested from `base_url`.

Local sources require a reachable `base_url`, even if the HTML itself is static.
If it redirects to a different origin, configure the final origin directly.

Keep source HTML containing credentials in `dashboard/`, **outside `www/`**.
Only the resulting image needs to be public. The app does not provide authentication
tokens or automatically log into Lovelace. HTML that calls HA APIs must supply its
own authentication. A URL source must render without an interactive login; otherwise
the image will show the login screen.

Use only HTML and URLs you trust. The Chromium container has write access to the
Home Assistant configuration directory. Only 64-bit ARM and x86-64 are supported.

## Waiting for live data

The default waits for page load, two seconds of settling, fonts, and HTML images.
For reliable asynchronous data, mark the page ready only after successful loading:

```javascript
await loadDashboardData();
document.body.dataset.ready = "true";
```

Then set:

```yaml
ready_selector: 'body[data-ready="true"]'
```

For the existing Focus/Schedule HTML, the visible selector
`#connection-status:text-is("SYNCED")` can gate capture on successful API loading.
A selector must become visible; attaching it to a hidden element will time out.

A timeout, HTTP error, failed image load, or write failure preserves the previous
PNG. The renderer cannot infer whether an application intentionally displays an
error message; use the readiness marker to distinguish successful data loading.
External CSS and script failures can also leave a page partly styled unless your
readiness logic detects them.

## Polling the same URL

The PNG is rendered on the app's schedule, independently of image requests.
Consumers can repeatedly fetch the exact same URL. Request revalidation or bypass
the consumer's cache so that it does not keep displaying a cached image:

```javascript
const response = await fetch("/local/dashboard.png", { cache: "no-store" });
const imageBlob = await response.blob();
```

For a simple HTTP client:

```sh
curl --fail -H 'Cache-Control: no-cache' \
  http://YOUR_HOME_ASSISTANT:8123/local/dashboard.png -o dashboard.png
```

Home Assistant controls the HTTP headers on `/local/`; this app cannot change
them. A client or proxy that ignores revalidation may need its own cache disabled.
Files in `www`, including this PNG, are accessible without Home Assistant login.

## Troubleshooting

- **HTML has data but the PNG does not:** use version 1.0.2 or later. Earlier
  versions could trigger Chromium's local-network restrictions when loading
  private HTML, blocking API calls. Also check that the private `source` file is
  the same version as the HTML viewed in your browser. A browser may retain an
  older HTML or PNG because Home Assistant serves `/local/` with cache headers;
  force a fresh fetch. `SYNCED` only means the HTML's data-loading code reported
  success; check its response parsing if it reports empty lists unexpectedly.
- **Local source file does not exist:** `source` is a path inside the app container.
  `/homeassistant` is the mounted Home Assistant configuration folder. For example,
  a file at `/root/homeassistant/dashboard/dashboard.html` in your terminal is
  `/homeassistant/dashboard/dashboard.html` inside the app. A file under
  `/root/local_apps/` is in a separate directory that this app does not mount.
  Copy the HTML and its relative assets into the Home Assistant configuration
  folder and set `source` to that location under `/homeassistant`. The startup log
  and missing-file error show the configured path.
- **Timeout waiting for ready_selector:** temporarily set `ready_selector: ""`,
  restart, and inspect the resulting PNG to see the page's state. This diagnostic
  capture may include loading or error content. The Focus/Schedule selector
  `#connection-status:text-is("SYNCED")` only matches a successful data refresh;
  `PREVIEW`, `LOADING`, `PARTIAL`, and `ERROR` do not match. Fix the underlying
  data problem before restoring the selector. Observed HTTP 401/403 responses in
  the log indicate authentication or permission errors in page resources/API calls.
- **Other capture timeouts:** version 1.0.1 identifies the stage (page load, fonts,
  images, screenshot, or browser startup/cleanup). Check that resource or step
  before increasing `timeout_seconds`. Network and JavaScript error counts are
  included without response bodies, URLs, or script error messages.
- **404 image:** check the app log; no image exists until the first successful
  render. Restart Core if `www` was just created.
- **Cannot load HA:** `homeassistant` resolves inside the Supervisor network.
  For standalone Docker use a LAN hostname/IP reachable from that container.
  `localhost` refers to the renderer container itself.
- **TLS errors:** use a certificate trusted by Chromium; certificate verification
  is enabled.
- **Blank or incomplete content:** set a readiness selector, raise the timeout, or
  check the dashboard's API calls, fonts, CSS, and scripts.
- **Wrong date/time:** set `timezone`.
- **Unexpected crop:** make your HTML fit the requested viewport. The existing
  Inky dashboard is fixed at 1600 × 1200; changing the output dimensions does not
  automatically resize its CSS layout.
- **Stale image:** inspect logs for failed renders and check consumer/proxy caching.

## References

- [Home Assistant app repositories](https://contributors.home-assistant.io/apps/guides/repository/)
- [Home Assistant app installation](https://contributors.home-assistant.io/apps/first-app/)
- [App configuration and directory mappings](https://contributors.home-assistant.io/apps/reference/configuration/)
- [Home Assistant ingress](https://contributors.home-assistant.io/apps/guides/ingress/)
- [Home Assistant static files](https://www.home-assistant.io/integrations/http/#hosting-files)
- [Playwright screenshots](https://playwright.dev/python/docs/screenshots)
- [Playwright container requirements](https://playwright.dev/python/docs/docker)
