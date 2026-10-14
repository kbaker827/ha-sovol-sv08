/*
 * Sovol SV08 dashboard card.
 *
 * Shipped with the sovol_sv08 integration and loaded automatically, so
 * "custom:sovol-sv08-card" can be added to any dashboard. Shows the live
 * camera, print progress, layers, time left, ETA, nozzle/bed temperatures,
 * gantry leveling, preheat presets and print controls for one printer.
 */

const CARD_VERSION = "1.2.0";
const DOMAIN = "sovol_sv08";

const ACCENTS = {
  sovol: "#009bb9", // Sovol's brand blue (sovol3d.com)
  theme: "var(--primary-color)",
  orange: "#ff8a00",
  blue: "#2f80ed",
  green: "#27ae60",
  purple: "#8e5cf6",
  red: "#eb5757",
  teal: "#14b8a6",
  pink: "#ec4899",
};

const STATUS = {
  printing: { label: "Printing", icon: "mdi:printer-3d-nozzle", tone: "accent" },
  paused: { label: "Paused", icon: "mdi:pause-circle", tone: "warning" },
  complete: { label: "Complete", icon: "mdi:check-circle", tone: "success" },
  cancelled: { label: "Cancelled", icon: "mdi:close-circle", tone: "muted" },
  error: { label: "Error", icon: "mdi:alert-circle", tone: "error" },
  standby: { label: "Ready", icon: "mdi:printer-3d", tone: "muted" },
  offline: { label: "Offline", icon: "mdi:lan-disconnect", tone: "error" },
};

// One-tap preheat (nozzle / bed, °C). The SV08's bed tops out at 100 °C.
// Override with `preheat_presets`.
const DEFAULT_PREHEAT_PRESETS = [
  { name: "PLA", nozzle: 210, bed: 60 },
  { name: "PETG", nozzle: 240, bed: 75 },
  { name: "ABS", nozzle: 255, bed: 100 },
  { name: "TPU", nozzle: 225, bed: 50 },
];

const CONFIRM_MS = 4000;
const SNAPSHOT_REFRESH_MS = 5000;
const LIVE_FIRST_FRAME_MS = 8000;
const RELOAD_RETRY_MS = 15000;

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );

const num = (state) => {
  if (!state || state.state === "unavailable" || state.state === "unknown") return null;
  const value = Number(state.state);
  return Number.isFinite(value) ? value : null;
};

