/* Visualizações de períodos (histórico e relatórios): gráfico de latência e quedas,
 * perda de pacotes, comparação entre dias, mapa de calor e faixas por dia.
 * Usado pela página Histórico e embutido nos relatórios HTML. */
(function () {
  "use strict";

  // ------------------------------------------------------------ formatação
  const nf = (d) => new Intl.NumberFormat("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d });
  const NF = [nf(0), nf(1), nf(2), nf(3)];
  const pad = (n) => String(n).padStart(2, "0");
  const WD = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];
  const f = {
    num: (v, d = 0) => (v == null ? "—" : NF[d].format(v)),
    ms: (v) => (v == null ? "—" : v < 1 ? "<1" : v < 10 ? NF[1].format(v) : NF[0].format(v)),
    pct: (v, d = 2) => (v == null ? "—" : `${NF[d].format(v)}%`),
    time: (t) => { const d = new Date(t); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; },
    hm: (t) => { const d = new Date(t); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; },
    date: (t) => { const d = new Date(t); return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`; },
    dm: (t) => { const d = new Date(t); return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}`; },
    wd: (t) => WD[new Date(t).getDay()],
    dur(ms) {
      if (ms == null) return "—";
      let s = Math.max(0, Math.round(ms / 1000));
      if (s < 60) return `${s} s`;
      const d = Math.floor(s / 86400); s -= d * 86400;
      const h = Math.floor(s / 3600); s -= h * 3600;
      const m = Math.floor(s / 60); s -= m * 60;
      if (d) return `${d} d ${h} h`;
      if (h) return `${h} h ${pad(m)} min`;
      return `${m} min ${pad(s)} s`;
    },
    margin: (ms) => (ms ? (ms >= 60000 ? `±${Math.round(ms / 60000)} min` : `±${NF[0].format(ms / 1000)} s`) : ""),
    esc: (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    pad,
  };

  // ------------------------------------------------------------ gráficos
  const STATUS_COLOR = { ok: "#34d399", elevada: "#fbbf24", critica: "#fb923c", perda: "#fb7185" };
  const RAW_COLOR = ["#34d399", "#fbbf24", "#fb923c", "#fb7185", "#f43f5e"];

  function theme(print) {
    return print ? { grid: "rgba(15,23,42,.08)", tick: "#64748b" } : { grid: "rgba(148,163,184,.08)", tick: "#6b7d8f" };
  }

  // Marcas do eixo X alinhadas ao horário local (meia-noite, 6 h, 12 h...)
  function localTicks(scale) {
    const s0 = scale.min, s1 = scale.max, H = 3600000;
    const steps = [60000, 2 * 60000, 5 * 60000, 10 * 60000, 15 * 60000, 30 * 60000, H, 2 * H, 3 * H, 6 * H, 12 * H, 24 * H, 48 * H, 7 * 24 * H];
    const step = steps.find((st) => (s1 - s0) / st <= 9) || 30 * 24 * H;
    const off = new Date(s0).getTimezoneOffset() * 60000;
    const ticks = [];
    for (let t = Math.ceil((s0 - off) / step) * step + off; t <= s1; t += step) ticks.push({ value: t });
    scale.ticks = ticks;
  }

  function renderCharts(o) {
    // o: { lat, loss, days, series, bands, start, end, thresholds:{normal,elevated,loss}, byDay, print }
    const T = o.thresholds, S = o.series, span = o.end - o.start;
    const th = theme(o.print);
    const charts = [];
    Chart.defaults.font.family = "Inter, 'Segoe UI', sans-serif";
    Chart.defaults.color = th.tick;
    Chart.defaults.animation = false;
    const pts = S.points;
    const isRaw = S.kind === "raw";
    const half = isRaw ? 0 : S.bucket_ms / 2;

    const fmtTick = (v) => {
      const d = new Date(v);
      const midnight = d.getHours() === 0 && d.getMinutes() === 0;
      if (span <= 20 * 60000) return f.time(v);
      if (span <= 36 * 3600000) return midnight && span > 6 * 3600000 ? `${f.dm(v)} 00:00` : f.hm(v);
      return midnight ? `${f.wd(v)} ${f.dm(v)}` : f.hm(v);
    };
    const xScale = () => ({
      type: "linear", min: o.start, max: o.end, offset: false,
      grid: { color: th.grid }, border: { display: false }, afterBuildTicks: localTicks,
      ticks: { callback: fmtTick, maxRotation: 0, autoSkip: false, font: { family: "'JetBrains Mono', monospace", size: 10 } },
    });
    const aggColor = (p) => {
      if (p.loss != null && p.loss > T.loss) return STATUS_COLOR.perda;
      if (p.lat != null && p.lat > T.elevated) return STATUS_COLOR.critica;
      if (p.lat != null && p.lat > T.normal) return STATUS_COLOR.elevada;
      return STATUS_COLOR.ok;
    };
    const bandsPlugin = {
      id: "bands",
      beforeDatasetsDraw(chart) {
        const { ctx, chartArea: a, scales: { x } } = chart;
        ctx.save();
        ctx.beginPath(); ctx.rect(a.left, a.top, a.width, a.height); ctx.clip();
        for (const b of o.bands) {
          if (b.type === "microqueda" && !isRaw) continue;
          const s0 = x.getPixelForValue(b.start_ts), s1 = x.getPixelForValue(b.end_ts || o.end);
          const w = Math.max(s1 - s0, b.type === "interrupcao" ? 2 : 1);
          ctx.fillStyle = b.type === "interrupcao" ? "rgba(244,63,94,.16)" : b.type === "microqueda" ? "rgba(251,146,60,.12)" : "rgba(100,116,139,.14)";
          ctx.fillRect(s0, a.top, w, a.height);
          if (b.type === "interrupcao") { ctx.fillStyle = "rgba(244,63,94,.9)"; ctx.fillRect(s0, a.top, w, 3); }
        }
        ctx.restore();
      },
    };
    const thresholdPlugin = (lines) => ({
      id: "thr",
      beforeDatasetsDraw(chart) {
        const { ctx, chartArea: a, scales: { y } } = chart;
        ctx.save(); ctx.setLineDash([4, 5]); ctx.lineWidth = 1;
        ctx.font = "500 10px 'JetBrains Mono', monospace";
        for (const [v, color, label] of lines) {
          if (v == null || v >= y.max) continue;
          const py = Math.round(y.getPixelForValue(v)) + 0.5;
          ctx.strokeStyle = color; ctx.beginPath(); ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); ctx.stroke();
          ctx.fillStyle = color; ctx.fillText(label, a.left + 6, py - 4);
        }
        ctx.restore();
      },
    });
    // Em médias, a cor vem da latência/perda; quedas viram marcadores (▼) no topo.
    const markersPlugin = {
      id: "markers",
      afterDatasetsDraw(chart) {
        if (isRaw) return;
        const { ctx, chartArea: a, scales: { x } } = chart;
        ctx.save();
        for (const p of pts) {
          if (!p.n_offline && !p.n_fail) continue;
          const px = x.getPixelForValue(p.t + half);
          if (px < a.left || px > a.right) continue;
          ctx.fillStyle = p.n_offline ? "#f43f5e" : "#fb923c";
          ctx.beginPath(); ctx.moveTo(px - 4, a.top + 1); ctx.lineTo(px + 4, a.top + 1); ctx.lineTo(px, a.top + 8); ctx.closePath(); ctx.fill();
        }
        ctx.restore();
      },
    };

    if (o.lat) {
      const lats = pts.map((p) => p.lat).filter((v) => v != null).sort((a, b) => a - b);
      const p98 = lats.length ? lats[Math.floor(lats.length * 0.98)] : 100;
      const yMax = Math.max(T.normal * 1.4, Math.ceil((p98 * 1.25) / 50) * 50);
      const barData = pts.map((p) => {
        const fail = isRaw ? p.status === 4 : p.lat == null;
        return { x: p.t + half, y: fail ? yMax : Math.min(Math.max(p.lat ?? 0, yMax * 0.012), yMax), p };
      });
      const colors = pts.map((p) => (isRaw ? RAW_COLOR[p.status] : p.lat == null ? "rgba(244,63,94,.55)" : aggColor(p)));
      charts.push(new Chart(o.lat, {
        type: "bar",
        data: { datasets: [{ data: barData, backgroundColor: colors, borderRadius: 1.5, barPercentage: 0.9, categoryPercentage: 1 }] },
        options: {
          responsive: true, maintainAspectRatio: false, parsing: false, normalized: true,
          interaction: { mode: "nearest", axis: "x", intersect: false },
          scales: { x: xScale(), y: { min: 0, max: yMax, grid: { color: th.grid }, border: { display: false }, ticks: { callback: (v) => `${v} ms` } } },
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: {
              title: (items) => { const p = items[0].raw.p; return isRaw ? `${f.date(p.t)} ${f.time(p.t)}` : `${f.date(p.t)} ${f.hm(p.t)}–${f.hm(p.t + S.bucket_ms)}`; },
              label: (it) => {
                const p = it.raw.p;
                if (isRaw) {
                  if (p.status === 4) return p.conn === "offline" ? "Sem resposta externa (interrupção)" : "Sem resposta externa";
                  const l = [`Latência: ${f.ms(p.lat)} ms`];
                  if (p.tg) for (const [k, n] of [["cf", "Cloudflare"], ["gg", "Google"], ["gw", "Roteador"]]) {
                    const t = p.tg[k]; if (t && t.m != null) l.push(`${n}: ${t.m === 0 ? "sem resposta" : f.ms(t.rtt) + " ms"}`);
                  }
                  return l;
                }
                const l = [`Média: ${f.ms(p.lat)} ms (mín. ${f.ms(p.lat_min)}, máx. ${f.ms(p.lat_max)})`, `Perda: ${f.pct(p.loss, 2)}`];
                if (p.n_offline) l.push(`Em interrupção: ${f.dur((p.covered_ms * p.n_offline) / p.n)}`);
                if (p.n_fail && !p.n_offline) l.push(`${p.n_fail} ciclo(s) sem resposta (microqueda)`);
                if (p.n) l.push(`Monitorado: ${f.dur(p.covered_ms)}`);
                return l;
              } } },
          },
        },
        plugins: [bandsPlugin, thresholdPlugin([[T.normal, "rgba(251,191,36,.6)", `normal até ${T.normal} ms`], [T.elevated, "rgba(251,146,60,.6)", `elevada até ${T.elevated} ms`]]), markersPlugin],
      }));
    }

    if (o.loss) {
      const lossData = pts.map((p) => ({ x: p.t + half, y: isRaw ? (p.total ? (100 * p.lost) / p.total : 0) : (p.loss ?? 0) }));
      charts.push(new Chart(o.loss, {
        type: "bar",
        data: { datasets: [{ data: lossData, backgroundColor: lossData.map((d) => (d.y > T.loss ? "#fb7185" : "rgba(251,113,133,.45)")), barPercentage: 0.92, categoryPercentage: 1 }] },
        options: {
          responsive: true, maintainAspectRatio: false, parsing: false,
          scales: { x: xScale(), y: { min: 0, suggestedMax: Math.max(T.loss * 2, 1), grid: { color: th.grid }, border: { display: false }, ticks: { callback: (v) => `${v}%` } } },
          plugins: { legend: { display: false }, tooltip: { callbacks: { title: (i) => `${f.date(i[0].raw.x - half)} ${isRaw ? f.time(i[0].raw.x) : f.hm(i[0].raw.x - half)}`, label: (i) => `Perda: ${f.pct(i.raw.y, 2)}` } } },
        },
        plugins: [bandsPlugin, thresholdPlugin([[T.loss, "rgba(251,113,133,.7)", `limite ${f.num(T.loss, 1)}%`]])],
      }));
    }

    if (o.days && o.byDay && o.byDay.length) {
      const days = o.byDay;
      charts.push(new Chart(o.days, {
        data: {
          labels: days.map((d) => `${f.wd(d.ts)} ${f.dm(d.ts)}`),
          datasets: [
            { type: "bar", label: "Sem conexão (min)", data: days.map((d) => +(d.outage_ms / 60000).toFixed(2)), backgroundColor: "rgba(244,63,94,.75)", borderRadius: 3, yAxisID: "y", order: 2 },
            { type: "line", label: "Disponibilidade (%)", data: days.map((d) => d.availability_pct), borderColor: o.print ? "#0f766e" : "#5eead4", backgroundColor: o.print ? "#0f766e" : "#5eead4", pointRadius: 3, tension: 0.2, yAxisID: "y2", order: 1 },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          scales: {
            x: { grid: { display: false } },
            y: { beginAtZero: true, grid: { color: th.grid }, border: { display: false }, title: { display: true, text: "min sem conexão" } },
            y2: { position: "right", suggestedMin: 98, max: 100, grid: { display: false }, border: { display: false }, ticks: { callback: (v) => `${v}%` } },
          },
          plugins: { legend: { labels: { boxWidth: 10, boxHeight: 10 } } },
        },
      }));
    }
    return charts;
  }

  // ------------------------------------------------------------ mapa de calor
  const HEAT_COLORS = ["rgba(52,211,153,.30)", "rgba(250,204,21,.45)", "rgba(251,146,60,.62)", "rgba(244,63,94,.72)", "rgba(225,29,72,1)"];
  function metrics(T) {
    return {
      indisp: { label: "Indisponibilidade na hora", lv: (c) => { const r = c.monitored_ms ? c.offline_ms / c.monitored_ms : 0; return r <= 0 ? 0 : r < 0.005 ? 1 : r < 0.02 ? 2 : r < 0.1 ? 3 : 4; },
                scale: ["0", "< 0,5%", "< 2%", "< 10%", "≥ 10%"] },
      falhas: { label: "Quedas e microquedas", lv: (c) => { const n = c.outages * 3 + c.micro; return n === 0 ? 0 : n <= 1 ? 1 : n <= 3 ? 2 : n <= 6 ? 3 : 4; },
                scale: ["nenhuma", "1 microqueda", "até 3", "até 6", "mais"] },
      perda: { label: "Perda de pacotes", lv: (c) => { const v = c.loss_pct || 0; return v < 0.5 ? 0 : v < 1 ? 1 : v < 2 ? 2 : v < 5 ? 3 : 4; },
               scale: ["< 0,5%", "< 1%", "< 2%", "< 5%", "≥ 5%"] },
      lat: { label: "Latência média", lv: (c) => { const v = c.lat_avg; if (v == null) return 0; return v < T.normal * 0.6 ? 0 : v < T.normal ? 1 : v < (T.normal + T.elevated) / 2 ? 2 : v < T.elevated ? 3 : 4; },
             scale: [`< ${Math.round(T.normal * 0.6)} ms`, `< ${T.normal} ms`, "", `< ${T.elevated} ms`, `≥ ${T.elevated} ms`] },
    };
  }
  function heatmapHtml(heat, metric, T) {
    const m = metrics(T)[metric];
    let h = '<div class="heat"><div></div>' + Array.from({ length: 24 }, (_, i) => `<div class="hd">${i % 3 === 0 ? pad(i) : ""}</div>`).join("");
    for (const d of heat.days) {
      h += `<div class="dl">${f.wd(d.ts)} ${f.dm(d.ts)}</div>`;
      d.hours.forEach((c, i) => {
        if (!c || !c.monitored_ms) { h += '<div class="c nd" title="Sem monitoramento"></div>'; return; }
        const tip = `${f.wd(d.ts)} ${f.dm(d.ts)}, ${pad(i)}h–${pad((i + 1) % 24)}h\nMonitorado: ${f.dur(c.monitored_ms)}\nSem conexão: ${f.dur(c.offline_ms)}\nInterrupções: ${c.outages} · microquedas: ${c.micro}\nPerda: ${f.pct(c.loss_pct, 2)} · latência média: ${f.ms(c.lat_avg)} ms`;
        h += `<div class="c" style="background:${HEAT_COLORS[m.lv(c)]}" title="${f.esc(tip)}"></div>`;
      });
    }
    const scale = `${m.label}: ` + m.scale.map((s, i) => `<i style="background:${HEAT_COLORS[i]}"></i>${s}`).join(" ");
    return { grid: h + "</div>", scale };
  }
  function worstHtml(heat) {
    return heat.worst_hours.length
      ? `Horários com mais problemas: ${heat.worst_hours.map((h) => `<b>${pad(h)}h–${pad((h + 1) % 24)}h</b>`).join(" ")}`
      : "Nenhum horário se destacou por problemas no período.";
  }

  // ------------------------------------------------------------ faixas por dia
  function stripsHtml(start, end, events) {
    const DAYMS = 86400000;
    const d0 = new Date(start); d0.setHours(0, 0, 0, 0);
    let days = [];
    for (let d = new Date(d0); d.getTime() < end; d.setDate(d.getDate() + 1)) days.push(d.getTime());
    const evs = events.filter((e) => ["interrupcao", "microqueda", "monitor_inativo"].includes(e.type));
    const dayHas = (t) => evs.some((e) => (e.end_ts || end) > t && e.start_ts < t + DAYMS);
    if (days.length > 62) days = days.filter(dayHas);
    if (!days.length) return '<div class="empty">Sem dados.</div>';
    let h = '<div class="strips">';
    for (const t of days) {
      const t1 = t + DAYMS;
      let marks = "", down = 0;
      for (const e of evs) {
        if (e.type === "microqueda") {
          if (e.start_ts < Math.max(t, start) || e.start_ts >= Math.min(t1, end)) continue;
        }
        const a = Math.max(e.start_ts, t, start), b = Math.min(e.end_ts || end, t1, end);
        if (b <= a && e.type !== "microqueda") continue;
        const left = ((a - t) / DAYMS) * 100, width = Math.max(((b - a) / DAYMS) * 100, 0);
        const cls = e.type === "interrupcao" ? "o" : e.type === "microqueda" ? "m" : "x";
        if (e.type === "interrupcao") down += b - a;
        marks += `<i class="${cls}" style="left:${left.toFixed(3)}%;width:${width.toFixed(3)}%" title="${f.esc(e.label)} ${f.time(e.start_ts)} · ${f.dur(e.duration_ms)}"></i>`;
      }
      if (start > t) marks += `<i class="x" style="left:0;width:${(((start - t) / DAYMS) * 100).toFixed(3)}%"></i>`;
      if (end < t1) marks += `<i class="x" style="left:${(((end - t) / DAYMS) * 100).toFixed(3)}%;right:0"></i>`;
      h += `<div class="strip"><span class="dl">${f.wd(t)} ${f.dm(t)}</span><div class="bar">${marks}</div><span class="dt">${down ? f.dur(down) : "—"}</span></div>`;
    }
    h += `</div><div class="hours" style="margin-top:4px"><span></span><div>${["00h", "06h", "12h", "18h", "24h"].map((x) => `<span>${x}</span>`).join("")}</div><span style="text-align:right">sem conexão</span></div>`;
    return h;
  }

  window.PeriodViz = { f, renderCharts, heatmapHtml, worstHtml, stripsHtml, HEAT_COLORS };
})();
