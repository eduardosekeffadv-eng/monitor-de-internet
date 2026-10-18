/* Relatório do Monitor de Internet: monta todas as seções a partir de REPORT_DATA. */
(function () {
  "use strict";
  const D = window.REPORT_DATA;
  const M = D.meta;
  const mode = document.body.dataset.mode;

  const V = window.PeriodViz;
  const f = V.f;
  const pad = f.pad;
  const sameDay = (a, b) => new Date(a).toDateString() === new Date(b).toDateString();

  const ST = D.stats;
  const T = D.thresholds;
  const TARGET = { gw: "Roteador", cf: "Cloudflare", gg: "Google", dish: (M.provider && M.provider.equip_name) || "Equipamento do provedor" };
  const MODE = M.modes;
  const span = M.end_effective - M.start;
  const multiDay = D.by_day.length > 1;

  // ------------------------------------------------------------ barra de ações
  const ICON_PRINT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9V3h12v6M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="7"/></svg>';
  const ICON_SAVE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0l-5-5m5 5l5-5M4 21h16"/></svg>';
  const tb = document.getElementById("toolbar");
  if (mode === "view") {
    tb.innerHTML = `<a class="tbtn" href="/relatorios">← Relatórios</a><span class="spacer">Visualizando o relatório no Monitor de Internet</span>
      <a class="tbtn" href="/relatorio/baixar${location.search}">${ICON_SAVE}Salvar arquivo HTML</a>
      <button class="tbtn primary" id="btn-print">${ICON_PRINT}Imprimir / salvar PDF</button>`;
  } else {
    tb.innerHTML = `<span class="spacer">Arquivo independente: abre sem o Monitor de Internet e sem internet.</span>
      <button class="tbtn primary" id="btn-print">${ICON_PRINT}Imprimir / salvar PDF</button>`;
  }
  document.getElementById("btn-print").addEventListener("click", () => window.print());

  // ------------------------------------------------------------ seções
  let sec = 0;
  const block = (title, lead, body, cls = "") =>
    `<section class="block ${cls}"><h2><span class="num">${pad(++sec)}</span>${title}</h2>${lead ? `<p class="lead">${lead}</p>` : ""}${body}</section>`;

  const prov = M.provider || {};
  const provText = prov.configured === false ? "não definido" : `${f.esc(prov.label)}${prov.name ? ` · ${f.esc(prov.name)}` : ""}`;
  const modesUsed = D.by_mode.map((m) => m.label).join(", ") || "—";

  let html = "";
  if (M.simulated) html += `<div class="sim">MODO SIMULAÇÃO — este relatório contém dados fictícios, gerados para demonstração.</div>`;

  html += `<header class="cover">
    <div>
      <div class="brand"><svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-linecap="round"><circle cx="16" cy="16" r="3.2" fill="currentColor" stroke="none"/><path d="M10.6 21.4a7.6 7.6 0 0 1 0-10.8M21.4 10.6a7.6 7.6 0 0 1 0 10.8" stroke-width="2"/><path d="M6.3 25.7a13.7 13.7 0 0 1 0-19.4M25.7 6.3a13.7 13.7 0 0 1 0 19.4" stroke-width="2" opacity=".55"/></svg>MONITOR DE INTERNET</div>
      <h1>${f.esc(M.title)} da conexão</h1>
      <div class="period">${f.esc(M.period_label)}</div>
      ${M.in_progress ? `<div class="note">Período em andamento — dados até ${f.date(M.end_effective)} às ${f.hm(M.end_effective)}.</div>` : ""}
    </div>
    <div class="meta">
      <div>Conexão: <b>${provText}</b></div>
      <div>Equipamento do provedor: <b>${f.esc(prov.equip_name || "—")}${prov.equip_host ? ` (${f.esc(prov.equip_host)})` : ""}</b></div>
      <div>Modo(s) de conexão do computador: <b>${f.esc(modesUsed)}</b></div>
      <div>Gerado em <b>${f.esc(M.generated_label)}</b> · v${f.esc(M.version)}</div>
    </div>
  </header>`;

  // veredito + anel de disponibilidade
  const av = ST.availability_pct;
  const ringColor = av == null ? "#64748b" : av >= 99.9 ? "var(--ok)" : av >= 99 ? "var(--warn)" : "var(--bad)";
  const circ = 2 * Math.PI * 80;
  const frac = av == null ? 0 : Math.max(0.005, av / 100);
  html += `<div class="verdict">
    <div class="ring"><svg viewBox="0 0 190 190"><circle cx="95" cy="95" r="80" fill="none" stroke="var(--line)" stroke-width="14"/>
      <circle cx="95" cy="95" r="80" fill="none" stroke="${ringColor}" stroke-width="14" stroke-linecap="round"
        stroke-dasharray="${(circ * frac).toFixed(1)} ${circ.toFixed(1)}"/></svg>
      <div class="val"><b style="color:${ringColor}">${av == null ? "—" : f.pct(av, av >= 99.995 ? 2 : 3)}</b><span>disponibilidade</span></div></div>
    <div><span class="badge lv-${ST.rating.level}">Estabilidade ${f.esc(ST.rating.label)}</span>
      <h2>Resumo do período</h2><p>${f.esc(D.summary)}</p>
      <div class="criteria">${f.esc(ST.rating.text)}</div></div>
  </div>`;

  const kpi = (l, v, s = "", cls = "") => `<div class="kpi"><div class="l">${l}</div><div class="v ${cls}">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
  html += `<div class="kpis">
    ${kpi("Interrupções confirmadas", f.num(ST.outage_count), ST.outages_per_day != null ? `${f.num(ST.outages_per_day, 1)} por dia monitorado` : "", ST.outage_count ? "c-bad" : "c-ok")}
    ${kpi("Tempo sem conexão", f.dur(ST.outage_ms), ST.outage_avg_ms ? `média de ${f.dur(ST.outage_avg_ms)} por interrupção` : "nenhum", ST.outage_ms ? "c-bad" : "c-ok")}
    ${kpi("Maior interrupção", ST.outage_longest ? f.dur(ST.outage_longest.clipped_ms) : "—", ST.outage_longest ? `${f.date(ST.outage_longest.start_ts)} às ${f.time(ST.outage_longest.start_ts)}` : "nenhuma")}
    ${kpi("Microquedas", f.num(ST.micro_count), ST.micro_per_hour != null ? `${f.num(ST.micro_per_hour, 2)} por hora monitorada` : "falhas de 1 a 2 s", ST.micro_count ? "c-warn" : "c-ok")}
    ${kpi("Latência média", `${f.ms(ST.lat_avg)}<small>ms</small>`, `mín. ${f.ms(ST.lat_min)} · máx. ${f.ms(ST.lat_max)} ms`)}
    ${kpi("Perda de pacotes", f.pct(ST.loss_pct, 2), `limite de instabilidade: ${f.num(T.loss, 1)}%`, ST.loss_pct > T.loss ? "c-bad" : ST.loss_pct > 0.5 ? "c-warn" : "c-ok")}
    ${kpi("Tempo monitorado", f.dur(ST.monitored_ms), `${f.num(ST.coverage_pct, 1)}% do período`)}
    ${kpi("Períodos de instabilidade", f.num(ST.instability_count), ST.instability_count ? `${f.dur(ST.instability_ms)} no total` : "nenhum", ST.instability_count ? "c-warn" : "c-ok")}
  </div>`;

  // gráfico de latência
  html += block("Latência e quedas ao longo do período",
    `Cada barra representa ${f.esc(D.series.label)}. A cor indica a qualidade; faixas vermelhas marcam interrupções confirmadas e faixas cinzas, períodos sem monitoramento.`,
    `<div class="chart"><canvas id="ch-lat"></canvas></div>
     <div class="legend"><span><i style="background:#34d399"></i>Normal (até ${f.num(T.normal)} ms)</span><span><i style="background:#fbbf24"></i>Elevada</span>
     <span><i style="background:#fb923c"></i>Acima de ${f.num(T.elevated)} ms</span><span><i style="background:#fb7185"></i>Perda acima do limite</span>
     ${D.series.kind === "raw" ? '<span><i style="background:#f43f5e"></i>Sem resposta externa</span>' : ""}<span><i style="background:rgba(100,116,139,.5)"></i>Sem monitoramento</span>
     ${D.series.kind === "agg" ? '<span>▼ <span style="color:#f43f5e">vermelho</span>: houve interrupção no intervalo · ▼ <span style="color:#fb923c">laranja</span>: houve microqueda</span>' : ""}</div>
     <h3 style="margin:20px 0 6px;font-size:13.5px;color:var(--text-2)">Perda de pacotes</h3>
     <div class="chart small"><canvas id="ch-loss"></canvas></div>`);

  // comparação entre dias
  if (multiDay) {
    const rows = D.by_day.map((d) => `<tr><td>${f.wd(d.ts)} ${f.date(d.ts)}</td><td class="n">${f.dur(d.monitored_ms)}</td>
      <td class="n ${d.availability_pct != null && d.availability_pct < 99 ? "c-bad" : ""}">${f.pct(d.availability_pct, 3)}</td>
      <td class="n">${d.outages}</td><td class="n">${f.dur(d.outage_ms)}</td><td class="n">${d.micro}</td>
      <td class="n">${f.ms(d.lat_avg)} ms</td><td class="n">${f.pct(d.loss_pct, 2)}</td></tr>`).join("");
    html += block("Comparação entre os dias", "Tempo sem conexão (barras) e disponibilidade (linha) de cada dia monitorado.",
      `<div class="chart small"><canvas id="ch-days"></canvas></div>
       <table style="margin-top:14px"><thead><tr><th>Dia</th><th class="n">Monitorado</th><th class="n">Disponib.</th><th class="n">Interrup.</th>
       <th class="n">Sem conexão</th><th class="n">Microq.</th><th class="n">Latência</th><th class="n">Perda</th></tr></thead><tbody>${rows}</tbody></table>`, "flow");
  }

  // mapa de calor
  const HM = D.heatmap;
  if (HM.days.length) {
    const worst = `<div class="worst">${V.worstHtml(HM)}</div>`;
    html += block("Horários com mais problemas",
      "Cada célula é uma hora de um dia. Passe o mouse para ver os detalhes. Células hachuradas: sem monitoramento.",
      `<div class="heat-tools no-print" id="heat-tools">
        <button data-m="indisp" class="active">Indisponibilidade</button><button data-m="falhas">Quedas e microquedas</button>
        <button data-m="perda">Perda de pacotes</button><button data-m="lat">Latência média</button></div>
       <div id="heat"></div><div class="scale" id="heat-scale"></div>${worst}`, "flow");
  }

  // por modo
  if (D.by_mode.length) {
    const rows = D.by_mode.map((m) => `<tr><td><b>${f.esc(m.label)}</b></td><td class="n">${f.dur(m.monitored_ms)}</td>
      <td class="n">${f.pct(m.availability_pct, 3)}</td><td class="n">${m.outages}</td><td class="n">${f.dur(m.outage_ms)}</td>
      <td class="n">${m.outages_per_day != null ? f.num(m.outages_per_day, 2) : "—"}</td><td class="n">${m.micro}</td>
      <td class="n">${f.ms(m.lat_avg)} ms</td><td class="n">${f.pct(m.loss_pct, 2)}</td></tr>`).join("");
    html += block("Comparativo por modo de conexão do computador",
      "Ajuda a separar problemas da rede de casa (Wi-Fi) dos problemas da conexão com a internet.",
      `<table><thead><tr><th>Modo</th><th class="n">Monitorado</th><th class="n">Disponib.</th><th class="n">Interrup.</th>
       <th class="n">Sem conexão</th><th class="n">Interr./dia</th><th class="n">Microq.</th><th class="n">Latência</th><th class="n">Perda</th></tr></thead>
       <tbody>${rows}</tbody></table>`);
  }

  // antena Starlink
  const DV = window.DishView;
  if (D.dish) {
    html += block("Quedas segundo a antena Starlink",
      "Motivos informados pela própria antena (API local de diagnóstico). Separa as falhas da rede da Starlink (satélites, troca de satélite, falta de cobertura) das ligadas à instalação (obstrução, energia, reinício).",
      DV.section(D.dish), "flow");
  }

  // indicadores de estabilidade
  html += block("Indicadores de estabilidade", "",
    `<div class="kpis" style="margin-top:0">
      ${kpi("Interrupções por dia", ST.outages_per_day != null ? f.num(ST.outages_per_day, 2) : "—", "média por dia monitorado")}
      ${kpi("Microquedas por hora", ST.micro_per_hour != null ? f.num(ST.micro_per_hour, 2) : "—", "falhas curtas, abaixo do critério")}
      ${kpi("Episódios de latência elevada", f.num(ST.latency_events), `mediana acima de ${f.num(T.normal)} ms`)}
      ${kpi("Episódios de perda de pacotes", f.num(ST.loss_events), `acima de ${f.num(T.loss, 1)}% em 60 s`)}
      ${kpi("Duração média das interrupções", f.dur(ST.outage_avg_ms))}
      ${kpi("Tempo total instável", f.dur(ST.instability_ms), "latência elevada ou perda")}
      ${kpi("Períodos sem monitoramento", f.num(ST.inactive_count), "PC desligado, suspenso ou programa fechado")}
      ${kpi("Cobertura do monitoramento", f.pct(ST.coverage_pct, 1), "parte do período efetivamente medida")}
    </div>`);

  // linha do tempo
  const tlDays = buildStrips();
  const keyEvents = D.timeline.filter((e) => ["interrupcao", "monitor_inativo", "modo_alterado", "tipo_conexao_alterado"].includes(e.type));
  const evPill = { interrupcao: "out", microqueda: "micro", perda_pacotes: "inst", latencia_elevada: "inst", monitor_inativo: "off" };
  const evRows = keyEvents.slice(0, 400).map((e) => {
    let txt = "";
    if (e.type === "interrupcao") txt = `${f.dur(e.duration_ms)} ${f.margin(e.precision_ms)} — ${f.esc(e.diagnosis_label || "")}${DV.tag(e)}`;
    else if (e.type === "monitor_inativo") txt = `${f.dur(e.duration_ms)} sem medições (${f.esc((e.details && e.details.motivo) || "")})`;
    else if (e.type === "modo_alterado") txt = `${f.esc(e.details.de)} → ${f.esc(e.details.para)}`;
    else if (e.type === "tipo_conexao_alterado") txt = `${f.esc(e.details.de)} → ${f.esc(e.details.para)}`;
    return `<div class="ev"><span class="t">${f.date(e.start_ts)} ${f.time(e.start_ts)}</span><span><span class="pill ${evPill[e.type] || "info"}">${f.esc(e.label)}</span></span><span>${txt}</span></div>`;
  }).join("");
  html += block("Linha do tempo das quedas",
    "Vermelho: interrupção confirmada · laranja: microqueda · cinza hachurado: sem monitoramento.",
    `${tlDays}<h3 style="margin:18px 0 4px;font-size:13.5px;color:var(--text-2)">Eventos principais</h3>
     <div class="events">${evRows || '<div class="empty">Nenhum evento principal no período.</div>'}</div>
     ${keyEvents.length > 400 ? `<p class="muted">Mostrando os 400 primeiros de ${keyEvents.length} eventos.</p>` : ""}`, "flow");

  // lista detalhada
  const outRows = D.outages.map((e, i) => {
    const eq = e.targets && e.targets.dish ? `<span class="sub">${f.esc(TARGET.dish)}: ${f.esc(e.targets.dish)}</span>` : "";
    const wifi = e.wifi_signal != null ? `<span class="sub">sinal Wi-Fi ${e.wifi_signal}%</span>` : "";
    return `<tr><td class="n">${i + 1}</td><td>${f.date(e.start_ts)}<span class="sub">${f.wd(e.start_ts)}</span></td>
      <td class="n">${f.time(e.start_ts)}<span class="sub">${f.margin(e.precision_ms)}</span></td>
      <td class="n">${e.end_ts ? f.time(e.end_ts) : "em andamento"}${e.end_ts && !sameDay(e.start_ts, e.end_ts) ? `<span class="sub">${f.dm(e.end_ts)}</span>` : ""}</td>
      <td class="n"><b>${f.dur(e.duration_ms)}</b>${e.truncated ? '<span class="sub">até a última medição</span>' : ""}</td>
      <td title="${f.esc(e.diagnosis_text || "")}">${f.esc(e.diagnosis_label || "—")}${e.dish ? `<span class="sub">antena: ${f.esc(e.dish.label)}</span>` : ""}</td>
      <td>${f.esc(e.router_state || "—")}${eq}</td>
      <td>${f.esc(e.mode_label || "—")}${wifi}</td>
      <td class="n">${f.ms(e.lat_before)} / ${f.ms(e.lat_after)} ms</td></tr>`;
  }).join("");
  html += block("Lista detalhada das interrupções",
    "Interrupção confirmada: nenhum destino externo (Cloudflare e Google) respondeu por " +
    `${M.criteria.outage_confirm_cycles} ciclos seguidos. Horários com a margem real da medição.`,
    D.outages.length ? `<div style="overflow-x:auto"><table><thead><tr><th class="n">#</th><th>Data</th><th class="n">Início</th><th class="n">Término</th>
      <th class="n">Duração</th><th>Diagnóstico provável</th><th>Roteador</th><th>Modo</th><th class="n">Latência antes / depois</th></tr></thead>
      <tbody>${outRows}</tbody></table></div>` : '<div class="empty">Nenhuma interrupção confirmada no período.</div>', "flow");

  // microquedas
  if (D.micros.length) {
    const mr = D.micros.map((e, i) => `<tr><td class="n">${i + 1}</td><td>${f.date(e.start_ts)}</td><td class="n">${f.time(e.start_ts)}</td>
      <td class="n">${f.dur(e.duration_ms)} ${f.margin(e.precision_ms)}</td><td>${f.esc(e.diagnosis_label || "—")}</td><td>${e.dish ? f.esc(e.dish.label) : "—"}</td><td>${f.esc(e.mode_label || "")}</td></tr>`).join("");
    html += block("Microquedas", `Falhas de todos os destinos externos por menos de ${M.criteria.outage_confirm_cycles} ciclos. Não contam como interrupção, mas revelam instabilidade.`,
      `<details ${D.micros.length <= 30 ? "open" : ""}><summary>Mostrar as ${D.micros.length} microquedas</summary>
       <table><thead><tr><th class="n">#</th><th>Data</th><th class="n">Horário</th><th class="n">Duração</th><th>Diagnóstico provável</th><th>Motivo (antena)</th><th>Modo</th></tr></thead>
       <tbody>${mr}</tbody></table></details>`, "flow");
  }

  // metodologia
  const C = M.criteria;
  const tgs = C.targets.filter((t) => t.enabled && t.host).map((t) => `${f.esc(t.name)} (${f.esc(t.host === "auto" ? "gateway detectado automaticamente" : t.host)})`).join(", ");
  html += block("Metodologia e critérios", "",
    `<div class="method">
      <p><b>Medição.</b> A cada ${f.num(C.interval_ms / 1000, 1).replace(",0", "")} s o computador verifica, em paralelo: ${tgs}. É usado ICMP (ping) nativo do Windows, com teste TCP como alternativa, e tempo limite de ${f.num(C.timeout_ms)} ms. Nenhum teste de velocidade é feito, para não consumir banda.</p>
      <p><b>Interrupção confirmada.</b> Nenhum destino externo responde por ${C.outage_confirm_cycles} ciclos seguidos. A falha de um único servidor não é considerada queda. O início é contado a partir do primeiro ciclo sem resposta; início e término têm margem de ±1 intervalo de medição.</p>
      <p><b>Microqueda.</b> Todos os destinos externos sem resposta por menos de ${C.outage_confirm_cycles} ciclos.</p>
      <p><b>Instabilidade.</b> Perda de pacotes acima de ${f.num(C.loss_threshold_pct, 1)}% em janela de ${C.loss_window_s} s, ou mediana da latência acima de ${f.num(C.lat_normal_max_ms)} ms (latência elevada até ${f.num(C.lat_elevated_max_ms)} ms; acima disso, crítica).</p>
      <p><b>Disponibilidade.</b> (tempo monitorado − tempo em interrupção confirmada) ÷ tempo monitorado. Períodos com o computador desligado, suspenso ou com o programa fechado não são considerados, nem como tempo online nem como queda.</p>
      <p><b>Diagnóstico.</b> Inferido a partir das respostas do roteador, do equipamento do provedor e do sinal Wi-Fi no momento da falha. É uma indicação provável, não uma certeza.</p>
      ${D.dish ? "<p><b>Dados da antena Starlink.</b> Lidos da interface local de diagnóstico da antena (a mesma usada pelo aplicativo da Starlink), somente leitura. Cada queda informada pela antena é associada à queda do monitor que ocorreu no mesmo momento (tolerância de 5 s para diferenças de relógio). Essa interface não é oficialmente documentada pela Starlink.</p>" : ""}
    </div>`);

  html += `<footer class="foot">Monitor de Internet v${f.esc(M.version)} · relatório gerado em ${f.esc(M.generated_label)} · dados medidos localmente neste computador</footer>`;
  document.getElementById("report").innerHTML = html;

  // ------------------------------------------------------------ mapa de calor
  function renderHeat(metric) {
    const el = document.getElementById("heat");
    if (!el) return;
    const r = V.heatmapHtml(HM, metric, T);
    el.innerHTML = r.grid;
    document.getElementById("heat-scale").innerHTML = r.scale;
  }
  const tools = document.getElementById("heat-tools");
  if (tools) {
    tools.addEventListener("click", (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      tools.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
      renderHeat(b.dataset.m);
    });
    renderHeat("indisp");
  }

  // ------------------------------------------------------------ faixas por dia
  function buildStrips() {
    return V.stripsHtml(M.start, M.end_effective, D.timeline);
  }

  // ------------------------------------------------------------ gráficos
  let charts = [];
  function buildCharts(print) {
    charts.forEach((c) => c.destroy());
    charts = V.renderCharts({
      lat: document.getElementById("ch-lat"), loss: document.getElementById("ch-loss"),
      days: document.getElementById("ch-days"), byDay: D.by_day,
      series: D.series, bands: D.bands, start: M.start, end: M.end_effective, thresholds: T, print,
    });
  }

  buildCharts(false);
  window.addEventListener("beforeprint", () => {
    document.querySelectorAll("details").forEach((d) => { d.dataset.wasOpen = d.open ? "1" : ""; d.open = true; });
    buildCharts(true);
  });
  window.addEventListener("afterprint", () => {
    document.querySelectorAll("details").forEach((d) => { d.open = d.dataset.wasOpen === "1"; });
    buildCharts(false);
  });
})();