const formatDuration = (seconds) => {
  if (seconds == null || seconds < 0) return "–";
  const s = Math.round(seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${s}s`;
};

// Update `parent` to match `html` while keeping existing nodes wherever
// possible. Moonraker pushes several updates a second; rebuilding buttons
// with innerHTML each time replaced the element under the user's finger
// mid-tap, so taps were lost.
const patchHTML = (parent, html) => {
  const template = document.createElement("template");
  template.innerHTML = html;
  patchChildren(parent, template.content);
};

const patchChildren = (from, to) => {
  const toNodes = Array.from(to.childNodes);
  toNodes.forEach((toNode, i) => {
    const fromNode = from.childNodes[i];
    if (!fromNode) {
      from.appendChild(toNode.cloneNode(true));
    } else if (fromNode.nodeType !== toNode.nodeType || fromNode.nodeName !== toNode.nodeName) {
      from.replaceChild(toNode.cloneNode(true), fromNode);
    } else if (fromNode.nodeType === Node.ELEMENT_NODE) {
      for (const { name } of Array.from(fromNode.attributes)) {
        if (!toNode.hasAttribute(name)) fromNode.removeAttribute(name);
      }
      for (const { name, value } of Array.from(toNode.attributes)) {
        if (fromNode.getAttribute(name) !== value) fromNode.setAttribute(name, value);
      }
      patchChildren(fromNode, toNode);
    } else if (fromNode.nodeValue !== toNode.nodeValue) {
      fromNode.nodeValue = toNode.nodeValue;
    }
  });
  while (from.childNodes.length > toNodes.length) from.removeChild(from.lastChild);
};

// Mirrors object_label() in pysovol/parser.py.
const label = (name) =>
  String(name).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

const prettyFilename = (name) =>
  String(name || "")
    .split("/")
    .pop()
    .replace(/\.(gcode|g|gco|bgcode)$/i, "");

// Presets are in °C; a number entity shown in °F takes °F values.
const toUnit = (celsius, unit) => (unit === "°F" ? Math.round((celsius * 9) / 5 + 32) : celsius);

class SovolSV08Card extends HTMLElement {
  static getConfigElement() {
    return document.createElement("sovol-sv08-card-editor");
  }

  static getStubConfig() {
    return {
      show_camera: true,
      camera_mode: "live",
      show_controls: true,
      show_preheat: true,
      accent_color: "sovol",
    };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._printers = null;
    this._loading = false;
    this._armed = null;
    this._signature = "";
    this._cameraSrc = "";
    this._cameraFallback = false;
    this._snapshotTimer = null;
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
  }

  setConfig(config) {
    this._config = {
      ...SovolSV08Card.getStubConfig(),
      ...(config && typeof config === "object" ? config : {}),
    };
    this._signature = "";
    this._cameraFallback = false;
    this._buildSkeleton();
    if (this._hass) this._safeUpdate();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._printers && !this._loading) this._loadPrinters();
    this._safeUpdate();
  }

  // Home Assistant replaces a card that throws while updating with a blank
  // "Configuration error" box for good. Catch everything instead, keep the
  // card alive, and show what went wrong so it can be reported.
  _safeUpdate() {
    try {
      this._update();
      this._lastError = null;
    } catch (err) {
      this._signature = "";
      const message = (err && (err.message || String(err))) || "Unknown error";
      if (message !== this._lastError) {
        this._lastError = message;
        console.error("sovol-sv08-card:", err);
      }
      if (this._contentEl) {
        this._contentEl.innerHTML = `
          <div class="alert"><ha-icon icon="mdi:alert-circle"></ha-icon>
          <span>Sovol SV08 card error: ${esc(message)}</span></div>`;
      }
    }
  }

  connectedCallback() {
    this._connected = true;
    this._signature = "";
    if (this._printers) this._watchRegistry();
    if (this._hass) this._safeUpdate();
  }

  disconnectedCallback() {
    // Close the MJPEG stream so hidden cards don't keep the camera busy.
    this._connected = false;
    this._setCamera("");
    clearTimeout(this._retryTimer);
    clearTimeout(this._registryTimer);
    if (typeof this._unsubRegistry === "function") this._unsubRegistry();
    this._unsubRegistry = null;
  }

  getCardSize() {
    return this._config?.show_camera === false ? 6 : 10;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  // ------------------------------------------------------------------
  // Data
  // ------------------------------------------------------------------

  async _loadPrinters() {
    this._loading = true;
    clearTimeout(this._retryTimer);
    try {
      const result = await this._hass.callWS({ type: `${DOMAIN}/entities` });
      this._printers = result.printers || [];
      this._error = null;
    } catch (err) {
      // Typically Home Assistant still starting; try again shortly.
      this._error = `Could not load Sovol SV08 printers: ${err.message || err}`;
      this._printers = this._printers || [];
      this._retryTimer = setTimeout(() => this._loadPrinters(), RELOAD_RETRY_MS);
    }
    this._loading = false;
    this._watchRegistry();
    this._signature = "";
    this._safeUpdate();
  }

  // Pick up entities added later (a new fan, a re-enabled sensor).
  async _watchRegistry() {
    if (this._unsubRegistry || !this._hass?.connection) return;
    this._unsubRegistry = true;
    try {
      this._unsubRegistry = await this._hass.connection.subscribeEvents(() => {
        clearTimeout(this._registryTimer);
        this._registryTimer = setTimeout(() => this._loadPrinters(), 1000);
      }, "entity_registry_updated");
    } catch (err) {
      this._unsubRegistry = null; // not allowed for this user; reload the page instead
    }
  }

  get _printer() {
    if (!this._printers || !this._printers.length) return null;
    const wanted = this._config.entry_id;
    return (wanted && this._printers.find((p) => p.entry_id === wanted)) || this._printers[0];
  }

  _eid(role) {
    return this._printer?.entities?.[role];
  }

  _state(role) {
    const eid = this._eid(role);
    return eid ? this._hass.states[eid] : undefined;
  }

  _roles(prefix) {
    return Object.keys(this._printer?.entities || {})
      .filter((role) => role.startsWith(prefix))
      // Skip leftovers the printer no longer provides; HA keeps them as
      // "restored" entities.
      .filter((role) => !this._state(role)?.attributes?.restored)
      .sort();
  }

  // ------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------

  _buildSkeleton() {
    const accent = ACCENTS[this._config.accent_color] || this._config.accent_color || ACCENTS.sovol;
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <ha-card style="--sv-accent:${esc(accent)}">
        <div class="camera" data-action="camera" hidden>
          <img alt="Printer camera" />
          <div class="camera-badge"><span class="dot"></span><span class="camera-label">LIVE</span></div>
          <div class="camera-error"><ha-icon icon="mdi:camera-off"></ha-icon><span>Camera unavailable</span></div>
        </div>
        <div class="content"></div>
      </ha-card>`;
    this._contentEl = this.shadowRoot.querySelector(".content");
    this._cameraEl = this.shadowRoot.querySelector(".camera");
    this._imgEl = this.shadowRoot.querySelector(".camera img");
    this._imgEl.addEventListener("error", () => {
      // Live MJPEG failed: fall back to refreshed snapshots.
      if (!this._cameraFallback && this._config.camera_mode !== "snapshot") {
        this._cameraFallback = true;
        this._cameraSrc = "";
        this._updateCamera();
        return;
      }
      if (this._cameraSrc) this._cameraEl.classList.add("is-error");
    });
    this._imgEl.addEventListener("load", () => this._cameraEl.classList.remove("is-error"));
    this._cameraSrc = "";
  }

  _update() {
    if (!this._hass || !this._config || !this._contentEl) return;
    if (!this._printers) {
      this._contentEl.innerHTML = `<div class="empty">Loading…</div>`;
      return;
    }
    if (!this._printer) {
      this._contentEl.innerHTML = `<div class="empty">${esc(
        this._error || "No Sovol SV08 found. Add the Sovol SV08 integration first."
      )}</div>`;
      return;
    }

    this._updateCamera();

    // Only re-render when something the card shows has changed.
    const signature =
      Object.values(this._printer.entities)
        .map((eid) => {
          const s = this._hass.states[eid];
          return s ? `${s.state}|${s.last_updated ?? ""}` : "";
        })
        .join(";") + `|${this._armed?.action ?? ""}`;
    if (signature === this._signature) return;
    this._signature = signature;
    patchHTML(this._contentEl, this._renderContent());
  }

  _updateCamera() {
    const camera = this._state("camera.webcam");
    const show = this._config.show_camera !== false && camera && camera.state !== "unavailable";
    this._cameraEl.hidden = !show;
    if (!show || !this._connected) {
      this._setCamera("");
      return;
    }
    const snapshot = this._config.camera_mode === "snapshot" || this._cameraFallback;
    this.shadowRoot.querySelector(".camera-label").textContent = snapshot ? "SNAPSHOT" : "LIVE";
    this._cameraEl.classList.toggle("is-live", !snapshot);
    const token = camera.attributes.access_token;
    if (snapshot) {
      if (!this._snapshotTimer) {
        const refresh = () => {
          const cam = this._state("camera.webcam");
          if (cam?.attributes.entity_picture) {
            this._setCamera(`${cam.attributes.entity_picture}&_t=${Date.now()}`, true);
          }
        };
        refresh();
        this._snapshotTimer = setInterval(refresh, SNAPSHOT_REFRESH_MS);
      }
    } else if (token) {
      const src = `/api/camera_proxy_stream/${camera.entity_id}?token=${token}`;
      if (src !== this._cameraSrc) {
        this._setCamera(src);
        // A stream that stays open without sending a frame never fires
        // "error"; treat no picture after a few seconds as a failure too.
        clearTimeout(this._liveWatchdog);
        this._liveWatchdog = setTimeout(() => {
          if (this._cameraSrc === src && !this._imgEl.naturalWidth && !this._cameraFallback) {
            this._cameraFallback = true;
            this._cameraSrc = "";
            this._updateCamera();
          }
        }, LIVE_FIRST_FRAME_MS);
      }
    }
  }

  _setCamera(src, keepTimer = false) {
    if (!src) clearTimeout(this._liveWatchdog);
    if (!keepTimer && this._snapshotTimer) {
      clearInterval(this._snapshotTimer);
      this._snapshotTimer = null;
    }
    if (src === this._cameraSrc || !this._imgEl) return;
    this._cameraSrc = src;
    if (src) this._imgEl.src = src;
    else this._imgEl.removeAttribute("src");
  }

  _status() {
    const ready = this._state("binary_sensor.printer_ready");
    const printState = this._state("sensor.print_state");
    if (!printState || printState.state === "unavailable" || ready?.state === "off") {
      return "offline";
    }
    if (this._state("binary_sensor.has_error")?.state === "on") return "error";
    return STATUS[printState.state] ? printState.state : "standby";
  }

  _renderContent() {
    const status = this._status();
    const info = STATUS[status];
    const active = status === "printing" || status === "paused";
    return `
      <div class="header">
        <div class="title">
          <ha-icon icon="mdi:printer-3d"></ha-icon>
          <span>${esc(this._config.title || this._printer.title || "Sovol SV08")}</span>
        </div>
        <div class="pill tone-${info.tone}">
          <ha-icon icon="${info.icon}"></ha-icon>${info.label}
        </div>
      </div>
      ${this._renderAlerts()}
      ${active ? this._renderProgress(status) : this._renderIdle(status)}
      ${this._renderTemps()}
      ${this._config.show_preheat !== false && !active && status !== "offline" ? this._renderPreheat() : ""}
      ${this._renderStats()}
      ${this._config.show_controls !== false ? this._renderControls(status) : ""}`;
  }

  _renderAlerts() {
    const alerts = [];
    const error = this._state("binary_sensor.has_error");
    if (error?.state === "on") {
      alerts.push(error.attributes.klipper_message || "The printer reported an error.");
    }
    for (const role of this._roles("binary_sensor.filament_")) {
      const s = this._state(role);
      if (s?.state === "on") {
        alerts.push(`${s.attributes.friendly_name || "Filament sensor"}: filament not detected`);
      }
    }
    return alerts
      .map((text) => `<div class="alert"><ha-icon icon="mdi:alert"></ha-icon><span>${esc(text)}</span></div>`)
      .join("");
  }

  _formatEta() {
    const etaState = this._state("sensor.print_eta");
    if (!etaState || ["unknown", "unavailable"].includes(etaState.state)) return "–";
    const date = new Date(etaState.state);
    if (Number.isNaN(date.getTime())) return "–";
    const sameDay = date.toDateString() === new Date().toDateString();
    const options = { ...(sameDay ? {} : { weekday: "short" }), hour: "2-digit", minute: "2-digit" };
    try {
      return date.toLocaleString(this._hass.locale?.language || undefined, options);
    } catch (err) {
      // Unknown language tag: fall back to the browser's locale.
      return date.toLocaleString(undefined, options);
    }
  }

  _renderProgress(status) {
    const progress = num(this._state("sensor.print_progress")) ?? 0;
    const layer = num(this._state("sensor.current_layer"));
    const layers = num(this._state("sensor.total_layers"));
    const remaining = num(this._state("sensor.time_remaining"));
    const elapsed = num(this._state("sensor.print_duration"));
    const filename = prettyFilename(this._state("sensor.filename")?.state);
    const radius = 42;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference * (1 - Math.min(Math.max(progress, 0), 100) / 100);
    const layerText = layer != null && layers ? `${layer} / ${layers}` : layers ? `– / ${layers}` : "–";

    return `
      <div class="progress">
        <div class="ring ${status === "paused" ? "paused" : ""}">
          <svg viewBox="0 0 100 100">
            <circle class="track" cx="50" cy="50" r="${radius}"></circle>
            <circle class="bar" cx="50" cy="50" r="${radius}"
              stroke-dasharray="${circumference.toFixed(2)}"
              stroke-dashoffset="${offset.toFixed(2)}"></circle>
          </svg>
          <div class="ring-label"><span class="pct">${Math.round(progress)}</span><span class="unit">%</span></div>
        </div>
        <div class="job">
          <div class="filename" title="${esc(filename)}">${esc(filename || "Printing")}</div>
          <div class="facts">
            ${this._fact("mdi:layers-triple", "Layer", layerText)}
            ${this._fact("mdi:timer-sand", "Remaining", formatDuration(remaining))}
            ${this._fact("mdi:clock-end", "ETA", this._formatEta())}
            ${this._fact("mdi:timer-outline", "Elapsed", formatDuration(elapsed))}
          </div>
        </div>
      </div>`;
  }

  _renderIdle(status) {
    const filename = prettyFilename(this._state("sensor.filename")?.state);
    const text =
      status === "offline"
        ? "Printer is not reachable"
        : status === "complete" && filename
          ? `Finished ${filename}`
          : status === "cancelled" && filename
            ? `Cancelled ${filename}`
            : "Ready to print";
    return `<div class="idle"><ha-icon icon="${STATUS[status].icon}"></ha-icon><span>${esc(text)}</span></div>`;
  }

  _fact(icon, name, value) {
    return `
      <div class="fact">
        <ha-icon icon="${icon}"></ha-icon>
        <div><div class="fact-label">${name}</div><div class="fact-value">${esc(value)}</div></div>
      </div>`;
  }

  _renderTemps() {
    const tiles = [];
    const heater = (name, icon, sensorRole, targetRole, numberRole) => {
      if (!this._eid(sensorRole)) return;
      const s = this._state(sensorRole);
      tiles.push(
        this._tempTile({
          name,
          icon,
          current: num(s),
          target: num(this._state(numberRole)) ?? num(this._state(targetRole)),
          unit: s?.attributes.unit_of_measurement,
          moreInfo: this._eid(numberRole) || this._eid(sensorRole),
        })
      );
    };
    heater("Nozzle", "mdi:printer-3d-nozzle", "sensor.nozzle_temperature", "sensor.nozzle_target", "number.nozzle_target_setpoint");
    heater("Bed", "mdi:radiator", "sensor.bed_temperature", "sensor.bed_target", "number.bed_target_setpoint");

    // Chamber thermistors (temperature_sensor with "chamber" in its name)
    for (const role of this._roles("sensor.temp_")) {
      if (!/chamber|enclosure/i.test(role)) continue;
      const s = this._state(role);
      tiles.push(
        this._tempTile({
          name: label(role.slice("sensor.temp_".length)),
          icon: "mdi:thermometer",
          current: num(s),
          target: null,
          unit: s?.attributes.unit_of_measurement,
          moreInfo: this._eid(role),
        })
      );
    }
    // heater_generic (e.g. an enclosure heater)
    for (const role of this._roles("sensor.heater_")) {
      const match = role.match(/^sensor\.heater_(.+)_temperature$/);
      if (match) {
        heater(label(match[1]), "mdi:heat-wave", role, null, `number.heater_${match[1]}_target`);
      }
    }
    return tiles.length ? `<div class="temps">${tiles.join("")}</div>` : "";
  }

  _tempTile({ name, icon, current, target, unit, moreInfo }) {
    const u = unit || "°C";
    // A target of 0 °C means "off"; shown in °F that is 32.
    const heating = target != null && target > (u === "°F" ? 32.5 : 0);
    return `
      <button class="temp ${heating ? "heating" : ""}" data-action="more-info" data-entity="${esc(moreInfo || "")}">
        <div class="temp-head"><ha-icon icon="${icon}"></ha-icon><span class="temp-label">${esc(name)}</span></div>
        <div class="temp-value">${current != null ? Math.round(current) : "–"}<span>${esc(u)}</span></div>
        <div class="temp-target">${heating ? `→ ${Math.round(target)}${esc(u)}` : target == null ? "&nbsp;" : "Off"}</div>
      </button>`;
  }

  _renderPreheat() {
    const nozzle = this._state("number.nozzle_target_setpoint");
    const bed = this._state("number.bed_target_setpoint");
    if (!nozzle || !bed) return "";
    const presets = (Array.isArray(this._config.preheat_presets) ? this._config.preheat_presets : DEFAULT_PREHEAT_PRESETS)
      .filter((p) => p && typeof p === "object" && p.name != null)
      .map((p) => ({ name: String(p.name), nozzle: Number(p.nozzle) || 0, bed: Number(p.bed) || 0 }));
    if (!presets.length) return "";
    const disabled = nozzle.state === "unavailable" || bed.state === "unavailable";
    const nozzleUnit = nozzle.attributes.unit_of_measurement;
    const bedUnit = bed.attributes.unit_of_measurement;
    const clamp = (value, entity) => Math.min(value, Number(entity.attributes.max) || value);
    const chips = presets.map(({ name, nozzle: n, bed: b }) => {
      const nv = clamp(toUnit(n, nozzleUnit), nozzle);
      const bv = clamp(toUnit(b, bedUnit), bed);
      const active = Math.abs(Number(nozzle.state) - nv) < 0.6 && Math.abs(Number(bed.state) - bv) < 0.6;
      return `
        <button class="preset ${active ? "active" : ""}" data-action="preheat" data-nozzle="${nv}" data-bed="${bv}"
          ${disabled ? "disabled" : ""} title="${esc(`${name}: nozzle ${nv}${nozzleUnit || "°C"}, bed ${bv}${bedUnit || "°C"}`)}">
          <span class="preset-name">${esc(name)}</span>
          <span class="preset-temp">${nv}° / ${bv}°</span>
        </button>`;
    });
    const heating = Number(nozzle.state) > 0 || Number(bed.state) > 0;
    chips.push(`
      <button class="preset ${heating ? "" : "active-off"}" data-action="preheat" data-nozzle="0" data-bed="0" ${disabled ? "disabled" : ""}
        title="Turn the nozzle and bed heaters off">
        <span class="preset-name">Cool</span><span class="preset-temp">Off</span>
      </button>`);
    return `
      <div class="presets">
        <div class="presets-title"><ha-icon icon="mdi:fire"></ha-icon>Preheat</div>
        <div class="preset-row">${chips.join("")}</div>
      </div>`;
  }

  _renderStats() {
    const stats = [
      ["sensor.speed_factor", "number.speed_factor_setpoint", "mdi:speedometer", "Speed"],
      ["sensor.flow_rate", "number.flow_rate_setpoint", "mdi:water-percent", "Flow"],
      ["sensor.fan_speed", "number.fan_speed_setpoint", "mdi:fan", "Fan"],
    ]
      .filter(([role]) => this._eid(role))
      .map(([role, setpoint, icon, name]) => {
        const value = num(this._state(role));
        return this._stat(icon, value != null ? `${Math.round(value)}%` : "–", name, this._eid(setpoint) || this._eid(role));
      });
    const qgl = this._state("binary_sensor.qgl_applied");
    if (qgl) {
      const leveled = qgl.state === "on";
      stats.push(this._stat(leveled ? "mdi:check-decagram" : "mdi:axis-z-rotate-clockwise", leveled ? "Leveled" : "Not leveled", "Gantry", qgl.entity_id, leveled ? "" : "warn"));
    }
    const mesh = this._state("sensor.bed_mesh_profile");
    if (mesh) {
      const loaded = !["unknown", "unavailable", ""].includes(mesh.state);
      stats.push(this._stat("mdi:grid", loaded ? mesh.state : "None", "Mesh", mesh.entity_id, loaded ? "" : "warn"));
    }
    const eddy = this._state("binary_sensor.eddy_calibrated");
    if (eddy && eddy.state !== "unavailable") {
      const ok = eddy.state === "on";
      stats.push(this._stat("mdi:radar", ok ? "Calibrated" : "Not calibrated", "Eddy", eddy.entity_id, ok ? "" : "warn"));
    }
    return stats.length ? `<div class="stats">${stats.join("")}</div>` : "";
  }

  _stat(icon, value, name, entity, variant = "") {
    return `
      <button class="stat ${variant}" data-action="more-info" data-entity="${esc(entity || "")}">
        <ha-icon icon="${icon}"></ha-icon>
        <span class="stat-text">
          <span class="stat-value" title="${esc(value)}">${esc(value)}</span>
          <span class="stat-label">${name}</span>
        </span>
      </button>`;
  }

  _renderControls(status) {
    const buttons = [];
    const active = status === "printing" || status === "paused";
    const offline = status === "offline";
    const light = this._state("switch.light");
    if (status === "printing" && this._eid("button.pause_print")) {
      buttons.push(this._control("pause", "mdi:pause", "Pause"));
    }
    if (status === "paused" && this._eid("button.resume_print")) {
      buttons.push(this._control("resume", "mdi:play", "Resume", "primary"));
    }
    if (active && this._eid("button.cancel_print")) {
      buttons.push(this._control("cancel", "mdi:stop", "Cancel", "danger", true));
    }
    if (light) {
      const on = light.state === "on";
      buttons.push(this._control("light", on ? "mdi:led-strip-variant" : "mdi:led-strip-variant-off", "Light", on ? "on" : "", false, offline));
    }
    if (!active && this._eid("button.home_all")) {
      buttons.push(this._control("home", "mdi:home-import-outline", "Home", "", false, offline));
    }
    if (!active && this._eid("button.quad_gantry_level")) {
      buttons.push(this._control("qgl", "mdi:axis-z-rotate-clockwise", "QGL", "", true, offline));
    }
    if (this._eid("button.emergency_stop")) {
      buttons.push(this._control("estop", "mdi:alert-octagon", "E-Stop", "danger", true, offline));
    }
    return `<div class="controls">${buttons.join("")}</div>`;
  }

  _control(action, icon, name, variant = "", confirm = false, disabled = false) {
    const armed = this._armed?.action === action;
    return `
      <button class="control ${variant} ${armed ? "armed" : ""}" data-action="${action}"
        ${confirm ? 'data-confirm="1"' : ""} ${disabled ? "disabled" : ""}>
        <ha-icon icon="${armed ? "mdi:alert" : icon}"></ha-icon>
        <span>${armed ? "Tap again" : name}</span>
      </button>`;
  }

  // ------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------

  _onClick(ev) {
    const target = ev.composedPath().find((el) => el.dataset && el.dataset.action);
    if (!target || target.disabled) return;
    const action = target.dataset.action;

    if (action === "camera") {
      this._moreInfo(this._eid("camera.webcam"));
      return;
    }
    if (action === "more-info") {
      this._moreInfo(target.dataset.entity);
      return;
    }

    // Destructive or slow actions need a second tap within a few seconds.
    if (target.dataset.confirm && this._armed?.action !== action) {
      clearTimeout(this._armed?.timer);
      this._armed = {
        action,
        timer: setTimeout(() => {
          this._armed = null;
          this._safeUpdate();
        }, CONFIRM_MS),
      };
      this._safeUpdate();
      return;
    }
    clearTimeout(this._armed?.timer);
    this._armed = null;

    const press = (role) => this._hass.callService("button", "press", { entity_id: this._eid(role) });
    const setValue = (role, value) =>
      this._hass.callService("number", "set_value", { entity_id: this._eid(role), value });
    const presses = {
      pause: "button.pause_print",
      resume: "button.resume_print",
      cancel: "button.cancel_print",
      home: "button.home_all",
      qgl: "button.quad_gantry_level",
      estop: "button.emergency_stop",
    };
    if (presses[action]) {
      press(presses[action]);
    } else if (action === "light") {
      this._hass.callService("switch", "toggle", { entity_id: this._eid("switch.light") });
    } else if (action === "preheat") {
      setValue("number.nozzle_target_setpoint", Number(target.dataset.nozzle));
      setValue("number.bed_target_setpoint", Number(target.dataset.bed));
    }
    this._safeUpdate();
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }
}

