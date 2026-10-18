/* Comum a todas as páginas: navegação, zoom da interface, relógio e status do monitor. */
(function () {
  "use strict";
  const ZOOMS = [0.75, 0.8, 0.9, 1, 1.1, 1.25];
  const KEY = "mi.zoom";

  function getZoom() {
    let z = 1;
    try { z = parseFloat(localStorage.getItem(KEY) || "1") || 1; } catch (e) { /* sem armazenamento */ }
    return ZOOMS.includes(z) ? z : 1;
  }
  function applyZoom(z) {
    // O zoom vale só para os blocos do próprio painel (ver app.css), nunca para a
    // página inteira: painéis que extensões do Chrome inserem na página ficam intactos.
    document.documentElement.style.zoom = "";
    document.documentElement.style.setProperty("--ui-zoom", String(z));
    try { localStorage.setItem(KEY, String(z)); } catch (e) { /* ignora */ }
    window.dispatchEvent(new Event("resize"));   // gráficos recalculam o tamanho
  }
  applyZoom(getZoom());

  function buildZoom(el) {
    const label = (z) => `${Math.round(z * 100)}%`;
    el.innerHTML = `<button class="zoom-btn" aria-haspopup="listbox" aria-expanded="false" title="Tamanho da interface">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4M8 11h6M11 8v6"/></svg>
        <span class="zoom-val">${label(getZoom())}</span>
        <svg class="chev-d" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="m6 9 6 6 6-6"/></svg>
      </button>
      <div class="zoom-menu" role="listbox" hidden>
        <div class="zoom-title">Tamanho da interface</div>
        ${ZOOMS.map((z) => `<button role="option" data-z="${z}">${label(z)}${z === 1 ? " <small>padrão</small>" : ""}</button>`).join("")}
      </div>`;
    const btn = el.querySelector(".zoom-btn");
    const menu = el.querySelector(".zoom-menu");
    const mark = () => menu.querySelectorAll("[data-z]").forEach((b) => b.classList.toggle("sel", Number(b.dataset.z) === getZoom()));
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      menu.hidden = !menu.hidden;
      btn.setAttribute("aria-expanded", String(!menu.hidden));
      mark();
    });
    menu.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-z]");
      if (!b) return;
      const z = Number(b.dataset.z);
      applyZoom(z);
      el.querySelector(".zoom-val").textContent = label(z);
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
    });
    document.addEventListener("click", () => { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); });
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") menu.hidden = true; });
  }

  function markNav() {
    const path = location.pathname.replace(/\/+$/, "") || "/";
    document.querySelectorAll(".nav a").forEach((a) => {
      const href = a.getAttribute("href");
      a.classList.toggle("active", href === "/" ? path === "/" : path.startsWith(href));
    });
  }

  // Páginas sem WebSocket: o status do monitor vem de uma consulta periódica.
  async function pollHealth(pill, text) {
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      const h = await r.json();
      pill.dataset.state = h.running ? "live" : "down";
      text.textContent = h.running ? (h.simulated ? "Monitorando (simulação)" : "Monitorando") : "Monitor parado";
    } catch (e) {
      pill.dataset.state = "down";
      text.textContent = "Monitor parado — execute iniciar_monitoramento.bat";
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const z = document.getElementById("zoom");
    if (z) buildZoom(z);
    markNav();
    const clock = document.getElementById("clock");
    if (clock) {
      const tick = () => { clock.textContent = new Date().toLocaleTimeString("pt-BR", { hour12: false }); };
      tick(); setInterval(tick, 1000);
    }
    const pill = document.getElementById("live-pill");
    if (pill && pill.dataset.poll === "1") {
      const text = document.getElementById("live-text");
      pollHealth(pill, text); setInterval(() => pollHealth(pill, text), 10000);
    }
  });

  window.MI = { getZoom, applyZoom };
})();
