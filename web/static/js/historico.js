/* Página Histórico: análise de qualquer período. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const V = window.PeriodViz;
  const f = V.f;
  const MIN = 60000, H = 3600000, DAY = 86400000;
  const PRESETS = { "5m": 5 * MIN, "15m": 15 * MIN, "1h": H, "6h": 6 * H, "24h": DAY, "7d": 7 * DAY, "30d": 30 * DAY };
  const FILTERS = {
    falhas: ["interrupcao", "microqueda"],
    instab: ["perda_pacotes", "latencia_elevada", "alerta_antena"],
    monitor: ["monitor_inativo", "monitor_iniciado", "monitor_encerrado"],
    alter: ["modo_alterado", "tipo_conexao_alterado", "configuracoes_alteradas", "rede_alterada", "ajuste_relogio"],
    todos: [],
  };
  const TARGET = { gw: "Roteador", cf: "Cloudflare", gg: "Google", dish: "Equipamento do provedor" };

  const S = { preset: "24h", start: null, end: null, data: null, charts: [], heat: "indisp", filter: "falhas",
              timer: null, expanded: new Set(), loading: false, shown: 150 };

  // ------------------------------------------------------------ período
  function readHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    if (h.get("de") && h.get("ate")) {
      S.preset = "custom"; S.start = Number(h.get("de")); S.end = Number(h.get("ate"));
    } else if (PRESETS[h.get("p")]) {
      S.preset = h.get("p");
    }
  }
  function writeHash() {
    const h = S.preset === "custom" ? `de=${S.start}&ate=${S.end}` : `p=${S.preset}`;
    history.replaceState(null, "", `#${h}`);
  }
  function range() {
    if (S.preset === "custom") return [S.start, S.end];
    const now = Date.now();
    return [now - PRESETS[S.preset], now];
  }
  const toLocalInput = (t) => {
    const d = new Date(t);
    return `${d.getFullYear()}-${f.pad(d.getMonth() + 1)}-${f.pad(d.getDate())}T${f.pad(d.getHours())}:${f.pad(d.getMinutes())}`;
  };

  function setPreset(p) {
    S.preset = p;
    $("presets").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.p === p));
    $("custom-range").hidden = p !== "custom";
    if (p === "custom") {
      if (S.start == null) { S.end = Date.now(); S.start = S.end - DAY; }
      $("from").value = toLocalInput(S.start);
      $("to").value = toLocalInput(S.end);
    }
    writeHash();
    load();
  }

  $("presets").addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (b) setPreset(b.dataset.p);
  });
  $("custom-range").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const a = new Date($("from").value).getTime(), b = new Date($("to").value).getTime();
    if (!(b > a)) { alert("O fim do período precisa ser depois do início."); return; }
    S.start = a; S.end = b;
    writeHash();
    load();
  });

  // ------------------------------------------------------------ carga
  async function load(silent) {
    if (S.loading) return;
    S.loading = true;
    clearTimeout(S.timer);
    const [start, end] = range();
    try {
      const [ra, rt] = await Promise.all([
        fetch(`/api/analysis?start=${start}&end=${end}`, { cache: "no-store" }),
        fetch(`/api/timeline?start=${start}&end=${end}&limit=2000`, { cache: "no-store" }),
      ]);
      if (!ra.ok) throw new Error((await ra.json()).detail || `HTTP ${ra.status}`);
      S.data = await ra.json();
      S.events = (await rt.json()).events;
      S.start = start; S.end = end;
      render();
    } catch (e) {
      if (!silent) $("period-label").textContent = `Erro ao carregar: ${e.message}`;
    } finally {
      S.loading = false;
    }
    // Períodos curtos e relativos ao momento atual se atualizam sozinhos.
    if (S.preset !== "custom") {
      const span = PRESETS[S.preset];
      const every = span <= H ? 5000 : span <= DAY ? 30000 : 120000;
      S.timer = setTimeout(() => load(true), every);
    }
  }

  // ------------------------------------------------------------ desenho
  function kpi(l, v, s = "", cls = "") {
    return `<article class="kpi"><div class="kpi-label">${l}</div><div class="kpi-value ${cls}">${v}</div><div class="kpi-sub">${s}</div></article>`;
  }

  function render() {
    const D = S.data, st = D.stats, T = D.thresholds;
    const endEff = Math.min(S.end, D.now);
    $("period-label").textContent = `${f.date(S.start)} ${f.hm(S.start)} → ${f.date(endEff)} ${f.hm(endEff)}`;
    $("btn-report").href = `/relatorio?tipo=personalizado&inicio=${S.start}&fim=${Math.max(endEff, S.start + 1000)}`;
    $("series-label").textContent = `Cada barra: ${D.series.label}`;
    $("marker-legend").hidden = D.series.kind === "raw";

    const av = st.availability_pct;
    $("kpis").innerHTML = [
      kpi("Disponibilidade", av == null ? "—" : f.pct(av, 3), `${f.dur(st.monitored_ms)} monitorados (${f.num(st.coverage_pct, 1)}%)`, av == null ? "" : av >= 99.9 ? "c-ok" : av >= 99 ? "c-warn" : "c-bad"),
      kpi("Interrupções", f.num(st.outage_count), st.outages_per_day != null ? `${f.num(st.outages_per_day, 1)} por dia monitorado` : "confirmadas", st.outage_count ? "c-bad" : "c-ok"),
      kpi("Tempo sem conexão", f.dur(st.outage_ms), st.outage_avg_ms ? `média de ${f.dur(st.outage_avg_ms)}` : "", st.outage_ms ? "c-bad" : "c-ok"),
      kpi("Maior interrupção", st.outage_longest ? f.dur(st.outage_longest.clipped_ms) : "nenhuma", st.outage_longest ? `${f.dm(st.outage_longest.start_ts)} às ${f.time(st.outage_longest.start_ts)}` : ""),
      kpi("Microquedas", f.num(st.micro_count), st.micro_per_hour != null ? `${f.num(st.micro_per_hour, 2)} por hora` : "", st.micro_count ? "c-warn" : "c-ok"),
      kpi("Latência média", `${f.ms(st.lat_avg)}<span class="u">ms</span>`, `mín. ${f.ms(st.lat_min)} · máx. ${f.ms(st.lat_max)} ms`),
      kpi("Perda de pacotes", f.pct(st.loss_pct, 2), `limite ${f.num(T.loss, 1)}%`, st.loss_pct > T.loss ? "c-bad" : st.loss_pct > 0.5 ? "c-warn" : "c-ok"),
      kpi("Estabilidade", st.rating.label, `${st.instability_count} episódio(s) de instabilidade`,
        { excelente: "c-ok", boa: "c-ok", regular: "c-warn", ruim: "c-bad" }[st.rating.level] || ""),
    ].join("");

    // gráficos
    S.charts.forEach((c) => c.destroy());
    const multiDay = D.by_day.length > 1;
    $("days-card").hidden = !multiDay;
    $("lat-empty").hidden = D.series.points.length > 0;
    S.charts = V.renderCharts({
      lat: $("ch-lat"), loss: $("ch-loss"), days: multiDay ? $("ch-days") : null, byDay: D.by_day,
      series: D.series, bands: D.bands, start: S.start, end: endEff, thresholds: T, print: false,
    });

    renderHeat();
    $("worst").innerHTML = V.worstHtml(D.heatmap);
    $("dish-sec").hidden = !D.dish;
    $("dish-body").innerHTML = D.dish ? DishView.section(D.dish) : "";

    // por modo
    $("by-mode").innerHTML = D.by_mode.length ? `<table class="tbl"><thead><tr><th>Modo</th><th class="n">Monitorado</th><th class="n">Disponib.</th>
      <th class="n">Interrup.</th><th class="n">Microq.</th><th class="n">Latência</th><th class="n">Perda</th></tr></thead><tbody>
      ${D.by_mode.map((m) => `<tr><td><b>${f.esc(m.label)}</b></td><td class="n">${f.dur(m.monitored_ms)}</td><td class="n">${f.pct(m.availability_pct, 3)}</td>
        <td class="n">${m.outages}</td><td class="n">${m.micro}</td><td class="n">${f.ms(m.lat_avg)} ms</td><td class="n">${f.pct(m.loss_pct, 2)}</td></tr>`).join("")}
      </tbody></table>` : '<div class="empty">Sem medições no período.</div>';

    if (multiDay) {
      $("by-day").innerHTML = `<table class="tbl"><thead><tr><th>Dia</th><th class="n">Monitorado</th><th class="n">Disponib.</th><th class="n">Interrup.</th>
        <th class="n">Sem conexão</th><th class="n">Microq.</th><th class="n">Latência</th><th class="n">Pior latência</th><th class="n">Perda</th></tr></thead><tbody>
        ${D.by_day.map((d) => `<tr><td>${f.wd(d.ts)} ${f.date(d.ts)}</td><td class="n">${f.dur(d.monitored_ms)}</td>
          <td class="n ${d.availability_pct != null && d.availability_pct < 99 ? "c-bad" : ""}">${f.pct(d.availability_pct, 3)}</td>
          <td class="n">${d.outages}</td><td class="n">${f.dur(d.outage_ms)}</td><td class="n">${d.micro}</td><td class="n">${f.ms(d.lat_avg)} ms</td>
          <td class="n">${f.ms(d.lat_max)} ms</td><td class="n">${f.pct(d.loss_pct, 2)}</td></tr>`).join("")}</tbody></table>`;
    }

    $("strips").innerHTML = (S.end - S.start) >= 6 * H ? V.stripsHtml(S.start, endEff, S.events) : "";
    renderTimeline();
    const first = D.first_data_ts;
    $("foot-data").textContent = first ? `Dados disponíveis desde ${f.date(first)} ${f.hm(first)} · médias por minuto mantidas por mais tempo que as medições individuais` : "Ainda não há medições.";
  }

  function renderHeat() {
    const D = S.data;
    if (!D.heatmap.days.length) { $("heat").innerHTML = '<div class="empty">Sem medições no período.</div>'; $("heat-scale").innerHTML = ""; return; }
    const r = V.heatmapHtml(D.heatmap, S.heat, D.thresholds);
    $("heat").innerHTML = r.grid;
    $("heat-scale").innerHTML = r.scale;
  }
  $("heat-tools").addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    S.heat = b.dataset.m;
    $("heat-tools").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    renderHeat();
  });

  // ------------------------------------------------------------ linha do tempo
  const CAT = {
    interrupcao: ["out", "Interrupção"], microqueda: ["micro", "Microqueda"], perda_pacotes: ["inst", "Perda de pacotes"],
    latencia_elevada: ["inst", "Latência elevada"], monitor_inativo: ["off", "Monitor inativo"], monitor_iniciado: ["info", "Monitor iniciado"],
    monitor_encerrado: ["off", "Monitor encerrado"], modo_alterado: ["info", "Modo alterado"], tipo_conexao_alterado: ["info", "Tipo de conexão"],
    configuracoes_alteradas: ["info", "Configurações"], alerta_antena: ["inst", "Alerta da antena"], rede_alterada: ["info", "Rede alterada"], ajuste_relogio: ["info", "Ajuste do relógio"],
  };
  function describe(e) {
    const d = e.details || {};
    switch (e.type) {
      case "interrupcao": return `${e.open ? "em andamento há " : ""}${f.dur(e.duration_ms)} ${f.margin(e.precision_ms)} — ${f.esc(e.diagnosis_label || "")}${DishView.tag(e)}`;
      case "microqueda": return `${f.dur(e.duration_ms)} ${f.margin(e.precision_ms)} — ${f.esc(e.diagnosis_label || "")}${DishView.tag(e)}`;
      case "alerta_antena": return [d.ativados && d.ativados.length ? `ativado: ${f.esc(d.ativados.join(", "))}` : "", d.encerrados && d.encerrados.length ? `encerrado: ${f.esc(d.encerrados.join(", "))}` : ""].filter(Boolean).join(" · ");
      case "perda_pacotes": return `perda de até ${f.num(d.perda_max_pct, 1)}% na janela de ${d.janela_s || 60} s · ${e.open ? "em andamento" : f.dur(e.duration_ms)}`;
      case "latencia_elevada": return `mediana de até ${f.ms(d.mediana_max_ms)} ms (pico ${f.ms(d.pico_ms)} ms) · ${e.open ? "em andamento" : f.dur(e.duration_ms)}`;
      case "monitor_inativo": return `${f.dur(e.duration_ms)} sem medições — ${f.esc(d.motivo || "")}`;
      case "monitor_iniciado": return `versão ${f.esc(d.versao || "")}${d.simulacao ? " (simulação)" : ""}`;
      case "monitor_encerrado": return f.esc(d.motivo || "");
      case "modo_alterado": case "tipo_conexao_alterado": return `${f.esc(d.de)} → ${f.esc(d.para)}${d.provedor ? ` · ${f.esc(d.provedor)}` : ""}`;
      case "configuracoes_alteradas": return `alterado: ${f.esc((d.alterados || []).join(", "))}`;
      case "rede_alterada": return `gateway ${f.esc(d.gateway_anterior || "—")} → ${f.esc(d.gateway_atual || "—")}`;
      case "ajuste_relogio": return `diferença de ${f.num(d.diferenca_s, 1)} s`;
      default: return "";
    }
  }
  function detail(e) {
    if (!["interrupcao", "microqueda"].includes(e.type)) return "";
    const tg = Object.entries(e.targets || {}).map(([k, v]) => `${f.esc(TARGET[k] || k)}: ${f.esc(v)}`).join(" · ");
    return `<div class="tl-detail">
      <dl class="detail-grid">
        <div><dt>Início</dt><dd>${f.date(e.start_ts)} ${f.time(e.start_ts)} <span class="hint">${f.margin(e.precision_ms)}</span></dd></div>
        <div><dt>Término</dt><dd>${e.end_ts ? `${f.time(e.end_ts)} <span class="hint">${f.margin(e.precision_ms)}</span>` : "em andamento"}</dd></div>
        <div><dt>Modo</dt><dd>${f.esc(e.mode_label || "—")}${e.wifi_signal != null ? ` · sinal ${e.wifi_signal}%` : ""}</dd></div>
        <div><dt>Roteador</dt><dd>${f.esc(e.router_state || "—")}</dd></div>
        <div><dt>Destinos</dt><dd>${tg || "—"}</dd></div>
        <div><dt>Latência antes / depois</dt><dd>${f.ms(e.lat_before)} / ${f.ms(e.lat_after)} ms</dd></div>
      </dl>
      ${DishView.box(e)}
      <div class="diag-box"><b>${f.esc(e.diagnosis_label || "Diagnóstico")}</b>${f.esc(e.diagnosis_text || "")}</div>
      ${e.details && e.details.observacao ? `<div class="obs">${f.esc(e.details.observacao)}</div>` : ""}</div>`;
  }
  function renderTimeline() {
    const types = FILTERS[S.filter];
    const evs = (S.events || []).filter((e) => !types.length || types.includes(e.type)).sort((a, b) => b.start_ts - a.start_ts);
    if (!evs.length) { $("timeline").innerHTML = '<div class="empty">Nenhum evento deste tipo no período.</div>'; return; }
    let html = "", lastDay = null;
    for (const e of evs.slice(0, S.shown)) {
      const day = f.date(e.start_ts);
      if (day !== lastDay) { html += `<div class="tl-day">${f.wd(e.start_ts)}, ${day}</div>`; lastDay = day; }
      const [cls, label] = CAT[e.type] || ["info", e.label];
      const can = ["interrupcao", "microqueda"].includes(e.type);
      html += `<div class="tl-item${S.expanded.has(e.id) ? " open" : ""}${can ? " can" : ""}" data-id="${e.id}">
        <div class="tl-row"><span class="tl-time">${f.time(e.start_ts)}</span><span class="tl-dot ${cls}"></span>
        <span class="pill ${cls}">${label}</span><span class="tl-desc">${describe(e)}</span>${can ? '<span class="chev">›</span>' : ""}</div>
        ${detail(e)}</div>`;
    }
    if (evs.length > S.shown) html += `<div class="more"><span class="empty">Mostrando os ${S.shown} eventos mais recentes de ${evs.length}.</span> <button class="btn small" id="more-ev">Mostrar mais</button></div>`;
    $("timeline").innerHTML = html;
  }
  $("timeline").addEventListener("click", (ev) => {
    if (ev.target.id === "more-ev") { S.shown += 300; renderTimeline(); return; }
    const it = ev.target.closest(".tl-item.can");
    if (!it || ev.target.closest(".tl-detail")) return;
    const id = Number(it.dataset.id);
    if (S.expanded.has(id)) S.expanded.delete(id); else S.expanded.add(id);
    it.classList.toggle("open");
  });
  $("ev-filter").addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    S.filter = b.dataset.f;
    S.shown = 150;
    $("ev-filter").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    renderTimeline();
  });

  // ------------------------------------------------------------ início
  fetch("/api/health").then((r) => r.json()).then((h) => { $("sim-banner").hidden = !h.simulated; }).catch(() => {});
  fetch("/api/settings").then((r) => r.json()).then((s) => {
    if (s.provider && s.provider.equip_name) TARGET.dish = s.provider.equip_name;
  }).catch(() => {});
  readHash();
  setPreset(S.preset);
})();