class SovolSV08CardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
    else this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (schema) =>
        ({
          entry_id: "Printer",
          title: "Title (optional)",
          accent_color: "Accent color",
          show_camera: "Show camera",
          camera_mode: "Camera mode",
          show_controls: "Show controls",
          show_preheat: "Show preheat presets",
        })[schema.name] || schema.name;
      this._form.addEventListener("value-changed", (ev) => {
        this._config = ev.detail.value;
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = [
      { name: "entry_id", selector: { config_entry: { integration: DOMAIN } } },
      { name: "title", selector: { text: {} } },
      {
        name: "accent_color",
        selector: {
          select: {
            mode: "dropdown",
            custom_value: true,
            options: Object.keys(ACCENTS).map((key) => ({
              value: key,
              label: { theme: "Theme color", sovol: "Sovol blue" }[key] || key[0].toUpperCase() + key.slice(1),
            })),
          },
        },
      },
      { name: "show_camera", selector: { boolean: {} } },
      {
        name: "camera_mode",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "live", label: "Live video" },
              { value: "snapshot", label: "Snapshot every 5 s" },
            ],
          },
        },
      },
      { name: "show_controls", selector: { boolean: {} } },
      { name: "show_preheat", selector: { boolean: {} } },
    ];
    this._form.data = { ...SovolSV08Card.getStubConfig(), ...this._config };
  }
}

