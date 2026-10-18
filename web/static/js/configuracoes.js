/* Página Configurações. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

  // Campos numéricos: chave, rótulo, unidade, mín., máx., passo, ajuda, escala (exibição = valor / escala)
  const SECTIONS = [
    { title: "Medição", lead: "Com que frequência e por quanto tempo cada destino é verificado.", fields: [
      ["interval_ms", "Intervalo entre medições", "s", 0.5, 60, 0.5, "Padrão: 1 s. Intervalos maiores reduzem a precisão dos horários das quedas.", 1000],
      ["timeout_ms", "Tempo limite por verificação", "ms", 100, 5000, 50, "Padrão: 800 ms. Precisa ser menor que o intervalo."],
    ] },
    { title: "Critérios de qualidade", lead: "Quando a conexão é considerada instável ou fora do ar.", fields: [
      ["lat_normal_max_ms", "Latência normal até", "ms", 5, 5000, 1, "Acima disso, a latência é considerada elevada."],
      ["lat_elevated_max_ms", "Latência elevada até", "ms", 10, 10000, 1, "Acima disso, crítica."],
      ["loss_threshold_pct", "Limite de perda de pacotes", "%", 0, 100, 0.1, "Perda acima deste valor na janela abaixo deixa a conexão INSTÁVEL."],
      ["loss_window_s", "Janela da perda de pacotes", "s", 10, 3600, 1, "Padrão: 60 s (janela móvel)."],
      ["outage_confirm_cycles", "Ciclos para confirmar interrupção", "ciclos", 1, 60, 1, "Ciclos seguidos sem resposta de todos os destinos externos. Menos que isso é microqueda."],
      ["wifi_weak_signal_pct", "Sinal Wi-Fi fraco abaixo de", "%", 1, 100, 1, "Usado no diagnóstico das falhas em modo Wi-Fi."],
    ] },
    { title: "Retenção dos dados", lead: "Dados mais antigos são apagados automaticamente. Quedas e demais eventos são mantidos para sempre.", fields: [
      ["retention_raw_days", "Medições individuais (1 por segundo)", "dias", 1, 3650, 1, "Padrão: 30 dias (cerca de 4 MB por dia)."],
      ["retention_agg_days", "Médias por minuto", "dias", 7, 3650, 1, "Padrão: 365 dias. Usadas nos gráficos e relatórios de períodos longos."],
    ] },
  ];

  let info = null;      // resposta de /api/settings
  let original = null;  // valores do formulário ao carregar

  function numField([key, label, unit, min, max, step, help, scale]) {
    return `<label class="fld" data-k="${key}"><span>${label}</span>
      <div class="unit"><input type="number" id="f-${key}" min="${min}" max="${max}" step="${step}" data-scale="${scale || 1}"><span>${unit}</span></div>
      <small>${help}</small></label>`;
  }

  function build() {
    const st = info.settings, prov = info.provider;
    const target = (id) => st.targets.find((t) => t.id === id);
    let h = "";

    // tipo de conexão
    h += `<section class="card form-section"><h2>Tipo de conexão</h2>
      <p class="lead">Define o equipamento do provedor monitorado e os textos do diagnóstico.</p>
      <div class="fields">
        <label class="fld"><span>Tipo de conexão</span><select id="p-type">${prov.profiles.map((p) => `<option value="${p.id}">${esc(p.label)}</option>`).join("")}</select>
          <small id="p-hint"></small></label>
        <label class="fld"><span>Nome do provedor ou plano (opcional)</span><input type="text" id="p-name" maxlength="80" placeholder="Ex.: Vivo Fibra 500 MB"></label>
        <label class="fld"><span id="p-equip-label">Endereço do equipamento do provedor</span><input type="text" id="p-host" maxlength="15" placeholder="em branco = não monitorar"><small id="p-equip-help"></small></label>
      </div>
      <label class="toggle" style="margin-top:12px"><input type="checkbox" id="p-thr"><span><b>Aplicar os limites de latência recomendados ao trocar o tipo</b>
        <small id="p-thr-hint">Preenche os campos "Latência normal até" e "Latência elevada até" abaixo.</small></span></label>
      <label class="toggle" id="dish-api-row" style="margin-top:10px"><input type="checkbox" id="b-dish_api_enabled"><span><b>Ler os dados da própria antena Starlink</b>
        <small>Motivo de cada queda (troca de satélite, obstrução, falta de cobertura…), alertas, mapa de obstruções e consumo, lidos da interface local da antena — somente leitura, nada é alterado nela. Funciona pelo Wi-Fi ou cabo da Starlink e, na maioria dos casos, por roteador próprio.</small></span></label>
    </section>`;

    // destinos
    h += `<section class="card form-section"><h2>Destinos monitorados</h2>
      <p class="lead">A conexão só é considerada fora do ar quando <b>todos</b> os destinos externos ativos param de responder.</p>
      <div class="fields">
        <label class="fld"><span>Roteador (rede local)</span><input type="text" id="t-gw" placeholder="auto">
          <small>"auto" usa o gateway padrão detectado pelo Windows${info.provider && info.provider.equip_host ? "" : ""}.</small></label>
        <label class="fld"><span>Destino externo 1 (Cloudflare)</span><input type="text" id="t-cf"><small><label><input type="checkbox" id="t-cf-on"> monitorar</label></small></label>
        <label class="fld"><span>Destino externo 2 (Google)</span><input type="text" id="t-gg"><small><label><input type="checkbox" id="t-gg-on"> monitorar</label></small></label>
      </div></section>`;

    for (const sec of SECTIONS) {
      h += `<section class="card form-section"><h2>${sec.title}</h2><p class="lead">${sec.lead}</p><div class="fields">${sec.fields.map(numField).join("")}</div></section>`;
    }

    // sistema
    h += `<section class="card form-section"><h2>Sistema</h2><p class="lead">Comportamento no Windows.</p>
      <div class="fields">
        <label class="toggle"><input type="checkbox" id="b-prevent_sleep"><span><b>Impedir que o Windows entre em suspensão</b>
          <small>Somente a suspensão automática por inatividade. Fechar a tampa ou suspender manualmente continua funcionando.</small></span></label>
        <label class="toggle"><input type="checkbox" id="b-autostart" ${info.autostart_supported ? "" : "disabled"}><span><b>Iniciar o monitoramento com o Windows</b>
          <small>${info.autostart_supported ? "Inicia em segundo plano ao entrar no Windows, sem abrir o Chrome. Não exige administrador." : "Disponível apenas no Windows."}</small></span></label>
        <label class="fld" data-k="port"><span>Porta do painel</span><div class="unit"><input type="number" id="f-port" min="1024" max="65535" step="1" data-scale="1"><span></span></div>
          <small>Padrão: 8000. Mudar a porta exige reiniciar o monitor (parar e iniciar).</small></label>
        <label class="fld"><span>Janela padrão do gráfico em tempo real</span><select id="s-chart">
          <option value="60">1 minuto</option><option value="180">3 minutos</option><option value="300">5 minutos</option><option value="600">10 minutos</option></select>
          <small>A janela escolhida no painel fica salva no navegador.</small></label>
      </div></section>`;

    // dados
    const db = info.db;
    h += `<section class="card form-section"><h2>Dados</h2>
      <p class="lead">Banco de dados: <code>${esc(db.path)}</code> · ${(db.bytes / 1048576).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MB
      ${db.first_ts ? ` · histórico desde ${new Date(db.first_ts).toLocaleString("pt-BR")}` : ""}</p>
      <p class="lead" style="margin:0">Para fazer uma cópia de segurança, pare o monitor e copie a pasta <code>data</code>.</p></section>`;

    $("sections").innerHTML = h;
    $("p-type").addEventListener("change", onTypeChange);
    $("p-host").addEventListener("input", () => { $("p-host").dataset.dirty = "1"; });
    $("form").addEventListener("input", markChanged);
    $("form").addEventListener("change", markChanged);
  }

  function profile(id) { return info.provider.profiles.find((p) => p.id === id); }

  function updateProviderHelp() {
    updateDishRow();
    const p = profile($("p-type").value);
    if (!p) return;
    $("p-hint").textContent = p.hint;
    $("p-equip-label").textContent = `Endereço do equipamento (${p.equip_name})`;
    $("p-equip-help").textContent = p.default_host ? `Padrão: ${p.default_host}. Em branco = não monitorar.` : "Sem endereço padrão. Em branco = não monitorar.";
    $("p-thr-hint").textContent = `Recomendado para ${p.label}: normal até ${p.lat_normal_ms} ms, elevada até ${p.lat_elevated_ms} ms.`;
  }

  function onTypeChange() {
    const p = profile($("p-type").value);
    if (!$("p-host").dataset.dirty) $("p-host").value = p.id === info.provider.type ? (info.provider.equip_host || "") : p.default_host;
    if ($("p-thr").checked) {
      setNum("lat_normal_max_ms", p.lat_normal_ms);
      setNum("lat_elevated_max_ms", p.lat_elevated_ms);
    }
    updateProviderHelp();
    markChanged();
  }
  function updateDishRow() {
    const row = $("dish-api-row");
    if (row) row.hidden = $("p-type").value !== "starlink";
  }

  function setNum(key, val) {
    const el = $(`f-${key}`);
    if (!el) return;
    const sc = Number(el.dataset.scale || 1);
    el.value = String(Math.round((val / sc) * 1000) / 1000);
  }
  function getNum(key) {
    const el = $(`f-${key}`);
    const sc = Number(el.dataset.scale || 1);
    return Number(el.value) * sc;
  }

  function fill(st, prov) {
    $("p-type").value = prov.type || "outro";
    $("p-name").value = prov.name || "";
    $("p-host").value = prov.equip_host || "";
    delete $("p-host").dataset.dirty;
    $("p-thr").checked = true;
    const t = (id) => st.targets.find((x) => x.id === id);
    $("t-gw").value = t("gw").host;
    $("t-cf").value = t("cf").host; $("t-cf-on").checked = t("cf").enabled;
    $("t-gg").value = t("gg").host; $("t-gg-on").checked = t("gg").enabled;
    for (const sec of SECTIONS) for (const [k] of sec.fields) setNum(k, st[k]);
    setNum("port", st.port);
    $("b-prevent_sleep").checked = !!st.prevent_sleep;
    $("b-dish_api_enabled").checked = st.dish_api_enabled !== false;
    $("b-autostart").checked = !!st.autostart;
    $("s-chart").value = String(st.chart_window_s);
    updateProviderHelp();
    original = snapshot();
    markChanged();
  }

  function snapshot() {
    const o = {};
    document.querySelectorAll("#form input, #form select").forEach((el) => { o[el.id] = el.type === "checkbox" ? el.checked : el.value; });
    return o;
  }

  function markChanged() {
    if (!original) return;
    const now = snapshot();
    let n = 0;
    document.querySelectorAll("#form input, #form select").forEach((el) => {
      const ch = now[el.id] !== original[el.id];
      if (ch && el.id !== "p-thr") n++;
      const fld = el.closest(".fld");
      if (fld) fld.classList.toggle("changed", ch);
    });
    $("save").disabled = n === 0;
    $("discard").disabled = n === 0;
    if (n) setMsg(`${n} alteração(ões) não salva(s).`, "warn");
  }

  function setMsg(text, cls) {
    const m = $("msg");
    m.textContent = text;
    m.className = `msg ${cls || ""}`;
  }

  function validate() {
    const errs = [];
    document.querySelectorAll("#form input[type=number]").forEach((el) => {
      const v = Number(el.value);
      if (el.value === "" || isNaN(v) || v < Number(el.min) || v > Number(el.max)) {
        errs.push(`${el.closest(".fld").querySelector("span").textContent}: entre ${el.min} e ${el.max}`);
      }
    });
    if (getNum("timeout_ms") >= getNum("interval_ms")) errs.push("O tempo limite precisa ser menor que o intervalo entre medições.");
    if (getNum("lat_elevated_max_ms") <= getNum("lat_normal_max_ms")) errs.push("A latência elevada precisa ser maior que a normal.");
    const host = $("p-host").value.trim();
    if (host && !IPV4.test(host)) errs.push("Endereço do equipamento do provedor: use um IPv4, ex.: 192.168.100.1.");
    for (const id of ["t-cf", "t-gg"]) if (!IPV4.test($(id).value.trim())) errs.push("Os destinos externos precisam de um endereço IPv4 (ex.: 1.1.1.1).");
    if ($("t-gw").value.trim() !== "auto" && !IPV4.test($("t-gw").value.trim())) errs.push('Roteador: use "auto" ou um IPv4.');
    if (!$("t-cf-on").checked && !$("t-gg-on").checked) errs.push("Mantenha pelo menos um destino externo ativo.");
    return errs;
  }

  async function save(ev) {
    ev.preventDefault();
    const errs = validate();
    if (errs.length) { setMsg(errs.join(" "), "err"); return; }
    $("save").disabled = true;
    setMsg("Salvando…");
    try {
      const prov = info.provider;
      const pType = $("p-type").value, pName = $("p-name").value.trim(), pHost = $("p-host").value.trim();
      if (pType !== prov.type || pName !== (prov.name || "") || pHost !== (prov.equip_host || "")) {
        const r = await fetch("/api/provider", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ type: pType, name: pName, equip_host: pHost, apply_thresholds: false }) });
        if (!r.ok) throw new Error((await r.json()).detail || "Erro ao salvar o tipo de conexão.");
      }
      const body = {
        targets: [
          { id: "gw", host: $("t-gw").value.trim() || "auto" },
          { id: "cf", host: $("t-cf").value.trim(), enabled: $("t-cf-on").checked },
          { id: "gg", host: $("t-gg").value.trim(), enabled: $("t-gg-on").checked },
        ],
        prevent_sleep: $("b-prevent_sleep").checked,
        dish_api_enabled: $("b-dish_api_enabled").checked,
        autostart: $("b-autostart").checked,
        chart_window_s: Number($("s-chart").value),
        port: getNum("port"),
      };
      for (const sec of SECTIONS) for (const [k] of sec.fields) body[k] = getNum(k);
      const r = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error((await r.json()).detail || `HTTP ${r.status}`);
      const res = await r.json();
      info = res;
      fill(res.settings, res.provider);
      const notes = [...(res.warnings || [])];
      if (res.restart_needed) notes.push(`A nova porta (${res.settings.port}) vale depois de reiniciar o monitor.`);
      setMsg(notes.length ? `Salvo. ${notes.join(" ")}` : "Alterações salvas e já em uso.", notes.length ? "warn" : "ok");
    } catch (e) {
      setMsg(`Não foi possível salvar: ${e.message}`, "err");
      $("save").disabled = false;
    }
  }

  async function load() {
    const r = await fetch("/api/settings", { cache: "no-store" });
    info = await r.json();
    $("sim-banner").hidden = !info.simulated;
    build();
    fill(info.settings, info.provider);
    setMsg("As alterações valem imediatamente, sem reiniciar (exceto a porta).");
  }

  $("form").addEventListener("submit", save);
  $("discard").addEventListener("click", () => { fill(info.settings, info.provider); setMsg("Alterações descartadas."); });
  $("defaults").addEventListener("click", () => {
    const d = info.defaults;
    for (const sec of SECTIONS) for (const [k] of sec.fields) setNum(k, d[k]);
    const p = profile($("p-type").value);
    if (p) { setNum("lat_normal_max_ms", p.lat_normal_ms); setNum("lat_elevated_max_ms", p.lat_elevated_ms); }
    $("b-prevent_sleep").checked = d.prevent_sleep;
    $("s-chart").value = String(d.chart_window_s);
    markChanged();
    setMsg("Valores padrão preenchidos (limites de latência conforme o tipo de conexão). Clique em Salvar para aplicar.", "warn");
  });
  window.addEventListener("beforeunload", (ev) => { if (!$("save").disabled) { ev.preventDefault(); ev.returnValue = ""; } });
  load().catch((e) => setMsg(`Erro ao carregar: ${e.message}`, "err"));
})();
