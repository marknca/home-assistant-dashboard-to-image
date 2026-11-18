# Changelog

## 1.0.1

- Identify the exact capture stage on timeout, including readiness, fonts, and images.
- Report HTTP error codes and network/JavaScript failure counts without logging credentials or page content.
- Check local source files before starting Chromium and distinguish missing files, directories, and paths outside the mount.
- Log the configured local path, timeout, and whether readiness checking is enabled at startup.

## 1.0.0

- Scheduled Chromium rendering of local HTML or HTTP(S) pages.
- Configurable PNG dimensions and a stable filename in Home Assistant www.
- Atomic updates, bounded rendering, graceful shutdown, and retries.
