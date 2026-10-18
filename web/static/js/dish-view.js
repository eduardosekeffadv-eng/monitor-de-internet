/* Exibição dos dados da antena Starlink (painel, histórico e relatório). */
(function () {
  "use strict";

  const nf1 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const nf0 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pad = (n) => String(n).padStart(2, "0");
  const hms = (ts) => { const d = new Date(ts); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };

  // cor de cada motivo; violeta = rede da Starlink, âmbar/laranja = instalação/antena
  const COLORS = {
    NO_PINGS_SWITCH: "#818cf8", NO_PINGS: "#a78bfa", NO_SCHEDULE: "#c084fc", NO_SATS: "#e879f9", NO_DOWNLINK: "#f0abfc",
    OBSTRUCTED: "#fbbf24", BOOTING: "#38bdf8", STOWED: "#fb923c", THERMAL_SHUTDOWN: "#f97316", SLEEPING: "#94a3b8",
    CABLE_RESET: "#fdba74", ACTUATOR_ACTIVITY: "#fcd34d", MOVING_WHILE_NOT_PERMITTED: "#fb923c", UNKNOWN: "#64748b",
  };
  const color = (cause) => COLORS[cause] || "#64748b";

  const ICON = '<svg class="dish-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 10.5a8 8 0 0 0 9 9z"/><path d="M9 15l4-4"/><path d="M14 4a6 6 0 0 1 6 6M14 7.5a2.5 2.5 0 0 1 2.5 2.5"/></svg>';

  /** Duração curta com décimos: 0,7 s · 12,4 s · 3 min 05 s */
  function secs(ms) {
    if (ms == null) return "—";
    const s = ms / 1000;
    if (s < 60) return `${nf1.format(s)} s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m} min ${pad(Math.round(s - m * 60))} s`;
    return `${Math.floor(m / 60)} h ${pad(m % 60)} min`;
  }

  /** Etiqueta curta para listas de quedas. */
  function tag(e) {
    const d = e && e.dish;
    if (!d) return "";
    return `<span class="dish-tag" style="--dc:${color(d.cause)}" title="Motivo informado pela antena Starlink: ${esc(d.text)}">${ICON}${esc(d.label)}</span>`;
  }

  /** Quadro com a explicação da antena, para o detalhe de uma queda. */
  function box(e) {
    const d = e && e.dish;
    if (!d) return "";
    const more = d.count > 1 ? ` A antena registrou ${d.count} quedas neste intervalo${d.others && d.others.length ? ` (também: ${esc(d.others.join(", ").toLowerCase())})` : ""}.` : "";
    return `<div class="dish-box" style="--dc:${color(d.cause)}">
      <div class="dish-box-h">${ICON}<span>Segundo a antena Starlink: <b>${esc(d.label)}</b></span><em>${esc(d.side_label)}</em></div>
      <div>${esc(d.text)}${more}</div>
      <div class="dish-box-f">Registrada pela antena às ${hms(d.start_ts)}, com ${secs(d.duration_ms)} de duração${d.did_switch ? " · houve troca de satélite" : ""}.</div>
    </div>`;
  }

  /** Barra empilhada + tabela de motivos. */
  function causes(summary, opts) {
    opts = opts || {};
    if (!summary || !summary.count) {
      return `<div class="dish-empty">${esc(opts.empty || "A antena não registrou quedas neste período.")}</div>`;
    }
    const total = summary.count;
    const seg = summary.causes.map((c) =>
      `<i style="flex:${c.count};background:${color(c.cause)}" title="${esc(c.label)}: ${c.count}"></i>`).join("");
    const rows = summary.causes.slice(0, opts.limit || 20).map((c) => `
      <div class="dish-cause">
        <span class="sw" style="background:${color(c.cause)}"></span>
        <span class="dc-l"><b>${esc(c.label)}</b>${opts.compact ? "" : `<small>${esc(c.text)}</small>`}</span>
        <span class="dc-side s-${esc(c.side)}">${esc(c.side_label)}</span>
        <span class="dc-n">${nf0.format(c.count)}<small>${nf0.format((c.count / total) * 100)}%</small></span>
        <span class="dc-t">${secs(c.total_ms)}${opts.compact ? "" : `<small>maior: ${secs(c.max_ms)}</small>`}</span>
      </div>`).join("");
    return `<div class="dish-bar">${seg}</div><div class="dish-causes">${rows}</div>`;
  }

  /** Desenha o mapa de obstruções (visão do céu) em um canvas. */
  function drawMap(canvas, map) {
    const dpr = window.devicePixelRatio || 1;
    const size = canvas.clientWidth || 300;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);
    const r = size / 2;
    ctx.save();
    ctx.beginPath(); ctx.arc(r, r, r - 1, 0, Math.PI * 2); ctx.closePath();
    ctx.fillStyle = "rgba(100,116,139,.10)"; ctx.fill();
    ctx.clip();
    const rows = map.rows, cols = map.cols;
    const cw = size / cols, ch = size / rows;
    let seen = 0, bad = 0;
    for (let y = 0; y < rows; y++) {
      const row = map.data[y];
      for (let x = 0; x < cols; x++) {
        const v = row[x];
        if (v < 0) continue;
        seen++;
        if (v < 1) bad++;
        // 1 = céu livre (verde-água) · próximo de 0 = obstruído (vermelho)
        const t = Math.max(0, Math.min(1, v));
        const R = Math.round(244 + (45 - 244) * t), G = Math.round(63 + (212 - 63) * t), B = Math.round(94 + (191 - 94) * t);
        ctx.fillStyle = `rgba(${R},${G},${B},${t >= 1 ? 0.55 : 0.95})`;
        ctx.fillRect(x * cw, y * ch, Math.ceil(cw), Math.ceil(ch));
      }
    }
    ctx.restore();
    ctx.strokeStyle = "rgba(148,163,184,.35)";
    ctx.lineWidth = 1;
    for (const k of [1, 2 / 3, 1 / 3]) { ctx.beginPath(); ctx.arc(r, r, (r - 1) * k, 0, Math.PI * 2); ctx.stroke(); }
    ctx.beginPath(); ctx.moveTo(r, 0); ctx.lineTo(r, size); ctx.moveTo(0, r); ctx.lineTo(size, r); ctx.stroke();
    return { seen, bad, pct: seen ? (bad / seen) * 100 : null };
  }

  /** Seção completa de um período (histórico e relatório): indicadores + motivos. */
  function section(dish) {
    if (!dish) return "";
    const sm = dish.summary, st = dish.stats;
    const k = (l, v, s) => `<div class="dk"><div class="l">${l}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
    const sides = sm.sides || {};
    const tiles = [
      k("Quedas registradas pela antena", nf0.format(sm.count), sm.count ? `somando ${secs(sm.total_ms)}` : "nenhuma no período"),
      k("Da rede Starlink", nf0.format(sides.starlink || 0), "satélites, troca de satélite, falta de cobertura"),
      k("Da instalação / antena", nf0.format(sides.local || 0), "obstrução, reinício, energia, temperatura"),
      k("Quedas do monitor explicadas", dish.monitor_failures ? `${nf0.format(dish.matched)} de ${nf0.format(dish.monitor_failures)}` : "—",
        dish.monitor_failures ? "interrupções e microquedas com motivo da antena" : "nenhuma queda medida pelo monitor"),
    ];
    if (dish.only_dish) tiles.push(k("Vistas só pela antena", nf0.format(dish.only_dish), "curtas demais para o monitor perceber"));
    if (st) {
      tiles.push(k("Perda medida pela antena", st.drop_pct == null ? "—" : `${nf1.format(st.drop_pct)}%`, `em ${secs((st.secs || 0) * 1000)} de dados da antena`));
      const obs = st.obstructed_s || 0, uns = st.unscheduled_s || 0;
      tiles.push(k("Perda por obstrução", secs(obs * 1000), "equivalente em segundos"));
      tiles.push(k("Perda por falta de satélite", secs(uns * 1000), "equivalente em segundos"));
      if (st.power_w != null) tiles.push(k("Consumo médio da antena", `${nf1.format(st.power_w)} W`, ""));
    }
    return `<div class="dish-kpis">${tiles.join("")}</div>${causes(sm)}`;
  }

  window.DishView = { COLORS, color, secs, tag, box, causes, section, drawMap, esc, ICON };
})();