const STYLES = `
  :host { display: block; }
  ha-card { overflow: hidden; }
  button { font: inherit; color: inherit; }
  button:focus-visible { outline: 2px solid var(--sv-accent); outline-offset: 2px; }
  .camera { position: relative; background: #000; cursor: pointer; aspect-ratio: 16 / 9; }
  .camera[hidden] { display: none; }
  .camera img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .camera-error {
    display: none; position: absolute; inset: 0; align-items: center; justify-content: center; gap: 8px;
    color: rgba(255,255,255,.75); font-size: 14px; --mdc-icon-size: 22px;
  }
  .camera.is-error img, .camera.is-error .camera-badge { visibility: hidden; }
  .camera.is-error .camera-error { display: flex; }
  .camera-badge {
    position: absolute; top: 10px; left: 10px; display: flex; align-items: center; gap: 6px;
    padding: 3px 8px; border-radius: 999px; background: rgba(0,0,0,.55); color: #fff;
    font-size: 11px; font-weight: 600; letter-spacing: .06em;
  }
  .camera-badge .dot { width: 7px; height: 7px; border-radius: 50%; background: #aaa; }
  .camera.is-live .camera-badge .dot { background: #ff4d4f; animation: pulse 1.6s infinite; }
  @keyframes pulse { 50% { opacity: .35; } }
  @media (prefers-reduced-motion: reduce) {
    .camera.is-live .camera-badge .dot { animation: none; }
    .ring .bar, .control { transition: none; }
  }
  .content { padding: 16px; display: flex; flex-direction: column; gap: 14px; }
  .empty { padding: 8px 0; color: var(--secondary-text-color); }
  .header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .title { display: flex; align-items: center; gap: 8px; font-size: 18px; font-weight: 600; min-width: 0; }
  .title span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .title ha-icon { color: var(--sv-accent); }
  .pill {
    display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border-radius: 999px;
    font-size: 13px; font-weight: 600; white-space: nowrap; --mdc-icon-size: 16px;
  }
  .tone-accent { color: var(--sv-accent); background: color-mix(in srgb, var(--sv-accent) 16%, transparent); }
  .tone-warning { color: var(--warning-color, #ffa600); background: color-mix(in srgb, var(--warning-color, #ffa600) 16%, transparent); }
  .tone-success { color: var(--success-color, #43a047); background: color-mix(in srgb, var(--success-color, #43a047) 16%, transparent); }
  .tone-error { color: var(--error-color, #db4437); background: color-mix(in srgb, var(--error-color, #db4437) 16%, transparent); }
  .tone-muted { color: var(--secondary-text-color); background: color-mix(in srgb, var(--secondary-text-color) 14%, transparent); }
  .alert {
    display: flex; gap: 8px; align-items: flex-start; padding: 10px 12px; border-radius: 12px;
    color: var(--error-color, #db4437); background: color-mix(in srgb, var(--error-color, #db4437) 12%, transparent);
    font-size: 14px; --mdc-icon-size: 20px;
  }
  .progress { display: flex; align-items: center; gap: 16px; }
  .ring { position: relative; width: 104px; height: 104px; flex: none; }
  .ring svg { width: 100%; height: 100%; transform: rotate(-90deg); }
  .ring circle { fill: none; stroke-width: 9; }
  .ring .track { stroke: color-mix(in srgb, var(--sv-accent) 15%, transparent); }
  .ring .bar { stroke: var(--sv-accent); stroke-linecap: round; transition: stroke-dashoffset .6s ease; }
  .ring.paused .bar { stroke: var(--warning-color, #ffa600); }
  .ring-label { position: absolute; inset: 0; display: flex; align-items: baseline; justify-content: center; padding-top: 34px; }
  .ring-label .pct { font-size: 28px; font-weight: 700; }
  .ring-label .unit { font-size: 14px; color: var(--secondary-text-color); margin-left: 1px; }
  .job { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
  .filename { font-weight: 600; font-size: 15px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .facts { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; }
  .fact { display: flex; gap: 6px; align-items: center; min-width: 0; --mdc-icon-size: 18px; }
  .fact ha-icon { color: var(--sv-accent); flex: none; }
  .fact-label { font-size: 11px; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
  .fact-value { font-size: 14px; font-weight: 600; white-space: nowrap; }
  .idle { display: flex; align-items: center; gap: 10px; color: var(--secondary-text-color); font-size: 15px; --mdc-icon-size: 22px; }
  .temps { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; }
  .temp {
    text-align: left; border: 1px solid var(--divider-color); border-radius: 14px; padding: 10px;
    background: var(--secondary-background-color, transparent); cursor: pointer; display: flex; flex-direction: column; gap: 2px;
  }
  .temp-head { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--secondary-text-color); --mdc-icon-size: 16px; }
  .temp-label { min-width: 0; overflow-wrap: anywhere; line-height: 1.25; }
  .temp.heating .temp-head ha-icon { color: var(--state-climate-heat-color, #ff8100); }
  .temp-value { font-size: 20px; font-weight: 700; }
  .temp-value span { font-size: 12px; font-weight: 500; color: var(--secondary-text-color); margin-left: 1px; }
  .temp-target { font-size: 12px; color: var(--secondary-text-color); }
  .temp.heating .temp-target { color: var(--state-climate-heat-color, #ff8100); }
  .presets { display: flex; flex-direction: column; gap: 6px; }
  .presets-title { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--secondary-text-color); --mdc-icon-size: 16px; }
  .presets-title ha-icon { color: var(--state-climate-heat-color, #ff8100); }
  .preset-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(60px, 1fr)); gap: 6px; }
  .preset {
    display: flex; flex-direction: column; align-items: center; gap: 1px; padding: 6px 2px; min-width: 0;
    border-radius: 10px; border: 1px solid var(--divider-color); background: transparent; cursor: pointer; line-height: 1.15;
  }
  .preset:disabled { opacity: .4; cursor: default; }
  .preset-name { font-size: 12px; font-weight: 600; }
  .preset-temp { font-size: 11px; color: var(--secondary-text-color); white-space: nowrap; }
  .preset.active { border-color: var(--state-climate-heat-color, #ff8100); background: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 14%, transparent); }
  .preset.active .preset-temp { color: var(--state-climate-heat-color, #ff8100); font-weight: 600; }
  .preset.active-off { border-color: var(--secondary-text-color); background: color-mix(in srgb, var(--primary-text-color) 7%, transparent); }
  .stats { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; }
  .stat {
    display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 12px; overflow: hidden; min-width: 0;
    border: 1px solid var(--divider-color); background: transparent; cursor: pointer; --mdc-icon-size: 18px;
  }
  .stat ha-icon { color: var(--sv-accent); flex: none; }
  .stat.warn ha-icon { color: var(--warning-color, #ffa600); }
  .stat-text { display: flex; flex-direction: column; min-width: 0; text-align: left; line-height: 1.2; }
  .stat-value { font-weight: 600; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .stat-label { font-size: 11px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .controls { display: flex; gap: 8px; flex-wrap: wrap; }
  .control {
    flex: 1; min-width: 58px; display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 10px 6px;
    border-radius: 14px; border: none; cursor: pointer; font-size: 12px; font-weight: 600;
    background: color-mix(in srgb, var(--primary-text-color) 7%, transparent); transition: background .15s, transform .1s;
  }
  .control:active { transform: scale(.97); }
  .control:disabled { opacity: .4; cursor: default; }
  .control.primary, .control.on { color: var(--sv-accent); background: color-mix(in srgb, var(--sv-accent) 16%, transparent); }
  .control.danger { color: var(--error-color, #db4437); }
  .control.armed { color: #fff; background: var(--error-color, #db4437); }
  @media (max-width: 420px) {
    .progress { flex-direction: column; align-items: stretch; }
    .ring { align-self: center; }
  }
`;

if (!customElements.get("sovol-sv08-card")) {
  customElements.define("sovol-sv08-card", SovolSV08Card);
  customElements.define("sovol-sv08-card-editor", SovolSV08CardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "sovol-sv08-card",
    name: "Sovol SV08",
    description: "Printer dashboard: live camera, progress, time left, temperatures, gantry leveling, preheat and controls.",
    preview: true,
    documentationURL: "https://github.com/kbaker827/ha-sovol-sv08#dashboard-card",
  });
  console.info(`%c SOVOL-SV08-CARD %c ${CARD_VERSION} `, "color:#fff;background:#009bb9;font-weight:700", "");
}
