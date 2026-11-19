"use strict";
const $ = id => document.getElementById(id);
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
let settings, defaults, section = "dashboards", selected, dirty = false, statuses = {}, previewVersion = "";
let fieldId = 0;

function node(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") element.className = value;
    else if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
    else if (key in element) element[key] = value;
    else element.setAttribute(key, value);
  }
  for (const child of children) element.append(child);
  return element;
}
function changed() { dirty = true; $("save").textContent = "Save changes •"; $("render").disabled = true; }
function notice(message, error = false) {
  $("notice").textContent = message; $("notice").classList.toggle("error", error); $("notice").hidden = false;
}
async function api(path, method = "GET", body) {
  const response = await fetch(`api/${path}`, { method, cache: "no-store", headers: body === undefined ? {} : {
    "Content-Type": "application/json", "X-Dashboard-PNG": "1"
  }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}
function current() { return settings?.[section].find(row => row.id === selected); }
function newId(prefix) {
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  return prefix + "-" + [...bytes].map(v => v.toString(16).padStart(2, "0")).join("");
}
function availableFilename(prefix) {
  const used = new Set([...settings.dashboards.map(d => d.config.filename), ...settings.outputs.map(o => o.filename)]);
  let count = 1; while (used.has(`${prefix}-${count}.png`)) count++;
  return `${prefix}-${count}.png`;
}
function field(label, object, key, { type = "text", wide = false, min, max, options, help } = {}) {
  let input;
  if (options) {
    input = node("select", {}, ...options.map(([value, text]) => node("option", { value }, text)));
    input.value = object[key];
  } else input = node("input", { type, value: object[key] ?? "" });
  if (min !== undefined) input.min = min;
  if (max !== undefined) input.max = max;
  input.addEventListener("input", () => { object[key] = type === "number" ? Number(input.value) : input.value; changed(); });
  const labelId = `field-label-${++fieldId}`;
  input.setAttribute("aria-labelledby", labelId);
  const wrap = node("label", { class: wide ? "wide" : "" }, node("span", { id: labelId }, label), input);
  if (help) wrap.append(node("span", { class: "help" }, help));
  return wrap;
}
function check(label, object, key) {
  return node("label", { class: "check" }, node("input", { type: "checkbox", checked: object[key], onchange: event => {
    object[key] = event.target.checked; changed(); showOutput();
  } }), label);
}
function refreshSelection() {
  const rows = settings[section];
  if (!rows.some(row => row.id === selected)) selected = rows[0]?.id;
  $("selection").replaceChildren(...rows.map(row => node("option", { value: row.id }, row.name)));
  if (!rows.length) $("selection").append(node("option", { value: "" }, "None yet"));
  $("selection").value = selected || "";
  $("select-label").textContent = section === "dashboards" ? "Select dashboard" : "Select shared output";
  $("add").textContent = section === "dashboards" ? "Add dashboard" : "Add shared output";
  $("delete").disabled = $("duplicate").disabled = !current();
  previewVersion = "";
  renderEditor(); showOutput();
}
function renderEditor() {
  const row = current(); const editor = $("editor"); editor.replaceChildren();
  if (!row) { editor.append(node("p", { class: "empty" }, "Add a configuration to get started.")); return; }
  editor.append(node("h2", {}, section === "dashboards" ? "Dashboard settings" : "Shared image settings"));
  const checks = node("div", { class: "checks" }, check("Enabled", row, "enabled"));
  if (section === "dashboards") checks.append(check("Publish its own PNG", row, "publish"));
  editor.append(checks);
  const fields = node("div", { class: "fields" }, field("Name", row, "name", { wide: true }));
  if (section === "dashboards") {
    const c = row.config;
    fields.append(
      field("HTML path or page URL", c, "source", { wide: true, help: "Private HTML paths start with /homeassistant/." }),
      field("Home Assistant base URL", c, "base_url", { wide: true }),
      field("Width (pixels)", c, "width", { type: "number", min: 1, max: 8192 }),
      field("Height (pixels)", c, "height", { type: "number", min: 1, max: 8192 }),
      field("PNG filename", c, "filename"),
      field("Refresh interval (seconds)", c, "interval_seconds", { type: "number", min: 10, max: 86400 }),
      field("Settling delay (milliseconds)", c, "render_delay_ms", { type: "number", min: 0, max: 60000 }),
      field("Capture timeout (seconds)", c, "timeout_seconds", { type: "number", min: 5, max: 300 }),
      field("Ready selector", c, "ready_selector", { wide: true, help: "Optional. Wait for this element before capturing the image." }),
      field("Browser timezone", c, "timezone", { wide: true })
    );
    editor.append(fields, node("p", { class: "help" }, "Turn off “Publish its own PNG” to use this dashboard only in shared outputs. Its refresh interval also controls refreshes while it is selected there."));
  } else {
    fields.append(field("Shared PNG filename", row, "filename"), field("Schedule timezone", row, "timezone"));
    const mode = field("Switch dashboards using", row, "mode", { wide: true, options: [["rotation", "Repeating rotation"], ["schedule", "Time of day / week"]] });
    mode.querySelector("select").addEventListener("change", () => {
      if (row.mode === "rotation" && !row.rotation.length && settings.dashboards.length) row.rotation.push({ dashboard_id: settings.dashboards[0].id, duration_seconds: 300 });
      if (row.mode === "schedule" && !row.schedule.length && settings.dashboards.length) row.schedule.push({ dashboard_id: settings.dashboards[0].id, at: "07:00", days: [...DAYS] });
      renderEditor();
    });
    fields.append(mode); editor.append(fields);
    editor.append(node("h3", {}, row.mode === "rotation" ? "Rotation order" : "Switch times"));
    editor.append(node("p", { class: "help" }, row.mode === "rotation" ?
      "Repeat these dashboards in order. Saving a changed rotation starts with its first entry; restarting the app preserves its position." :
      "Each entry switches the image at the specified local time. That dashboard stays selected until the next switch, including overnight and across weekends."));
    const entries = row.mode === "rotation" ? row.rotation : row.schedule;
    const steps = node("div", { class: "steps" });
    entries.forEach((entry, index) => steps.append(stepEditor(entry, entries, index, row.mode)));
    editor.append(steps, node("button", { type: "button", disabled: !settings.dashboards.length, onclick: () => {
      entries.push(row.mode === "rotation" ? { dashboard_id: settings.dashboards[0].id, duration_seconds: 300 } : { dashboard_id: settings.dashboards[0].id, at: "19:00", days: [...DAYS] });
      changed(); renderEditor();
    } }, "Add entry"));
    if (!settings.dashboards.length) editor.append(node("p", { class: "help" }, "Add a dashboard first."));
  }
}
function stepEditor(entry, entries, index, mode) {
  const box = node("div", { class: "step" });
  const options = settings.dashboards.map(d => [d.id, d.name + (d.enabled ? "" : " (disabled)")]);
  box.append(node("div", { class: "step-fields" }, field("Dashboard", entry, "dashboard_id", { options }),
    mode === "rotation" ? field("Duration (seconds)", entry, "duration_seconds", { type: "number", min: 10, max: 86400 }) : field("Switch at", entry, "at", { type: "time" })));
  if (mode === "schedule") {
    const days = node("div", { class: "days" });
    for (const day of DAYS) days.append(node("label", { class: "check" }, node("input", { type: "checkbox", checked: entry.days.includes(day), onchange: event => {
      entry.days = event.target.checked ? [...entry.days, day] : entry.days.filter(d => d !== day); changed();
    } }), day[0].toUpperCase() + day.slice(1)));
    box.append(days);
  }
  const move = direction => { [entries[index], entries[index + direction]] = [entries[index + direction], entries[index]]; changed(); renderEditor(); };
  box.append(node("div", { class: "step-actions" },
    node("button", { disabled: index === 0, onclick: () => move(-1), "aria-label": `Move entry ${index + 1} up` }, "Move up"),
    node("button", { disabled: index === entries.length - 1, onclick: () => move(1), "aria-label": `Move entry ${index + 1} down` }, "Move down"),
    node("button", { class: "danger", onclick: () => { entries.splice(index, 1); changed(); renderEditor(); } }, "Remove")));
  return box;
}
function showOutput() {
  const row = current(); const kind = section === "dashboards" ? "dashboard" : "output";
  const key = `${kind}:${row?.id}`; const status = statuses[key];
  const publishes = row && row.enabled && (kind === "output" || row.publish);
  $("render").disabled = !publishes || dirty;
  $("output-description").textContent = kind === "output" ? "One stable URL, with the selected dashboard changing on your schedule." : "A separate image using this dashboard’s dimensions and refresh interval.";
  $("output-url").textContent = row ? "/local/" + (row.config?.filename || row.filename) : "No output selected";
  if (!row || (kind === "dashboard" && !row.publish)) {
    $("status").textContent = row ? "Used in shared outputs only." : "";
    $("preview").hidden = true; $("preview-empty").hidden = false; return;
  }
  const text = [];
  if (dirty) text.push("Save changes to apply this configuration.");
  if (!row.enabled) text.push("Paused. The existing PNG is retained.");
  if (status?.rendering) text.push("Rendering…");
  else if (status?.queued) text.push("Queued for rendering.");
  if (status?.last_success) text.push(`Last updated: ${new Date(status.last_success * 1000).toLocaleString()}`);
  if (kind === "output" && status?.published_dashboard) text.push("Showing: " + (settings.dashboards.find(d => d.id === status.published_dashboard)?.name || status.published_dashboard));
  if (status?.next_switch) text.push(`Next switch: ${new Date(status.next_switch * 1000).toLocaleString()}`);
  if (status?.last_error) text.push(status.last_error + " The previous image is retained.");
  $("status").textContent = text.join("\n") || "Waiting for the first capture.";
  const version = `${key}:${status?.last_success || 0}`;
  if (version !== previewVersion) {
    previewVersion = version;
    $("preview").src = `api/preview?kind=${kind}&id=${encodeURIComponent(row.id)}&v=${status?.last_success || Date.now()}`;
  }
}
async function poll() {
  try { statuses = await api("status"); showOutput(); }
  catch { $("status").textContent = "Connection lost. Retrying…"; }
}
for (const name of ["dashboards", "outputs"]) $(name + "-tab").addEventListener("click", () => {
  section = name; selected = undefined;
  for (const other of ["dashboards", "outputs"]) { $(other + "-tab").classList.toggle("active", other === name); $(other + "-tab").setAttribute("aria-pressed", String(other === name)); }
  refreshSelection();
});
$("selection").addEventListener("change", event => { selected = event.target.value; refreshSelection(); });
$("add").addEventListener("click", () => {
  const id = newId(section === "dashboards" ? "dashboard" : "output");
  const row = section === "dashboards" ? { id, name: "New dashboard", enabled: true, publish: true,
    config: { ...defaults, filename: availableFilename("dashboard") } } : {
    id, name: "New shared output", enabled: true, filename: availableFilename("rotation"), mode: "rotation", timezone: defaults.timezone,
    rotation: settings.dashboards.length ? [{ dashboard_id: settings.dashboards[0].id, duration_seconds: 300 }] : [], schedule: []
  };
  settings[section].push(row); selected = id; changed(); refreshSelection();
});
$("duplicate").addEventListener("click", () => {
  const row = structuredClone(current()); row.id = newId(section === "dashboards" ? "dashboard" : "output"); row.name += " copy";
  if (row.config) row.config.filename = availableFilename("dashboard"); else row.filename = availableFilename("rotation");
  settings[section].push(row); selected = row.id; changed(); refreshSelection();
});
$("delete").addEventListener("click", () => {
  const row = current();
  if (!confirm(`Delete “${row.name}”? Existing PNG files will be kept.`)) return;
  settings[section] = settings[section].filter(item => item.id !== row.id); selected = undefined; changed(); refreshSelection();
});
$("save").addEventListener("click", async () => {
  $("save").disabled = true;
  try {
    const result = await api("settings", "PUT", settings); settings = result.settings; dirty = false;
    $("save").textContent = "Save changes"; notice("Saved. Schedules are active; no restart is needed."); refreshSelection(); await poll();
  } catch (error) { notice(error.message, true); }
  finally { $("save").disabled = false; }
});
$("render").addEventListener("click", async () => {
  try { await api("render", "POST", { key: `${section === "dashboards" ? "dashboard" : "output"}:${selected}` }); await poll(); }
  catch (error) { notice(error.message, true); }
});
$("preview").addEventListener("load", () => { $("preview").hidden = false; $("preview-empty").hidden = true; });
$("preview").addEventListener("error", () => { $("preview").hidden = true; $("preview-empty").hidden = false; });
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
(async () => {
  try { const result = await api("settings"); settings = result.settings; defaults = result.defaults; refreshSelection(); await poll(); setInterval(poll, 3000); }
  catch (error) { notice(error.message, true); for (const id of ["add", "duplicate", "delete", "save", "render"]) $(id).disabled = true; }
})();
