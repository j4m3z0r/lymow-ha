class LymowMapCard extends HTMLElement {
  setConfig(config) {
    this._config = config;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    this._render();
  }

  _isDark() {
    const v = getComputedStyle(document.documentElement)
      .getPropertyValue("--primary-background-color")
      .trim();
    const m = v.match(/^#([0-9a-f]{6})$/i);
    if (m) {
      const r = parseInt(m[1].slice(0, 2), 16);
      const g = parseInt(m[1].slice(2, 4), 16);
      const b = parseInt(m[1].slice(4, 6), 16);
      return (r * 299 + g * 587 + b * 114) / 1000 < 128;
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  _render() {
    if (!this._hass || !this._config) return;

    const entityId = this._config.entity || "sensor.lymow_mower_map";
    const state = this._hass.states[entityId];

    if (!state) {
      this.innerHTML = `<div style="padding:16px;color:var(--error-color,#f44336)">Entity ${entityId} not found</div>`;
      return;
    }

    const attrs = state.attributes;
    const zones = attrs.zones || [];
    const channels = attrs.channels || [];
    const position = attrs.mower_position;
    const heading = attrs.mower_heading;
    const status = attrs.robot_status || state.state;

    const hasPolygons = zones.some((z) => z.polygon && z.polygon.length > 2);

    const statusColors = {
      charging: "#4caf50",
      charging_full: "#4caf50",
      cleaning: "#2196f3",
      docked: "#9e9e9e",
      docking: "#ff9800",
      paused: "#ff9800",
      paused_docking: "#ff9800",
      error: "#f44336",
      emergency_stop: "#f44336",
      waiting: "#9e9e9e",
    };
    const color = statusColors[status] || "#9e9e9e";
    const statusLabel = status.replace(/_/g, " ");

    let mapContent;
    if (hasPolygons) {
      mapContent = this._buildSVG(zones, channels, position, heading);
    } else {
      const goZones = zones.filter((z) => z.zone_type === "go" && z.name);
      const chips = goZones
        .map(
          (z) =>
            `<span style="background:var(--secondary-background-color,#e8f5e9);color:var(--primary-text-color,#212121);padding:2px 8px;border-radius:12px;font-size:12px;display:inline-block">${z.name}</span>`
        )
        .join("");
      mapContent = `
        <div style="padding:6px 12px;color:var(--secondary-text-color,#888);font-size:12px">${zones.length} zones · map data loading</div>
        <div style="display:flex;flex-wrap:wrap;gap:6px;padding:0 12px 12px">${chips}</div>`;
    }

    this.innerHTML = `
      <div style="font-family:var(--paper-font-body1_-_font-family,sans-serif);border-radius:var(--ha-card-border-radius,4px);overflow:hidden">
        <div style="display:flex;align-items:center;gap:8px;padding:10px 12px;background:${color}22;border-left:4px solid ${color}">
          <span style="font-size:16px;font-weight:500;text-transform:capitalize;color:var(--primary-text-color,#212121)">${statusLabel}</span>
        </div>
        ${mapContent}
      </div>`;
  }

  _buildSVG(zones, channels, position, heading) {
    const allPoints = [
      ...zones.flatMap((z) => z.polygon || []),
      ...channels.flatMap((c) => c.points || []),
    ];
    if (!allPoints.length) return "";

    const xs = allPoints.map((p) => p[0]);
    const ys = allPoints.map((p) => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const w = maxX - minX || 1, h = maxY - minY || 1;
    const pad = 12;
    const svgW = 400, svgH = Math.round((svgW * h) / w) || 300;
    const scale = Math.min((svgW - 2 * pad) / w, (svgH - 2 * pad) / h);

    const tx = (x) => pad + (x - minX) * scale;
    const ty = (y) => svgH - pad - (y - minY) * scale;

    const dark = this._isDark();
    const goFill      = dark ? "#1b3a1b" : "#c8e6c9";
    const goStroke    = dark ? "#66bb6a" : "#43a047";
    const noFill      = dark ? "#3a1b1b" : "#ffcdd2";
    const noStroke    = dark ? "#ef5350" : "#e53935";
    const chanStroke  = dark ? "#90caf9" : "#1565c0";
    const dotStroke   = dark ? "#111111" : "white";

    const labelColor = dark ? "#e0e0e0" : "#1a1a1a";

    const paths = zones
      .map((z) => {
        if (!z.polygon || z.polygon.length < 3) return "";
        const pts = z.polygon
          .map((p) => `${tx(p[0]).toFixed(1)},${ty(p[1]).toFixed(1)}`)
          .join(" ");
        const fill   = z.zone_type === "nogo" ? noFill   : goFill;
        const stroke = z.zone_type === "nogo" ? noStroke : goStroke;
        return `<polygon points="${pts}" fill="${fill}" stroke="${stroke}" stroke-width="0.8" opacity="0.9"/>`;
      })
      .join("");

    const labels = zones
      .map((z) => {
        if (z.zone_type !== "go" || !z.name || !z.polygon || z.polygon.length < 3) return "";
        const svgPts = z.polygon.map((p) => [tx(p[0]), ty(p[1])]);
        const cx = (svgPts.reduce((s, p) => s + p[0], 0) / svgPts.length).toFixed(1);
        const cy = (svgPts.reduce((s, p) => s + p[1], 0) / svgPts.length).toFixed(1);
        // Estimate zone width in SVG coords to scale font
        const pxs = svgPts.map((p) => p[0]);
        const zoneW = Math.max(...pxs) - Math.min(...pxs);
        const fontSize = Math.max(5, Math.min(11, zoneW / (z.name.length * 0.65)));
        return `<text x="${cx}" y="${cy}" text-anchor="middle" dominant-baseline="middle" font-size="${fontSize.toFixed(1)}" font-family="sans-serif" fill="${labelColor}" opacity="0.85" pointer-events="none">${z.name}</text>`;
      })
      .join("");

    const channelPaths = channels
      .map((c) => {
        if (!c.points || c.points.length < 2) return "";
        const pts = c.points
          .map((p) => `${tx(p[0]).toFixed(1)},${ty(p[1]).toFixed(1)}`)
          .join(" ");
        return `<polyline points="${pts}" fill="none" stroke="${chanStroke}" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.85"/>`;
      })
      .join("");

    let dot = "";
    if (position) {
      const cx = tx(position[0]);
      const cy = ty(position[1]);
      if (heading != null) {
        // Concave arrowhead (➤ shape) scaled to ~1m in map-space so it stays
        // proportional regardless of zoom. Points in mower-frame (1 unit = 1m):
        //   tip=(0.8,0), wings=(-0.4,±0.55), notch=(0.05,0) — concave back.
        const cos = Math.cos(heading), sin = Math.sin(heading);
        const pt = (px, py) => {
          // Rotate mower-frame point to SVG coords (SVG y is flipped vs map y)
          const sx = (px * cos - py * sin) * scale;
          const sy = (-px * sin - py * cos) * scale;
          return `${(cx + sx).toFixed(1)},${(cy + sy).toFixed(1)}`;
        };
        const pts = `${pt(0.8,0)} ${pt(-0.4,0.55)} ${pt(0.05,0)} ${pt(-0.4,-0.55)}`;
        dot = `<polygon points="${pts}" fill="#2196f3" stroke="${dotStroke}" stroke-width="0.5"/>`;
      } else {
        dot = `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="5" fill="#2196f3" stroke="${dotStroke}" stroke-width="2"/>`;
      }
    }

    return `<svg width="100%" viewBox="0 0 ${svgW} ${svgH}" style="display:block;background:var(--secondary-background-color,#f5f5f5)">${paths}${labels}${channelPaths}${dot}</svg>`;
  }
}
customElements.define("lymow-map-card", LymowMapCard);
