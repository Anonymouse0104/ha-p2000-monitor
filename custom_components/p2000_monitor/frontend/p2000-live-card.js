/*
 * P2000 live card for Home Assistant, shipped with the P2000 Monitor integration.
 *
 *   type: custom:p2000-live-card
 *   entities: [sensor.p2000_monitor, sensor.brandweer_limburg]
 *   display: text | pager | both     (optional, default text)
 *   max_items: 30                    (optional)
 *   title: P2000 live                (optional)
 *
 * Shows the alerts of the chosen P2000 sensors as a live list: service colour,
 * priority, GRIP, region/place, distance, and on click the pager text, the
 * alerted vehicles (with Brandbase names) and capcodes.
 */
const SERVICES = {
  Brandweerdiensten: { label: "Brandweer", color: "#E53935", icon: "mdi:fire-truck" },
  Ambulancediensten: { label: "Ambulance", color: "#F9A825", icon: "mdi:ambulance" },
  Politiediensten: { label: "Politie", color: "#1E88E5", icon: "mdi:police-badge" },
  KNRM: { label: "KNRM", color: "#FB8C00", icon: "mdi:lifebuoy" },
  Lifeliner: { label: "Lifeliner", color: "#8E24AA", icon: "mdi:helicopter" },
  DARES: { label: "DARES", color: "#6D4C41", icon: "mdi:radio-handheld" },
};
const OTHER = { label: "Overig", color: "#757575", icon: "mdi:alarm-light" };
const URGENCY = { 1: "#D32F2F", 2: "#EF6C00", 3: "#F9A825", 4: "#689F38", 5: "#689F38" };
const DISPLAY_LABELS = { text: "Meldingtekst", pager: "Pagertekst", both: "Beide" };

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function parseTime(value) {
  const time = Date.parse(value || "");
  return Number.isNaN(time) ? null : time;
}

