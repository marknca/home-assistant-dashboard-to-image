# Changelog

## 1.1.0

- Add a Home Assistant ingress UI with dashboard selection, creation, duplication, settings, previews, and immediate rendering.
- Run multiple dashboard configurations and independent PNG refresh schedules in one installation.
- Add shared image outputs with ordered rotations or timezone-aware daily/weekly switch times.
- Preserve rotation position across restarts, validate filename ownership and references, and prevent outdated captures from publishing after edits or schedule changes.
- Import existing options into Default once and persist configuration in private app data; later changes use Open Web UI.
- Keep a single render worker, preserve successful images on failure, and add scheduler, API, migration, and real-browser UI coverage.

## 1.0.2

- Fix Chromium blocking Home Assistant API requests from privately rendered local HTML: establish the real base URL origin before injecting the HTML.
- Keep private HTML and assets inside the browser, preserve relative asset paths, and reject redirects to another origin.
- Add a browser regression test for same-origin API access through a LAN-style hostname.

## 1.0.1

- Identify the exact capture stage on timeout, including readiness, fonts, and images.
- Report HTTP error codes and network/JavaScript failure counts without logging credentials or page content.
- Check local source files before starting Chromium and distinguish missing files, directories, and paths outside the mount.
- Log the configured local path, timeout, and whether readiness checking is enabled at startup.

## 1.0.0

- Scheduled Chromium rendering of local HTML or HTTP(S) pages.
- Configurable PNG dimensions and a stable filename in Home Assistant www.
- Atomic updates, bounded rendering, graceful shutdown, and retries.
