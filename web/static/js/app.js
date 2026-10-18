/* Painel principal do Monitor de Internet. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  // Nomes exibidos; o do equipamento do provedor muda com o tipo de conexão.
  const TARGET_NAMES = { gw: "Roteador", cf: "Cloudflare", gg: "Google", dish: "Equipamento do provedor" };
  const METHOD = { 0: "sem resposta", 1: "ICMP", 2: "TCP" };
  const FAILURE_TYPES = ["interrupcao", "microqueda"];

  const ICONS = {
    cabo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="3" width="12" height="9" rx="1.5"/><path d="M9 3v3M12 3v3M15 3v3M9 12v3h6v-3M12 15v6"/></svg>',
    wifi_direto: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M2.5 8.5a14 14 0 0 1 19 0M5.5 12a9.5 9.5 0 0 1 13 0M8.6 15.4a5 5 0 0 1 6.8 0"/><circle cx="12" cy="19" r="1.3" fill="currentColor"/></svg>',
    wifi_roteador: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="14" width="18" height="6" rx="1.6"/><path d="M7 17h.01M10.5 17h.01M8 14l-2-5M16 14l2-5"/><path d="M8.5 6.5a5 5 0 0 1 7 0M10.4 8.8a2.2 2.2 0 0 1 3.2 0"/></svg>',
  };
  const MODE_HELP = {
    cabo: "Computador ligado por cabo de rede",
    wifi_direto: "Wi-Fi do próprio equipamento do provedor",
    wifi_roteador: "Wi-Fi do seu roteador próprio",
  };

  const S = {
    snap: null,
    settings: {},
    modes: {},
    mode: null,
    sessionId: null,
    sessionStart: null,
    last: null,
    stats: null,
    net: null,
    targets: [],
    dead: [],
    failures: new Map(),
    open: new Map(),
    expanded: new Set(),
    filter: "all",
    ws: null,
    wsRetry: 1000,
    modalChoice: null,
    provider: null,
    providerChoice: null,
    equipDirty: false,
    dish: null,
  };
  const DV = window.DishView;

  // ------------------------------------------------------------ gráfico
  const chart = new RealtimeBars($("rt-chart"), {
    tooltip: $("chart-tooltip"),
    emptyEl: $("chart-empty"),
    scrollEl: $("rt-scroll"),
  });

  function initWindowSelect(defaultS) {
    let w = defaultS;
    try { w = parseInt(localStorage.getItem("mi.window") || defaultS, 10) || defaultS; } catch (e) { /* sem armazenamento */ }
    const btns = $("window-select").querySelectorAll("button");
    const apply = (v) => {
      btns.forEach((b) => b.classList.toggle("active", Number(b.dataset.w) === v));
      chart.setWindow(v);
    };
    btns.forEach((b) => b.addEventListener("click", () => {
      const v = Number(b.dataset.w);
      apply(v);
      try { localStorage.setItem("mi.window", String(v)); } catch (e) { /* ignora */ }
    }));
    apply([60, 180, 300, 600].includes(w) ? w : 180);
  }

  // ------------------------------------------------- pausa / ao vivo
  $("btn-pause").addEventListener("click", () => chart.toggle());
  $("btn-live").addEventListener("click", () => chart.live());
  chart.onState((st) => {
    S.chartPaused = st.paused;
    $("btn-pause").classList.toggle("paused", st.paused);
    $("btn-pause").title = st.paused ? "Gráfico pausado — clique para voltar ao vivo" : "Pausar o gráfico para analisar as barras — o monitoramento continua (atalho: barra de espaço)";
    $("pause-text").textContent = st.paused ? "Pausado" : "Pausar";
    $("btn-live").classList.toggle("on", !st.paused);
    $("chart-wrap").classList.toggle("is-paused", st.paused);
    const sub = $("chart-subtitle");
    sub.classList.toggle("paused-note", st.paused);
    sub.textContent = st.paused
      ? `Pausado em ${F.time(st.view)}${st.behindMs > 1500 ? ` · ${F.duration(st.behindMs)} atrás do ao vivo` : ""} · use a barra de rolagem ou a roda do mouse para navegar · AO VIVO volta ao tempo atual`
      : S.subtitle || sub.textContent;
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.code !== "Space" || ev.repeat) return;
    const tag = (ev.target && ev.target.tagName) || "";
    if (/INPUT|TEXTAREA|SELECT|BUTTON/.test(tag)) return;
    if (!$("mode-modal").hidden || !$("provider-modal").hidden || !$("sky-modal").hidden) return;
    ev.preventDefault();
    chart.toggle();
  });

  // -------------------------------------------------------------- estado
  async function fetchState() {
    const r = await fetch("/api/state", { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    applySnapshot(await r.json());
  }

  function applySnapshot(snap) {
    const firstLoad = !S.snap;
    S.snap = snap;
    S.settings = snap.settings;
    S.modes = snap.settings.connection_modes;
    S.sessionId = snap.session_id;
    S.sessionStart = snap.mode.session_start;
    S.mode = snap.mode;
    S.net = snap.net;
    S.targets = snap.targets;
    S.stats = snap.stats;
    S.provider = snap.provider;
    S.dish = snap.dish_api || null;
    $("sim-banner").hidden = !snap.simulated;
    applyNames();

    chart.setThresholds(S.settings.lat_normal_max_ms, S.settings.lat_elevated_max_ms, S.settings.interval_ms);
    if (firstLoad) initWindowSelect(S.settings.chart_window_s || 180);
    // Uma falha em uma parte não pode impedir o restante do painel de carregar.
    safely("gráfico", () => { chart.setSamples(snap.samples); chart.setBands(snap.bands); });

    S.failures.clear();
    for (const e of snap.recent_failures) S.failures.set(e.id, e);
    S.open.clear();
    for (const e of snap.open_events) if (e && e.id) S.open.set(e.id, e);

    S.last = snap.samples.length ? snap.samples[snap.samples.length - 1] : null;
    safely("painel", renderAll);
    S.subtitle = `Uma barra por medição (a cada ${F.dec1(S.settings.interval_ms / 1000).replace(",0", "")} s) · altura = latência`;
    if (!S.chartPaused) $("chart-subtitle").textContent = S.subtitle;
    $("foot-version").textContent = `v${snap.version}`;
    $("foot-session").textContent = `Sessão iniciada em ${F.date(S.sessionStart)} ${F.time(S.sessionStart)}`;

    if (!$("provider-modal").hidden || !$("mode-modal").hidden) return;
    let provAsked = false;
    try { provAsked = sessionStorage.getItem("mi.provAsked") === "1"; } catch (e) { /* ignora */ }
    if (!S.provider.configured && !provAsked) openProviderModal(true);   // primeira execução
    else maybeAskMode();
  }

  function maybeAskMode() {
    if (!S.mode || S.mode.confirmed) return;
    let asked = false;
    try { asked = sessionStorage.getItem(`mi.asked.${S.sessionId}`) === "1"; } catch (e) { /* ignora */ }
    if (!asked) openModeModal(true);
  }

  function applyNames() {
    for (const t of S.targets || []) TARGET_NAMES[t.id] = t.name;
    chart.setTargetNames({ ...TARGET_NAMES, dish: S.provider ? S.provider.equip_short : "Provedor" });
  }

  function safely(what, fn) {
    try { fn(); } catch (e) { console.error(`Monitor de Internet: erro ao atualizar ${what}`, e); }
  }

  // ----------------------------------------------------------- WebSocket
  function setLive(state, text) {
    $("live-pill").dataset.state = state;
    $("live-text").textContent = text;
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    S.ws = ws;
    let pingTimer = null;
    ws.onopen = async () => {
      S.wsRetry = 1000;
      setLive("live", "Ao vivo");
      pingTimer = setInterval(() => { try { ws.send("ping"); } catch (e) { /* ignora */ } }, 20000);
      try { await fetchState(); } catch (e) { console.error(e); }
    };
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      switch (msg.type) {
        case "sample": safely("medição", () => onSample(msg)); break;
        case "event": safely("evento", () => onEvent(msg.event)); break;
        case "stats": S.stats = msg.stats; safely("indicadores", renderKpis); break;
        case "mode":
          S.mode = msg.mode;
          safely("modo", renderMode);
          // o modo informado logo no início vale para a sessão toda: recarrega as quedas já listadas
          fetchState().catch(() => {});
          break;
        case "provider": safely("tipo de conexão", () => onProvider(msg)); break;
        case "dish": S.dish = msg.dish; safely("antena", renderDish); break;
      }
    };
    ws.onclose = async () => {
      clearInterval(pingTimer);
      let alive = false;
      try { alive = (await fetch("/api/health", { cache: "no-store" })).ok; } catch (e) { alive = false; }
      if (alive) setLive("connecting", "Reconectando…");
      else setLive("down", "Monitor parado — execute iniciar_monitoramento.bat");
      setTimeout(connect, S.wsRetry);
      S.wsRetry = Math.min(S.wsRetry * 1.6, 8000);
    };
    ws.onerror = () => { try { ws.close(); } catch (e) { /* ignora */ } };
  }

  function onSample(msg) {
    const s = msg.sample;
    S.last = s;
    S.net = msg.net;
    S.dead = msg.dead || [];
    if (S.snap) S.snap.conn_status = s.conn;
    chart.push(s);
    renderHero();
    renderChips();
    renderTargets();
    renderModeWifi();
  }

  function onProvider(msg) {
    S.provider = msg.provider;
    S.targets = msg.targets;
    S.settings = { ...S.settings, ...msg.settings };
    chart.setThresholds(S.settings.lat_normal_max_ms, S.settings.lat_elevated_max_ms, S.settings.interval_ms);
    applyNames();
    renderAll();
  }

  function onEvent(e) {
    if (!e || !e.id) return;
    chart.upsertBand(e);
    if (e.open) S.open.set(e.id, e); else S.open.delete(e.id);
    if (FAILURE_TYPES.includes(e.type)) {
      S.failures.set(e.id, e);
      if (S.failures.size > 200) {
        const oldest = [...S.failures.values()].sort((a, b) => a.start_ts - b.start_ts)[0];
        S.failures.delete(oldest.id);
      }
      renderFailures();
    }
    renderStatus();
  }

  // ------------------------------------------------------------- render
  function renderAll() {
    renderProvider();
    renderHero();
    renderMode();
    renderKpis();
    renderChips();
    renderTargets();
    renderFailures();
    renderDish();
  }

  function renderHero() {
    renderStatus();
    const s = S.last;
    const latEl = $("lat-now");
    const big = latEl.parentElement;
    big.classList.remove("c-ok", "c-warn", "c-bad");
    if (!s) { latEl.textContent = "—"; return; }
    if (s.lat == null) {
      latEl.textContent = "—";
      big.classList.add("c-bad");
      $("lat-sub").textContent = "Sem resposta dos destinos externos";
    } else {
      latEl.textContent = F.ms(s.lat);
      big.classList.add(s.status === 0 ? "c-ok" : s.status === 3 ? "c-bad" : "c-warn");
      const parts = [];
      for (const id of ["cf", "gg"]) {
        const t = s.t[id];
        if (!t || t.m == null) continue;
        parts.push(`${TARGET_NAMES[id]} ${t.m === 0 ? "sem resposta" : F.ms(t.rtt) + " ms"}`);
      }
      if (s.median != null) parts.push(`mediana ${F.ms(s.median)} ms`);
      $("lat-sub").textContent = parts.join(" · ");
    }

    const loss = s.loss60;
    const thr = S.settings.loss_threshold_pct || 2;
    const lossBig = $("loss-now").parentElement;
    lossBig.classList.remove("c-ok", "c-warn", "c-bad");
    $("loss-now").textContent = loss == null ? "—" : F.dec2(loss);
    lossBig.classList.add(loss == null ? "c-ok" : loss > thr ? "c-bad" : loss > 0 ? "c-warn" : "c-ok");
    const scale = thr * 5;
    const fill = $("loss-meter");
    fill.style.width = `${Math.min(100, ((loss || 0) / scale) * 100)}%`;
    fill.style.background = loss > thr ? "var(--bad)" : loss > 0 ? "var(--warn)" : "var(--ok)";
    $("loss-mark").style.left = `${(thr / scale) * 100}%`;
    $("loss-mark").title = `Limite de instabilidade: ${F.dec1(thr)}%`;
  }

  function currentOutage() {
    for (const e of S.open.values()) if (e.type === "interrupcao") return e;
    return null;
  }

  function renderStatus() {
    const card = $("status-card");
    const s = S.last;
    const conn = s ? s.conn : (S.snap ? S.snap.conn_status : "online");
    card.dataset.status = conn;
    $("status-word").textContent = conn === "offline" ? "OFFLINE" : conn === "instavel" ? "INSTÁVEL" : "ONLINE";

    let desc = "Aguardando as primeiras medições…";
    if (s) {
      if (conn === "offline") {
        const o = currentOutage();
        desc = o
          ? `Nenhum destino externo responde desde ${F.time(o.start_ts)} (${F.margin(o.precision_ms)}). ${o.diagnosis_label || ""}.`
          : "Interrupção confirmada: nenhum destino externo responde.";
      } else if (conn === "instavel") {
        const reasons = [];
        if (s.fail_run > 0) reasons.push(`Sem resposta externa há ${s.fail_run} ciclo(s) — verificando`);
        for (const e of S.open.values()) {
          if (e.type === "perda_pacotes" && s.loss60 != null) reasons.push(`Perda de pacotes de ${F.dec1(s.loss60)}% no último minuto`);
          if (e.type === "latencia_elevada" && s.median != null) reasons.push(`Latência elevada: mediana de ${F.ms(s.median)} ms`);
        }
        desc = reasons.join(" · ") || "Oscilação detectada";
      } else {
        const dead = (S.dead || []).map((d) => TARGET_NAMES[d]);
        desc = dead.length
          ? `Conexão normal. ${dead.join(" e ")} sem resposta há mais de 1 minuto (ignorado na perda de pacotes).`
          : "Todos os destinos externos respondendo normalmente.";
      }
    }
    $("status-desc").textContent = desc;
    $("status-foot").textContent = s ? `Última medição ${F.time(s.ts)}` : "";
  }

  function renderMode() {
    const m = S.mode;
    if (!m) return;
    $("mode-icon").innerHTML = ICONS[m.mode] || "";
    $("mode-name").textContent = m.label;
    const st = $("mode-state");
    st.textContent = m.confirmed ? `Informado · período desde ${F.time(m.session_start)}` : "Não confirmado nesta sessão — clique para confirmar";
    st.classList.toggle("unconfirmed", !m.confirmed);
    renderModeWifi();
  }

  function renderModeWifi() {
    const el = $("mode-wifi");
    const warn = $("mode-warn");
    const net = S.net || {};
    const mode = S.mode ? S.mode.mode : "cabo";
    warn.hidden = !net.mismatch;
    warn.textContent = net.mismatch || "";
    if (mode === "cabo") {
      el.textContent = net.if_name ? `Interface: ${net.if_name}` : "";
      return;
    }
    const w = net.wifi;
    if (!w) { el.textContent = "Lendo o sinal Wi-Fi…"; return; }
    if (w.error === "localizacao") {
      el.innerHTML = '<span style="color:var(--warn)">Sinal indisponível: o Windows exige a Localização ativada para informar o Wi-Fi (Configurações › Privacidade e segurança › Localização).</span>';
      return;
    }
    if (!w.available) { el.textContent = "Nenhuma interface Wi-Fi encontrada"; return; }
    if (!w.connected) { el.innerHTML = '<span style="color:var(--bad)">Wi-Fi desconectado</span>'; return; }
    const sig = w.signal;
    const on = sig == null ? 0 : sig >= 80 ? 4 : sig >= 60 ? 3 : sig >= 40 ? 2 : 1;
    const weak = sig != null && sig < (S.settings.wifi_weak_signal_pct || 40);
    const bars = [1, 2, 3, 4].map((i) => `<i class="${i <= on ? "on" : ""}"></i>`).join("");
    el.innerHTML = `<span class="wifi-bars ${weak ? "weak" : ""}">${bars}</span>
      <span>Sinal ${sig == null ? "—" : sig + "%"}${w.rssi != null ? ` (${w.rssi} dBm)` : ""}${w.ssid ? " · " + F.esc(w.ssid) : ""}</span>`;
  }

  function kpi(id, value, unit, cls) {
    const el = $(id);
    el.className = "kpi-value" + (cls ? " " + cls : "");
    el.innerHTML = value == null || value === "—" ? "—" : `${value}${unit ? `<span class="u">${unit}</span>` : ""}`;
  }

  function renderKpis() {
    const st = S.stats;
    if (!st) return;
    kpi("k-lat-avg", st.lat_avg == null ? null : F.ms(st.lat_avg), "ms");
    kpi("k-lat-min", st.lat_min == null ? null : F.ms(st.lat_min), "ms");
    kpi("k-lat-max", st.lat_max == null ? null : F.ms(st.lat_max), "ms", st.lat_max > S.settings.lat_elevated_max_ms ? "c-warn" : "");
    kpi("k-outages", F.int(st.outages_today), "", st.outages_today > 0 ? "c-bad" : "c-ok");
    $("k-micro").textContent = st.micro_today ? `+ ${st.micro_today} microqueda${st.micro_today > 1 ? "s" : ""}` : "nenhuma microqueda";
    renderTicking();

    if (st.availability_pct == null) kpi("k-avail", null);
    else kpi("k-avail", F.dec3(st.availability_pct), "%",
      st.availability_pct >= 99.9 ? "c-ok" : st.availability_pct >= 99 ? "c-warn" : "c-bad");
    $("k-avail-sub").textContent = st.monitored_ms_today ? `de ${F.duration(st.monitored_ms_today)} monitorados` : "";

    const lo = st.last_outage;
    if (lo) {
      kpi("k-last", F.time(lo.start_ts), "");
      $("k-last-sub").textContent = `${F.dayLabel(lo.start_ts)} · ${lo.open ? "em andamento" : F.duration(lo.duration_ms)}`;
    } else {
      kpi("k-last", "nenhuma", "");
      $("k-last-sub").textContent = "desde o início do registro";
    }
    $("foot-db").textContent = `Banco de dados: ${F.bytes(st.db_bytes)}`;
    $("loss-sub").textContent = `Hoje: ${st.loss_today_pct == null ? "—" : F.dec2(st.loss_today_pct) + "%"}`;
  }

  /** Valores que mudam a cada segundo (tempo online, indisponibilidade em curso). */
  function renderTicking() {
    const st = S.stats;
    if (!st) return;
    const now = Date.now();
    const since = {
      ultima_interrupcao: "desde a última interrupção",
      inicio: "desde o início do monitoramento",
      retomada: "desde a retomada do monitoramento",
    };
    const outage = currentOutage();
    $("k-online-label").textContent = "Tempo online";
    if (outage) {
      kpi("k-online", "offline", "", "c-bad");
      $("k-online-sub").textContent = `sem conexão há ${F.duration(now - outage.start_ts)}`;
    } else if (st.online_since) {
      kpi("k-online", F.duration(now - st.online_since), "");
      const when = F.isToday(st.online_since) ? F.hm(st.online_since) : `${F.dateShort(st.online_since)} ${F.hm(st.online_since)}`;
      $("k-online-sub").textContent = `${since[st.online_since_kind] || since.ultima_interrupcao} (${when})`;
      $("k-online-sub").title = $("k-online-sub").textContent;
    }
    let down = st.outage_ms_today || 0;
    if (outage) down += Math.max(0, now - st.now);
    kpi("k-down", down ? F.duration(down) : "0 s", "", down ? "c-bad" : "c-ok");
    $("k-down-sub").textContent = st.micro_ms_today ? `microquedas: ${F.duration(st.micro_ms_today)}` : "sem conectividade externa";
  }

  function chipClass(t) {
    if (!t || t.m == null) return "off";
    if (t.m === 0) return "bad";
    return t.rtt != null && t.rtt > S.settings.lat_normal_max_ms ? "warn" : "ok";
  }

  function renderChips() {
    const s = S.last;
    const html = [];
    for (const tg of S.targets) {
      if (!tg.enabled) continue;
      const t = s && s.t ? s.t[tg.id] : null;
      if (tg.id === "dish" && (!t || t.m == null)) continue;
      const val = !t || t.m == null ? "—" : t.m === 0 ? "falha" : `${F.ms(t.rtt)} ms`;
      const label = tg.id === "dish" ? (S.provider ? S.provider.equip_short : "Provedor") : tg.name;
      html.push(`<span class="chip"><i class="dot ${chipClass(t)}"></i>${F.esc(label)}<b>${val}</b></span>`);
    }
    $("target-chips").innerHTML = html.join("");
  }

  function renderTargets() {
    const s = S.last;
    const net = S.net || {};
    const dishState = {
      detectando: "verificando se o equipamento é acessível…",
      ativa: null,
      indisponivel: "não acessível pela sua rede — nova tentativa periódica, sem alertas",
      desativada: "desativada nas configurações",
    };
    const html = S.targets.map((tg) => {
      const t = s && s.t ? s.t[tg.id] : null;
      let host = tg.host;
      if (tg.id === "gw") host = net.gateway || "não identificado";
      let val = "—", sub = "";
      let cls = chipClass(t);
      if (!tg.enabled && tg.id === "dish") { host = "sem endereço"; sub = "não monitorado — configure em Conexão"; cls = "off"; }
      else if (!tg.enabled) { sub = "desativado"; cls = "off"; }
      else if (tg.id === "dish" && dishState[net.dish]) { sub = dishState[net.dish]; cls = "off"; }
      else if (t && t.m === 0) { val = "sem resposta"; sub = t.err || ""; }
      else if (t && t.m != null) { val = `${F.ms(t.rtt)} ms`; sub = METHOD[t.m]; }
      const dead = S.dead.includes(tg.id) ? " · fora do ar há mais de 1 min" : "";
      return `<div class="target"><i class="dot ${cls}"></i>
        <div><div class="target-name">${F.esc(tg.name)}${tg.external ? "" : ' <span class="hint">(local)</span>'}</div>
        <div class="target-host">${F.esc(host)}${dead}</div></div>
        <div class="target-val">${val}<small>${F.esc(sub)}</small></div></div>`;
    });
    $("targets").innerHTML = html.join("");
    const types = { cabo: "cabo (Ethernet)", wifi: "Wi-Fi", outro: "outro", desconhecido: "não identificado" };
    $("net-info").innerHTML = `
      <div>Interface ativa: <b>${F.esc(net.if_name || "—")}</b> · ${types[net.if_type] || "—"}</div>
      <div>Critério de interrupção: <b>${S.settings.outage_confirm_cycles} ciclos</b> sem resposta de todos os destinos externos</div>`;
  }

  // ------------------------------------------------------------ quedas
  function renderFailures() {
    const list = [...S.failures.values()]
      .filter((e) => S.filter === "all" || e.type === S.filter)
      .sort((a, b) => b.start_ts - a.start_ts);
    const box = $("failures");
    if (!list.length) {
      box.innerHTML = `<div class="empty">${S.filter === "all" ? "Nenhuma queda registrada até agora." : "Nenhum registro deste tipo."}</div>`;
      return;
    }
    box.innerHTML = list.map(failureHtml).join("");
  }

  function failureHtml(e) {
    const isOut = e.type === "interrupcao";
    const live = e.open;
    const dur = live ? Date.now() - e.start_ts : e.duration_ms;
    const open = S.expanded.has(e.id) ? " open" : "";
    const targets = Object.entries(e.targets || {})
      .map(([k, v]) => `${TARGET_NAMES[k] || k}: ${v}`).join("<br>") || "—";
    const errors = Object.entries((e.details && e.details.erros) || {})
      .map(([k, v]) => `${TARGET_NAMES[k] || k}: ${F.esc(v)}`).join(" · ");
    const wifi = e.wifi_signal != null ? ` · sinal ${e.wifi_signal}%` : (e.details && e.details.wifi ? ` · ${e.details.wifi}` : "");
    return `<div class="fail-item${open}" data-id="${e.id}">
      <div class="fail-row">
        <span class="badge ${isOut ? "out" : "micro"}${live ? " live" : ""}">${isOut ? "Interrupção" : "Microqueda"}</span>
        <span class="fail-when">${F.time(e.start_ts)}<small>${F.dayLabel(e.start_ts)}</small></span>
        <span class="fail-dur" data-live="${live ? e.start_ts : ""}">${F.duration(dur)}<small>${F.margin(e.precision_ms)}</small></span>
        <span class="fail-diag">${e.dish ? DV.tag(e).replace('class="dish-tag"', 'class="dish-tag first"') : F.esc(e.diagnosis_label || "—")}<small>${F.esc(e.mode_label || "")}</small></span>
        <span class="chev">›</span>
      </div>
      <div class="fail-detail">
        <dl class="detail-grid">
          <div><dt>Data</dt><dd>${F.date(e.start_ts)}</dd></div>
          <div><dt>Início</dt><dd>${F.time(e.start_ts)} <span class="hint">${F.margin(e.precision_ms)}</span></dd></div>
          <div><dt>Término</dt><dd>${live ? "em andamento" : `${F.time(e.end_ts)} <span class="hint">${F.margin(e.precision_ms)}</span>`}</dd></div>
          <div><dt>Duração</dt><dd>${F.duration(dur)} <span class="hint">${F.margin(e.precision_ms)}</span></dd></div>
          <div><dt>Tipo</dt><dd>${isOut ? "Interrupção de conectividade externa" : "Microqueda (abaixo do critério de interrupção)"}</dd></div>
          <div><dt>Modo de conexão</dt><dd>${F.esc(e.mode_label || "—")}${wifi}</dd></div>
          <div><dt>Roteador</dt><dd>${F.esc(e.router_state || "—")}</dd></div>
          <div><dt>Destinos</dt><dd>${targets}</dd></div>
          <div><dt>Latência antes / depois</dt><dd>${e.lat_before != null ? F.ms(e.lat_before) + " ms" : "—"} / ${e.lat_after != null ? F.ms(e.lat_after) + " ms" : live ? "—" : "aguardando"}</dd></div>
        </dl>
        ${DV.box(e)}
        <div class="diag-box"><b>${F.esc(e.diagnosis_label || "Diagnóstico")}</b>${F.esc(e.diagnosis_text || "")}</div>
        ${errors ? `<div class="obs" style="color:var(--text-3)">Respostas: ${errors}</div>` : ""}
        ${e.details && e.details.observacao ? `<div class="obs">${F.esc(e.details.observacao)}</div>` : ""}
      </div>
    </div>`;
  }

  $("failures").addEventListener("click", (ev) => {
    const row = ev.target.closest(".fail-row");
    if (!row) return;
    const item = row.parentElement;
    const id = Number(item.dataset.id);
    if (S.expanded.has(id)) S.expanded.delete(id); else S.expanded.add(id);
    item.classList.toggle("open");
  });
  $("fail-filter").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    S.filter = b.dataset.f;
    $("fail-filter").querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    renderFailures();
  }));

  // ------------------------------------------------------- antena Starlink
  const DISH_LOGO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 10.5a8 8 0 0 0 9 9z"/><path d="M9 15l4-4"/><path d="M14 4a6 6 0 0 1 6 6M14 7.5a2.5 2.5 0 0 1 2.5 2.5"/><path d="M6 21h6"/></svg>';
  const DISH_MSG = {
    conectando: "Conectando à antena para ler os dados dela…",
    sem_biblioteca: "Para ver os dados da antena, execute o instalar.bat novamente (ele instala o componente de comunicação com a antena). O monitoramento funciona normalmente sem isso.",
  };

  function dishTile(label, value, unit, sub, cls, wide) {
    return `<div class="dish-tile${wide ? " wide" : ""}"><div class="l">${label}</div>
      <div class="v ${cls || ""}">${value}${unit ? `<small>${unit}</small>` : ""}</div>${sub ? `<div class="s">${sub}</div>` : ""}</div>`;
  }

  function mbps(bps) {
    if (bps == null) return "—";
    const v = bps / 1e6;
    return v < 10 ? F.dec2(v) : F.dec1(v);
  }

  function renderDish() {
    const d = S.dish;
    const card = $("dish-card");
    if (!d || d.status === "desativada") { card.hidden = true; return; }
    card.hidden = false;
    $("dish-logo").innerHTML = DISH_LOGO;
    const st = $("dish-state");
    const active = d.status === "ativa";
    const parts = ["Dados informados pela própria antena"];
    if (d.hardware) parts.push(/mini/i.test(d.hardware) ? "Starlink Mini" : d.hardware);
    if (d.software) parts.push(`software ${d.software}`);
    if (d.simulated) parts.push("simulação");
    $("dish-sub").textContent = parts.join(" · ");

    if (!active) {
      st.dataset.s = d.status === "conectando" ? "off" : "warn";
      st.textContent = d.status === "conectando" ? "Conectando…" : d.status === "sem_biblioteca" ? "Componente ausente" : "Sem resposta";
      $("dish-msg").hidden = false;
      $("dish-msg").textContent = DISH_MSG[d.status] || d.message || "";
      $("dish-body").hidden = d.status !== "indisponivel" || !(d.summary_24h && d.summary_24h.count);
      $("btn-sky").disabled = true;
      if (!$("dish-body").hidden) renderDishCauses(d);
      return;
    }
    $("dish-msg").hidden = true;
    $("dish-body").hidden = false;
    $("btn-sky").disabled = false;
    st.dataset.s = d.connected ? "ok" : "bad";
    st.textContent = d.connected ? "Conectada aos satélites" : d.state_label;

    const lat = d.pop_latency_ms;
    const drop = d.drop_rate == null ? null : d.drop_rate * 100;
    const thrN = S.settings.lat_normal_max_ms || 100;
    const realAlerts = (d.alerts || []).filter((a) => !a.info);
    const alerts = (d.alerts || []).length
      ? `<div class="dish-alerts">${d.alerts.map((a) => `<span class="${a.info ? "info" : ""}">${F.esc(a.label)}</span>`).join("")}</div>`
      : "";
    const obst = d.obstructed_now == null ? (d.fraction_obstructed == null ? "não informada" : null) : d.obstructed_now ? "obstruída" : "livre";
    const obstVal = obst != null ? obst : `${F.dec1(d.fraction_obstructed * 100)}%`;
    $("dish-tiles").innerHTML = [
      dishTile("Latência até a Starlink", lat == null ? "—" : F.ms(lat), "ms", "medida pela antena (até o PoP)", lat == null ? "" : lat > thrN ? "c-warn" : "c-ok"),
      dishTile("Perda até a Starlink", drop == null ? "—" : F.dec2(drop), "%", "pings da antena sem resposta", drop == null ? "" : drop > 2 ? "c-bad" : drop > 0.5 ? "c-warn" : "c-ok"),
      dishTile("Obstrução", F.esc(obstVal), "", d.fraction_obstructed != null && obst != null ? `${F.dec1(d.fraction_obstructed * 100)}% do céu bloqueado` : "veja o mapa do céu", obst === "obstruída" ? "c-bad" : ""),
      dishTile("Consumo", d.power_w == null ? "—" : F.dec1(d.power_w), d.power_w == null ? "" : "W", d.power_w == null ? "aguardando 1 min de dados" : "média do último minuto"),
      dishTile("Ligada há", d.uptime_s == null ? "—" : F.duration(d.uptime_s * 1000), "", "sem reiniciar"),
      dishTile("Tráfego agora", `${mbps(d.down_bps)}<small>↓</small> ${mbps(d.up_bps)}<small>↑ Mb/s</small>`, "", "download · upload"),
      dishTile("Alertas", realAlerts.length ? `${realAlerts.length}` : "nenhum", "", alerts || "nenhum problema informado", realAlerts.length ? "c-warn" : "c-ok", true),
    ].join("");
    renderDishCauses(d);
  }

  function renderDishCauses(d) {
    const sm = d.summary_24h;
    $("dish-24-n").textContent = sm && sm.count ? `${sm.count} queda${sm.count > 1 ? "s" : ""} · ${DV.secs(sm.total_ms)}` : "";
    $("dish-causes").innerHTML = DV.causes(sm, { compact: true, empty: "Nenhuma queda registrada pela antena nas últimas 24 horas." });
  }

  async function openSky() {
    $("sky-modal").hidden = false;
    $("sky-msg").hidden = false;
    $("sky-msg").textContent = "Lendo o mapa da antena…";
    $("sky-note").textContent = "";
    const cv = $("sky-canvas");
    cv.getContext("2d").clearRect(0, 0, cv.width, cv.height);
    try {
      const r = await fetch("/api/dish/obstrucao", { cache: "no-store" });
      if (!r.ok) {
        let msg = "Mapa indisponível no momento.";
        try { msg = (await r.json()).detail || msg; } catch (e) { /* ignora */ }
        throw new Error(msg);
      }
      const m = await r.json();
      $("sky-msg").hidden = true;
      const res = DV.drawMap(cv, m);
      $("sky-note").textContent = res.seen
        ? `${F.dec1(res.pct)}% das direções observadas apresentaram bloqueio de sinal · mapa de ${m.rows}×${m.cols} pontos lido às ${F.time(m.fetched_at)}. O mapa vai se completando com o tempo e é zerado quando a antena reinicia.`
        : "A antena ainda não acumulou dados de obstrução (acontece logo depois de reiniciar).";
    } catch (e) {
      $("sky-msg").textContent = e.message;
    }
  }
  $("btn-sky").addEventListener("click", openSky);
  $("sky-close").addEventListener("click", () => { $("sky-modal").hidden = true; });
  $("sky-modal").addEventListener("click", (ev) => { if (ev.target.id === "sky-modal") $("sky-modal").hidden = true; });

  // -------------------------------------------------------- modo (modal)
  function openModeModal(onStart) {
    const modal = $("mode-modal");
    const current = S.mode ? S.mode.mode : "cabo";
    S.modalChoice = current;
    $("mode-options").innerHTML = Object.entries(S.modes).map(([k, label]) => `
      <div class="mode-opt${k === current ? " selected" : ""}" data-mode="${k}" tabindex="0" role="button">
        ${ICONS[k]}<div>${F.esc(label)}</div><small>${MODE_HELP[k] || ""}</small></div>`).join("");
    $("mode-modal-note").textContent = onStart
      ? `Pré-selecionado: ${S.modes[current]} (último modo usado).`
      : "Ao trocar o modo, um novo período é registrado a partir de agora.";
    modal.hidden = false;
    modal.dataset.onstart = onStart ? "1" : "";
  }

  function closeModeModal() {
    $("mode-modal").hidden = true;
    try { sessionStorage.setItem(`mi.asked.${S.sessionId}`, "1"); } catch (e) { /* ignora */ }
  }

  $("mode-options").addEventListener("click", (ev) => {
    const opt = ev.target.closest(".mode-opt");
    if (!opt) return;
    S.modalChoice = opt.dataset.mode;
    $("mode-options").querySelectorAll(".mode-opt").forEach((o) => o.classList.toggle("selected", o === opt));
  });
  $("mode-options").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ev.target.click(); }
  });
  $("mode-cancel").addEventListener("click", closeModeModal);
  $("mode-confirm").addEventListener("click", async () => {
    const btn = $("mode-confirm");
    btn.disabled = true;
    try {
      const r = await fetch("/api/mode", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: S.modalChoice }),
      });
      if (!r.ok) throw new Error(await r.text());
      S.mode = await r.json();
      renderMode();
      closeModeModal();
    } catch (e) {
      $("mode-modal-note").textContent = "Não foi possível salvar o modo. Verifique se o monitoramento está em execução.";
    } finally {
      btn.disabled = false;
    }
  });
  $("mode-card").addEventListener("click", () => openModeModal(false));
  $("mode-card").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openModeModal(false); }
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    if (!$("sky-modal").hidden) $("sky-modal").hidden = true;
    else if (!$("provider-modal").hidden) closeProviderModal();
    else if (!$("mode-modal").hidden) closeModeModal();
  });

  // ------------------------------------------------- tipo de conexão
  const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

  function renderProvider() {
    const p = S.provider;
    if (!p) return;
    const pill = $("provider-pill");
    pill.classList.toggle("unset", !p.configured);
    $("provider-text").textContent = !p.configured ? "Definir tipo" : p.name ? `${p.label} · ${p.name}` : p.label;
  }

  function profileOf(id) {
    return (S.provider.profiles || []).find((x) => x.id === id);
  }

  function updateProviderForm() {
    const prof = profileOf(S.providerChoice);
    const confirm = $("provider-confirm");
    confirm.disabled = !prof;
    if (!prof) {
      $("equip-label").textContent = "Endereço do equipamento do provedor";
      $("equip-help").textContent = "Escolha um tipo de conexão.";
      $("thr-hint").textContent = "";
      return;
    }
    $("equip-label").textContent = `Endereço do ${prof.equip_name.charAt(0).toLowerCase() + prof.equip_name.slice(1)}`;
    if (prof.id === "starlink") $("equip-label").textContent = "Endereço da antena Starlink";
    if (!S.equipDirty) {
      const same = S.provider.type === prof.id;
      $("equip-host").value = same ? S.provider.equip_host || "" : prof.default_host;
    }
    $("equip-help").textContent = prof.default_host
      ? `Padrão para ${prof.label}: ${prof.default_host}. Se ele não responder pela sua rede, o monitor desativa a verificação sozinho, sem alertas. Deixe em branco para não monitorar.`
      : `Sem endereço padrão. Informe o IP ${prof.equip_ref.replace(/^o /, "do ").replace(/^a /, "da ")} se ele for acessível pela sua rede, ou deixe em branco (comum quando o roteador é o próprio equipamento do provedor).`;
    $("thr-hint").textContent = `(normal até ${prof.lat_normal_ms} ms, elevada até ${prof.lat_elevated_ms} ms)`;
    $("equip-host").classList.remove("invalid");
  }

  function openProviderModal(firstRun) {
    const p = S.provider;
    S.providerChoice = p.configured ? p.type : null;
    S.equipDirty = false;
    $("provider-options").innerHTML = p.profiles.map((x) => `
      <div class="provider-opt${x.id === S.providerChoice ? " selected" : ""}" data-p="${x.id}" tabindex="0" role="button">
        <b>${F.esc(x.label)}</b><small>${F.esc(x.hint)}</small></div>`).join("");
    $("provider-name").value = p.name || "";
    $("apply-thresholds").checked = true;
    $("provider-note").textContent = firstRun
      ? "Primeira execução: escolha o tipo da sua conexão para começar."
      : `Limites atuais: normal até ${p.lat_normal_max_ms} ms, elevada até ${p.lat_elevated_max_ms} ms.`;
    $("provider-cancel").textContent = firstRun ? "Agora não" : "Cancelar";
    $("provider-modal").dataset.first = firstRun ? "1" : "";
    updateProviderForm();
    $("provider-modal").hidden = false;
  }

  function closeProviderModal() {
    const first = $("provider-modal").dataset.first === "1";
    $("provider-modal").hidden = true;
    try { sessionStorage.setItem("mi.provAsked", "1"); } catch (e) { /* ignora */ }
    if (first) maybeAskMode();
  }

  $("provider-options").addEventListener("click", (ev) => {
    const opt = ev.target.closest(".provider-opt");
    if (!opt) return;
    if (S.providerChoice !== opt.dataset.p) S.equipDirty = false;
    S.providerChoice = opt.dataset.p;
    $("provider-options").querySelectorAll(".provider-opt").forEach((o) => o.classList.toggle("selected", o === opt));
    updateProviderForm();
  });
  $("provider-options").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ev.target.click(); }
  });
  $("equip-host").addEventListener("input", () => { S.equipDirty = true; $("equip-host").classList.remove("invalid"); });
  $("provider-pill").addEventListener("click", () => { if (S.provider) openProviderModal(false); });
  $("provider-cancel").addEventListener("click", closeProviderModal);
  $("provider-confirm").addEventListener("click", async () => {
    const host = $("equip-host").value.trim();
    if (host && !IPV4.test(host)) {
      $("equip-host").classList.add("invalid");
      $("provider-note").textContent = "Endereço inválido. Use o formato 192.168.100.1 ou deixe em branco.";
      return;
    }
    const btn = $("provider-confirm");
    btn.disabled = true;
    try {
      const r = await fetch("/api/provider", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: S.providerChoice, name: $("provider-name").value.trim(),
                               equip_host: host, apply_thresholds: $("apply-thresholds").checked }),
      });
      if (!r.ok) {
        let msg = "Não foi possível salvar.";
        try { msg = (await r.json()).detail || msg; } catch (e) { /* ignora */ }
        const err = new Error(msg); err.fromServer = true; throw err;
      }
      S.provider = await r.json();
      renderProvider();
      closeProviderModal();
    } catch (e) {
      $("provider-note").textContent = e.fromServer ? e.message
        : "Não foi possível salvar. Verifique se o monitoramento está em execução.";
    } finally {
      btn.disabled = false;
    }
  });

  // ---------------------------------------------------------- relógio
  setInterval(() => {
    $("clock").textContent = F.time(Date.now());
    renderTicking();
    document.querySelectorAll(".fail-dur[data-live]").forEach((el) => {
      const start = Number(el.dataset.live);
      if (start) el.firstChild.textContent = F.duration(Date.now() - start);
    });
  }, 1000);
  $("clock").textContent = F.time(Date.now());

  connect();
})();
