# Home Assistant Dashboard PNG

A Home Assistant OS app/add-on that renders trusted HTML with Chromium into
`www/dashboard.png`, refreshed every five minutes by default. Width, height,
filename, source, readiness, and interval are configurable.

The defaults match the 1600 × 1200 Inky dashboard in the surrounding workspace.
That private dashboard and its credentials are deliberately not bundled here.

**[Installation and configuration](dashboard_png/DOCS.md)**

## Install from GitHub

After this project is published to a GitHub repository:

1. In Home Assistant, open **Settings → Apps → Install app** (older versions:
   **Settings → Add-ons → Add-on store**).
2. Open **⋮ → Repositories**, paste the repository URL, and select **Add**:
   `https://github.com/YOUR_USERNAME/home-assistant-dashboard-png`.
3. Find **Dashboard PNG** under **Dashboard PNG for Home Assistant** and install it.
4. Follow [Configure the dashboard](dashboard_png/DOCS.md#configure-the-dashboard)
   to place your HTML, set the dimensions and schedule, and start the app.

Home Assistant builds the included Dockerfile on your device. No separately
published container image is required. Installation downloads Chromium's container
image, so the first build can take several minutes.

This uses the Home Assistant app/add-on store. HACS is for a different type of
extension and is not involved.

## Publish your own repository

Create an empty public GitHub repository, for example
`home-assistant-dashboard-png`. Publish the **contents of this project folder** so
that `repository.yaml` and the `dashboard_png/` directory are at the repository
root. Uploading only the ZIP file will not make the app discoverable.

From this project directory, replace `YOUR_USERNAME` and run:

```sh
git init -b main
git add README.md repository.yaml .gitignore compose.yaml dashboard_png examples tests
git commit -m "Add Dashboard PNG Home Assistant app"
git remote add origin https://github.com/YOUR_USERNAME/home-assistant-dashboard-png.git
git push -u origin main
```

The included `repository.yaml` supplies the required repository name. Once you
have a repository URL, you can optionally add `url` and `maintainer` fields there,
and a `url` field in `dashboard_png/config.yaml` for the app's homepage.

Keep your actual dashboard HTML and credentials in your Home Assistant
configuration directory. The `examples/` folder contains a safe sample to publish.

For later releases, increase `version` in `dashboard_png/config.yaml`, update the
version label in its Dockerfile and the changelog, then push. Home Assistant's
**Check for updates** discovers the new version and **Update** rebuilds it.

See the [official repository guide](https://contributors.home-assistant.io/apps/guides/repository/).

## Layout

- `repository.yaml`: Home Assistant app-store repository metadata.
- `dashboard_png/`: installable app, Dockerfile, renderer, and documentation.
- `examples/`: credential-free HTML and options.
- `tests/`: configuration, publication, scheduler, and real-browser tests.
- `compose.yaml`: standalone deployment for Home Assistant Container.

## Home Assistant Container / standalone Docker

Apps require Home Assistant OS. Container users can run this renderer alongside
Home Assistant with a shared configuration directory:

1. Copy `examples/options.json` to `options.json` in this project.
2. Place the source at `dashboard/dashboard.html` inside your HA configuration
   directory. Use your own trusted dashboard or `examples/dashboard.html`.
3. Edit `options.json`: set `base_url` to the HA LAN address reachable from Docker.
   Keep `source` as `/homeassistant/dashboard/dashboard.html`; that is the path
   inside the renderer container.
4. Start:

```sh
export HA_CONFIG_DIR=/absolute/path/to/homeassistant
docker compose up -d --build
docker compose logs -f dashboard-png
```

The output is `$HA_CONFIG_DIR/www/dashboard.png`, served by HA at
`http://YOUR_HOME_ASSISTANT:8123/local/dashboard.png`.
Restart Core once if the `www` directory is new.

## Development

Use Python 3.11 or newer (the container provides Python 3.12).

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r dashboard_png/requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m unittest discover -s tests -v
RUN_BROWSER_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

The ordinary suite uses the standard library. The browser suite runs offline
against temporary HTML and a local HTTP test server; it checks actual PNG
dimensions, updated content at one filename, source assets, data readiness,
failure preservation, and HTTP errors.

To use an installed Chrome for browser tests, set `BROWSER_EXECUTABLE` to its
executable. To render once for debugging:

```sh
.venv/bin/python dashboard_png/renderer.py \
  --options options.json --config-dir /absolute/path/to/homeassistant --once
```

Exit status: 0 = success; 1 = failed one-shot render; 2 = invalid configuration.
The scheduled process retries render errors. SIGINT/SIGTERM cancels active work
and closes Chromium. The container image and Python Playwright version are pinned
together; update both when upgrading Playwright.