function relative(ms) {
  const minutes = Math.round((Date.now() - ms) / 60000);
  if (minutes < 1) return "nu";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours} uur` : `${Math.floor(hours / 24)} d`;
}

class P2000LiveCard extends HTMLElement {
  setConfig(config) {
    const entities = config.entities || (config.entity ? [config.entity] : []);
    if (!entities.length) throw new Error("Kies minstens één P2000-sensor (entities).");
    this._config = { title: "P2000 live", display: "text", max_items: 30, new_minutes: 5, ...config, entities };
    this._display = this._config.display;
    this._hidden = this._hidden || new Set();
    this._expanded = this._expanded || new Set();
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    }
    this._render();
  }

  set hass(hass) {
    const old = this._hass;
    this._hass = hass;
    if (!old || this._config.entities.some((id) => old.states[id] !== hass.states[id])) this._render();
  }

  connectedCallback() {
    // Keep "3 min" style times current.
    this._timer = setInterval(() => this._render(), 30000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  getCardSize() {
    return 8;
  }

  static getStubConfig(hass) {
    const sensor = Object.keys(hass.states).find(
      (id) => id.startsWith("sensor.") && hass.states[id].attributes.source === "alarmeringdroid"
    );
    return { entities: sensor ? [sensor] : [] };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        {
          name: "entities",
          required: true,
          selector: { entity: { multiple: true, filter: { integration: "p2000_monitor", domain: "sensor" } } },
        },
        {
          name: "display",
          selector: {
            select: {
              mode: "dropdown",
              options: Object.entries(DISPLAY_LABELS).map(([value, label]) => ({ value, label })),
            },
          },
        },
        { name: "max_items", selector: { number: { min: 5, max: 100, mode: "box" } } },
      ],
      computeLabel: (schema) =>
        ({ title: "Titel", entities: "P2000-sensoren", display: "Weergave", max_items: "Aantal meldingen" })[schema.name],
    };
  }

  _messages() {
    const byId = new Map();
    for (const id of this._config.entities) {
      const state = this._hass?.states[id];
      for (const message of state?.attributes.meldingen || []) {
        if (!byId.has(message.message_id)) byId.set(message.message_id, message);
      }
    }
    return [...byId.values()].sort(
      (a, b) => (parseTime(b.published) ?? Infinity) - (parseTime(a.published) ?? Infinity) || Number(b.api_id || 0) - Number(a.api_id || 0)
    );
  }

  _onClick(ev) {
    const target = ev.target.closest("[data-action]");
    if (!target) return;
    const { action, value } = target.dataset;
    if (action === "display") {
      const order = ["text", "pager", "both"];
      this._display = order[(order.indexOf(this._display) + 1) % order.length];
    } else if (action === "service") {
      this._hidden.has(value) ? this._hidden.delete(value) : this._hidden.add(value);
    } else if (action === "row") {
      this._expanded.has(value) ? this._expanded.delete(value) : this._expanded.add(value);
    } else {
      return;
    }
    this._render();
  }

  _row(message) {
    const service = SERVICES[message.discipline] || OTHER;
    const published = parseTime(message.published);
    const isNew = published && Date.now() - published < this._config.new_minutes * 60000;
    const expanded = this._expanded.has(message.message_id);
    const text = message.message || "";
    const pager = message.pager_text || message.melding || "";
    const main = this._display === "pager" ? pager || text : text || pager;
    const sub = this._display === "both" && pager && pager !== text ? pager : "";
    const time = published
      ? new Date(published).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" })
      : "test";
    const priority = message.priority
      ? `<span class="badge" style="background:${URGENCY[message.urgency] || "#757575"}">${esc(message.priority)}</span>`
      : "";
    const grip = message.grip ? `<span class="badge grip">GRIP ${esc(message.grip)}</span>` : "";
    const where = [message.regio_name, message.city].filter(Boolean).map(esc).join(" · ");
    const distance = message.distance_km != null ? `<span class="muted">${Math.round(message.distance_km)} km</span>` : "";
    const vehicles = (message.vehicles || []).filter((v) => v && v.id);

    let details = "";
    if (expanded) {
      const other = this._display === "pager" ? text : pager;
      const vehicleRows = vehicles
        .map((v) => {
          const name = [v.type, v.station].filter(Boolean).map(esc).join(" · ");
          const label = `<b>${esc(v.id)}</b> ${name}`;
          return `<li>${v.url ? `<a href="${esc(v.url)}" target="_blank" rel="noreferrer">${label}</a>` : label}</li>`;
        })
        .join("");
      const capcodes = (message.capcode_details?.length ? message.capcode_details : (message.capcodes || []).map((c) => ({ capcode: c })))
        .map((c) => `<li><b>${esc(c.capcode)}</b> ${esc(c.description || "")}</li>`)
        .join("");
      details = `
        <div class="details">
          ${other && other !== main ? `<div class="other">${esc(other)}</div>` : ""}
          ${vehicleRows ? `<div class="label">Voertuigen</div><ul>${vehicleRows}</ul>` : ""}
          ${capcodes ? `<div class="label">Capcodes</div><ul>${capcodes}</ul>` : ""}
        </div>`;
    }

    return `
      <div class="row ${isNew ? "new" : ""}" data-action="row" data-value="${esc(message.message_id)}">
        <div class="bar" style="background:${service.color}"></div>
        <div class="body">
          <div class="meta">
            <span class="time" title="${published ? relative(published) : ""}">${time}</span>
            ${priority}${grip}
            <span class="service" style="color:${service.color}"><ha-icon icon="${service.icon}"></ha-icon>${esc(service.label)}</span>
            <span class="where">${where}</span>
            ${distance}
            ${vehicles.length ? `<span class="service muted" title="Voertuigen"><ha-icon icon="mdi:car-emergency"></ha-icon>${vehicles.length}</span>` : ""}
          </div>
          <div class="text">${esc(main)}</div>
          ${sub ? `<div class="pager">${esc(sub)}</div>` : ""}
          ${details}
        </div>
      </div>`;
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const messages = this._hass ? this._messages() : [];
    const today = new Date().toDateString();
    const counts = new Map();
    for (const message of messages) {
      const label = (SERVICES[message.discipline] || OTHER).label;
      const published = parseTime(message.published);
      if (published && new Date(published).toDateString() === today) counts.set(label, (counts.get(label) || 0) + 1);
    }
    const chips = [...Object.values(SERVICES), OTHER]
      .filter((service) => counts.has(service.label) || this._hidden.has(service.label))
      .map(
        (service) => `
          <button class="chip ${this._hidden.has(service.label) ? "off" : ""}" data-action="service" data-value="${esc(service.label)}"
                  style="--chip:${service.color}" title="Vandaag">
            ${esc(service.label)} <b>${counts.get(service.label) || 0}</b>
          </button>`
      )
      .join("");
    const visible = messages
      .filter((message) => !this._hidden.has((SERVICES[message.discipline] || OTHER).label))
      .slice(0, Number(this._config.max_items) || 30);

    this.shadowRoot.innerHTML = `
      <style>
        ha-card { overflow: hidden; }
        .header { display: flex; align-items: center; gap: 8px; padding: 12px 16px 8px; flex-wrap: wrap; }
        .title { font-size: 1.15em; font-weight: 600; flex: 1; color: var(--primary-text-color); }
        .toggle { border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color);
                  border-radius: 14px; padding: 2px 10px; cursor: pointer; font: inherit; font-size: .85em; }
        .chips { display: flex; gap: 6px; padding: 0 16px 8px; flex-wrap: wrap; }
        .chip { border: 1px solid var(--chip); background: color-mix(in srgb, var(--chip) 15%, transparent);
                color: var(--primary-text-color); border-radius: 12px; padding: 1px 8px; cursor: pointer; font: inherit; font-size: .8em; }
        .chip.off { opacity: .4; background: none; }
        .list { max-height: var(--p2000-max-height, 70vh); overflow-y: auto; }
        .row { display: flex; border-top: 1px solid var(--divider-color); cursor: pointer; }
        .row.new { background: color-mix(in srgb, var(--warning-color, #ffa600) 12%, transparent); }
        .bar { width: 4px; flex: none; }
        .body { padding: 8px 12px; min-width: 0; flex: 1; }
        .meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: .82em; color: var(--secondary-text-color); }
        .time { font-variant-numeric: tabular-nums; font-weight: 600; color: var(--primary-text-color); }
        .badge { color: #fff; border-radius: 4px; padding: 0 5px; font-weight: 700; font-size: .95em; }
        .badge.grip { background: #000; }
        .service { display: inline-flex; align-items: center; gap: 3px; font-weight: 600; }
        .service ha-icon { --mdc-icon-size: 16px; }
        .where { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .muted { color: var(--secondary-text-color); }
        .text { margin-top: 2px; color: var(--primary-text-color); overflow-wrap: anywhere; }
        .pager, .other { margin-top: 2px; font-family: var(--code-font-family, monospace); font-size: .85em; color: var(--secondary-text-color); overflow-wrap: anywhere; }
        .details { margin-top: 6px; font-size: .85em; color: var(--primary-text-color); }
        .details .label { margin-top: 6px; font-weight: 600; color: var(--secondary-text-color); }
        .details ul { margin: 2px 0 0; padding-left: 18px; }
        .details a { color: var(--primary-color); text-decoration: none; }
        .empty { padding: 16px; color: var(--secondary-text-color); }
      </style>
      <ha-card>
        <div class="header">
          <div class="title">${esc(this._config.title)}</div>
          <button class="toggle" data-action="display" title="Wissel weergave">${DISPLAY_LABELS[this._display] || DISPLAY_LABELS.text}</button>
        </div>
        ${chips ? `<div class="chips">${chips}</div>` : ""}
        <div class="list">
          ${visible.length ? visible.map((message) => this._row(message)).join("") : `<div class="empty">Geen meldingen</div>`}
        </div>
      </ha-card>`;
  }
}

if (!customElements.get("p2000-live-card")) {
  customElements.define("p2000-live-card", P2000LiveCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "p2000-live-card",
    name: "P2000 live",
    description: "Live P2000-meldingen met dienstkleuren, prioriteit, voertuigen en capcodes.",
    preview: true,
    documentationURL: "https://github.com/Anonymouse0104/ha-p2000-monitor",
  });
}
