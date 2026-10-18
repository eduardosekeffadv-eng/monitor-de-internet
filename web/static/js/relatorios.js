/* Página Relatórios: escolha do tipo e do período. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const pad = (n) => String(n).padStart(2, "0");
  const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const dmy = (d) => `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`;
  const local = (d) => `${ymd(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
  const WD = ["domingo", "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado"];

  let kind = "diario";
  const today = new Date();
  $("in-day").value = ymd(today);
  $("in-week").value = ymd(today);
  $("in-month").value = `${today.getFullYear()}-${pad(today.getMonth() + 1)}`;
  const dayAgo = new Date(today.getTime() - 86400000);
  $("in-from").value = local(dayAgo);
  $("in-to").value = local(today);

  const QUICK = {
    diario: [["Hoje", 0], ["Ontem", -1], ["Anteontem", -2]],
    semanal: [["Esta semana", 0], ["Semana passada", -7]],
    mensal: [["Este mês", 0], ["Mês passado", -1]],
    personalizado: [["Últimas 24 h", 24], ["Últimos 3 dias", 72], ["Últimos 7 dias", 168], ["Últimos 30 dias", 720]],
  };

  function parseDate(v) { const [y, m, d] = v.split("-").map(Number); return new Date(y, m - 1, d); }

  function params() {
    if (kind === "diario") return { q: `tipo=diario&data=${$("in-day").value}`, ok: !!$("in-day").value };
    if (kind === "semanal") return { q: `tipo=semanal&data=${$("in-week").value}`, ok: !!$("in-week").value };
    if (kind === "mensal") return { q: `tipo=mensal&data=${$("in-month").value}-01`, ok: !!$("in-month").value };
    const a = new Date($("in-from").value).getTime(), b = new Date($("in-to").value).getTime();
    return { q: `tipo=personalizado&inicio=${a}&fim=${b}`, ok: b > a, err: b > a ? "" : "O fim precisa ser depois do início." };
  }

  function describe() {
    if (kind === "diario" && $("in-day").value) {
      const d = parseDate($("in-day").value);
      return `${WD[d.getDay()]}, ${d.getDate()} de ${MONTHS[d.getMonth()]} de ${d.getFullYear()} (00:00 a 23:59)`;
    }
    if (kind === "semanal" && $("in-week").value) {
      const d = parseDate($("in-week").value);
      const mon = new Date(d); mon.setDate(d.getDate() - ((d.getDay() + 6) % 7));
      const sun = new Date(mon); sun.setDate(mon.getDate() + 6);
      return `de segunda ${dmy(mon)} a domingo ${dmy(sun)}`;
    }
    if (kind === "mensal" && $("in-month").value) {
      const [y, m] = $("in-month").value.split("-").map(Number);
      return `${MONTHS[m - 1]} de ${y}`;
    }
    if (kind === "personalizado") {
      const a = new Date($("in-from").value), b = new Date($("in-to").value);
      if (isNaN(a) || isNaN(b)) return "—";
      return `de ${dmy(a)} ${pad(a.getHours())}:${pad(a.getMinutes())} a ${dmy(b)} ${pad(b.getHours())}:${pad(b.getMinutes())}`;
    }
    return "—";
  }

  function update() {
    const p = params();
    $("preview").textContent = `Período: ${describe()}`;
    $("open").href = p.ok ? `/relatorio?${p.q}` : "#";
    $("download").href = p.ok ? `/relatorio/baixar?${p.q}` : "#";
    $("open").toggleAttribute("aria-disabled", !p.ok);
    $("err").textContent = p.err || "";
  }

  function setKind(k) {
    kind = k;
    document.querySelectorAll(".rep-card").forEach((c) => c.classList.toggle("selected", c.dataset.k === k));
    $("f-day").hidden = k !== "diario";
    $("f-week").hidden = k !== "semanal";
    $("f-month").hidden = k !== "mensal";
    $("f-from").hidden = $("f-to").hidden = k !== "personalizado";
    $("quick").innerHTML = QUICK[k].map(([l, v]) => `<button class="btn small" data-v="${v}" type="button">${l}</button>`).join("");
    update();
  }

  $("kinds").addEventListener("click", (ev) => { const c = ev.target.closest(".rep-card"); if (c) setKind(c.dataset.k); });
  $("kinds").addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ev.target.click(); } });
  $("quick").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-v]");
    if (!b) return;
    const v = Number(b.dataset.v), now = new Date();
    if (kind === "diario") { const d = new Date(now); d.setDate(d.getDate() + v); $("in-day").value = ymd(d); }
    if (kind === "semanal") { const d = new Date(now); d.setDate(d.getDate() + v); $("in-week").value = ymd(d); }
    if (kind === "mensal") { const d = new Date(now.getFullYear(), now.getMonth() + v, 1); $("in-month").value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}`; }
    if (kind === "personalizado") { $("in-to").value = local(now); $("in-from").value = local(new Date(now.getTime() - v * 3600000)); }
    update();
  });
  ["in-day", "in-week", "in-month", "in-from", "in-to"].forEach((id) => $(id).addEventListener("input", update));
  $("open").addEventListener("click", (ev) => { if (!params().ok) ev.preventDefault(); });
  $("download").addEventListener("click", (ev) => { if (!params().ok) ev.preventDefault(); });

  fetch("/api/health").then((r) => r.json()).then((h) => { $("sim-banner").hidden = !h.simulated; }).catch(() => {});
  fetch("/api/settings").then((r) => r.json()).then((s) => {
    if (s.db && s.db.first_ts) {
      const d = new Date(s.db.first_ts);
      $("data-since").textContent = `Os relatórios usam todo o histórico gravado neste computador, disponível desde ${dmy(d)} às ${pad(d.getHours())}:${pad(d.getMinutes())}.`;
    }
  }).catch(() => {});
  setKind("diario");
})();
