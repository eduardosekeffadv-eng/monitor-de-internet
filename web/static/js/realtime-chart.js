/* Gráfico de barras em tempo real (estilo visualizador de áudio), com Chart.js local.
 *
 * - Uma barra por medição; o eixo X é o horário e rola continuamente.
 * - Altura = latência. Falha nunca vira 0 ms: é desenhada como barra
 *   vermelha de altura cheia com hachura.
 * - Interrupções, microquedas e períodos sem monitoramento aparecem como
 *   faixas de fundo.
 * - A dica (tooltip) segue a barra sob o mouse mesmo com o gráfico rolando.
 */
(function () {
  "use strict";

  const COLORS = {
    ok: ["#34d399", "#0f5c45"],
    warn: ["#fbbf24", "#6b4e0a"],
    crit: ["#fb923c", "#7a3510"],
    partial: ["#fb7185", "#6d1a2a"],
    bad: "#f43f5e",
    grid: "rgba(148, 163, 184, 0.07)",
    tick: "#6b7d8f",
  };
  const STATUS_LABEL = ["Normal", "Latência elevada", "Latência crítica", "Perda parcial", "Sem resposta externa"];
  const STATUS_COLOR = ["#34d399", "#fbbf24", "#fb923c", "#fb7185", "#f43f5e"];
  const METHOD = { 0: "sem resposta", 1: "ICMP", 2: "TCP" };
  const KEEP_MS = 30 * 60 * 1000;   // memória do gráfico (permite pausar e voltar no tempo)
  const GROW_MS = 380;

  function niceCeil(v) {
    const steps = [50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 3000, 5000];
    for (const s of steps) if (v <= s) return s;
    return Math.ceil(v / 1000) * 1000;
  }
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);

  function makeHatch(ctx) {
    const c = document.createElement("canvas");
    c.width = c.height = 8;
    const g = c.getContext("2d");
    g.fillStyle = "rgba(244, 63, 94, 0.30)";
    g.fillRect(0, 0, 8, 8);
    g.strokeStyle = "rgba(244, 63, 94, 0.95)";
    g.lineWidth = 2;
    g.beginPath();
    g.moveTo(-2, 10); g.lineTo(10, -2);
    g.moveTo(-2, 2); g.lineTo(2, -2);
    g.moveTo(6, 10); g.lineTo(10, 6);
    g.stroke();
    return ctx.createPattern(c, "repeat");
  }

  class RealtimeBars {
    constructor(canvas, opts) {
      this.canvas = canvas;
      this.wrap = canvas.parentElement;
      this.tooltip = opts.tooltip;
      this.scrollEl = opts.scrollEl || null;   // barra de rolagem horizontal (opcional)
      this.emptyEl = opts.emptyEl;
      this.intervalMs = opts.intervalMs || 1000;
      this.normalMs = opts.normalMs || 100;
      this.elevatedMs = opts.elevatedMs || 250;
      this.windowMs = (opts.windowS || 180) * 1000;
      this.points = [];
      this.names = { gw: "Roteador", cf: "Cloudflare", gg: "Google", dish: "Provedor" };
      this.bands = new Map();
      this.view = null;          // horário "atual" exibido (ms)
      this.lastTs = null;
      this.lastArrive = 0;
      this.lastFrame = 0;
      this.yMax = 150;
      this.mouse = null;
      this.hovered = null;
      this.hoverKey = null;
      this._gradCache = null;
      this._build();
      this._bindHover();
      this._bindScroll();
      this._frame = this._frame.bind(this);
      requestAnimationFrame(this._frame);
    }

    // ------------------------------------------------------------ API
    setThresholds(normal, elevated, interval) {
      this.normalMs = normal; this.elevatedMs = elevated;
      if (interval) this.intervalMs = interval;
    }
    setWindow(seconds) { this.windowMs = seconds * 1000; if (this.paused) this._clampView(); }

    // ----- pausa / ao vivo (como numa transmissão ao vivo)
    pause() {
      if (this.paused || this.view == null) return;
      this.paused = true;
      this.pausedAt = this.view;
      this._emit();
    }
    live() {
      this.paused = false;
      this.pausedAt = null;
      if (this.lastTs != null) this.view = this.lastTs + Math.min(performance.now() - this.lastArrive, this.intervalMs);
      this._emit();
    }
    toggle() { if (this.paused) this.live(); else this.pause(); }
    scrollBy(ms) {
      if (this.view == null) return;
      if (!this.paused) this.pause();
      this.view += ms;
      this._clampView();
      if (this.lastTs != null && this.view >= this.lastTs) { this.live(); return; }
      this._emit();
    }
    /** Posiciona o fim da janela visível em `ts` (pausa, se necessário). */
    scrollTo(ts) {
      if (this.view == null) return;
      if (!this.paused) this.pause();
      this.view = ts;
      this._clampView();
      this._emit();
    }

    // ----- barra de rolagem própria (funciona igual em qualquer Chrome/configuração do Windows)
    _bindScroll() {
      const el = this.scrollEl;
      if (!el) return;
      el.innerHTML = '<div class="rt-thumb"></div>';
      this._thumb = el.firstChild;
      const fracAt = (clientX, grabOffset) => {
        const r = el.getBoundingClientRect();
        const k = r.width ? el.clientWidth / r.width : 1;            // zoom da interface
        const x = (clientX - r.left) * k - grabOffset;
        const free = el.clientWidth - this._thumb.offsetWidth;
        return free > 0 ? Math.max(0, Math.min(1, x / free)) : 1;
      };
      const go = (frac) => {
        const rg = this._scrollRange();
        if (!rg || rg.span <= 0) return;
        if (frac >= 0.999) { if (this.paused) this.live(); return; }
        this.scrollTo(rg.min + frac * rg.span);
      };
      el.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        const r = this._thumb.getBoundingClientRect();
        const k = r.width ? this._thumb.offsetWidth / r.width : 1;
        const onThumb = e.target === this._thumb;
        // clicou no trilho: o centro do marcador vai até o ponto clicado
        const grab = onThumb ? (e.clientX - r.left) * k : this._thumb.offsetWidth / 2;
        this._dragging = true;
        el.classList.add("dragging");
        el.setPointerCapture(e.pointerId);
        go(fracAt(e.clientX, grab));
        const move = (ev) => go(fracAt(ev.clientX, grab));
        const up = () => {
          this._dragging = false;
          el.classList.remove("dragging");
          el.removeEventListener("pointermove", move);
          el.removeEventListener("pointerup", up);
          el.removeEventListener("pointercancel", up);
        };
        el.addEventListener("pointermove", move);
        el.addEventListener("pointerup", up);
        el.addEventListener("pointercancel", up);
      });
      el.addEventListener("wheel", (e) => {
        e.preventDefault();
        const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
        this.scrollBy((d > 0 ? 1 : -1) * this.windowMs * 0.12);
      }, { passive: false });
    }
    _scrollRange() {
      if (!this.points.length || this.lastTs == null) return null;
      const oldest = this.points[0].x;
      const min = Math.min(oldest + this.windowMs * 0.9, this.lastTs);
      return { min, max: this.lastTs, span: this.lastTs - min, total: this.lastTs - oldest };
    }
    _syncScroll() {
      const el = this.scrollEl;
      if (!el || !this._thumb) return;
      const area = this.chart.chartArea;
      if (area) {
        // alinha a barra com a área do gráfico (sem os eixos)
        const ml = `${Math.round(area.left)}px`, w = `${Math.round(area.width)}px`;
        if (el.style.marginLeft !== ml) el.style.marginLeft = ml;
        if (el.style.width !== w) el.style.width = w;
      }
      const r = this._scrollRange();
      const tw = el.clientWidth;
      if (!r || !tw) return;
      const ratio = Math.min(1, this.windowMs / Math.max(this.windowMs, r.total + this.intervalMs));
      const thumbW = Math.max(28, Math.round(tw * ratio));
      const frac = this.paused && r.span > 0 ? Math.max(0, Math.min(1, (this.view - r.min) / r.span)) : 1;
      const left = Math.round(frac * (tw - thumbW));
      const st = this._thumb.style;
      const W = `${thumbW}px`, L = `${left}px`;
      if (st.width !== W) st.width = W;
      if (st.transform !== `translateX(${L})`) st.transform = `translateX(${L})`;
      el.classList.toggle("has-more", ratio < 0.99);
      el.classList.toggle("paused", !!this.paused);
    }

    _clampView() {
      if (!this.points.length) return;
      const oldest = this.points[0].x + this.windowMs * 0.9;
      const newest = this.lastTs;
      if (this.view < oldest) this.view = Math.min(oldest, newest);
      if (this.view > newest) this.view = newest;
    }
    onState(fn) { this._listeners = this._listeners || []; this._listeners.push(fn); }
    _emit() {
      const st = { paused: !!this.paused, view: this.view, behindMs: this.paused && this.lastTs ? Math.max(0, this.lastTs - this.view) : 0 };
      (this._listeners || []).forEach((fn) => fn(st));
    }
    setTargetNames(names) { Object.assign(this.names, names); this.hoverKey = null; }

    setSamples(samples) {
      // Substitui o conjunto inteiro de uma vez (nunca esvaziando o array que o
      // Chart.js observa), preservando barras que chegaram ao vivo depois do
      // instantâneo — elas podem chegar antes de a carga inicial terminar.
      const pts = samples.map((s) => ({ x: s.ts, y: 0, s, born: 0 }));
      const lastSnap = pts.length ? pts[pts.length - 1].x : -Infinity;
      for (const p of this.points) if (p.x > lastSnap) pts.push(p);
      this.points = pts;
      this.chart.data.datasets[0].data = pts;
      this.chart.update("none");
      if (pts.length) {
        const newest = pts[pts.length - 1].x;
        if (this.lastTs == null || newest > this.lastTs) {
          this.lastTs = newest;
          this.lastArrive = performance.now();
        }
        if (this.view == null) this.view = this.lastTs;
      }
      this._updateEmpty();
    }

    push(sample) {
      const last = this.points[this.points.length - 1];
      if (last && sample.ts <= last.x) return;
      this.points.push({ x: sample.ts, y: 0, s: sample, born: performance.now() });
      this.lastTs = sample.ts;
      this.lastArrive = performance.now();
      if (this.view == null) this.view = sample.ts - this.intervalMs;
      this._updateEmpty();
    }

    setBands(events) {
      this.bands.clear();
      for (const e of events) this.upsertBand(e);
    }
    upsertBand(e) {
      if (!["interrupcao", "microqueda", "monitor_inativo"].includes(e.type)) return;
      this.bands.set(e.id, { type: e.type, start: e.start_ts, end: e.end_ts });
    }

    // --------------------------------------------------------- interno
    _updateEmpty() {
      if (this.emptyEl) this.emptyEl.hidden = this.points.length > 0;
    }

    _gradients(area, ctx) {
      const key = `${area.top}|${area.bottom}`;
      if (this._gradCache && this._gradCache.key === key) return this._gradCache;
      const mk = ([top, bottom]) => {
        const g = ctx.createLinearGradient(0, area.top, 0, area.bottom);
        g.addColorStop(0, top);
        g.addColorStop(1, bottom);
        return g;
      };
      this._gradCache = {
        key,
        ok: mk(COLORS.ok), warn: mk(COLORS.warn), crit: mk(COLORS.crit), partial: mk(COLORS.partial),
        hatch: this._hatch || (this._hatch = makeHatch(ctx)),
      };
      return this._gradCache;
    }

    _color(ctx) {
      const area = ctx.chart.chartArea;
      if (!area) return COLORS.ok[0];
      const g = this._gradients(area, ctx.chart.ctx);
      const p = ctx.raw;
      if (!p) return g.ok;
      switch (p.s.status) {
        case 1: return g.warn;
        case 2: return g.crit;
        case 3: return g.partial;
        case 4: return g.hatch;
        default: return g.ok;
      }
    }

    _build() {
      const self = this;
      const bandGeom = (chart) => {
        const { chartArea: a, scales: { x } } = chart;
        const out = [];
        if (!a) return out;
        const half = self.intervalMs / 2;
        for (const b of self.bands.values()) {
          const end = b.end ?? (self.view ?? b.start) + half;
          if (end < x.min || b.start > x.max) continue;
          const shift = b.type === "monitor_inativo" ? 0 : half;
          let x1 = Math.max(x.getPixelForValue(b.start - shift), a.left);
          let x2 = Math.min(x.getPixelForValue(end - shift), a.right);
          if (x2 - x1 < 1) x2 = x1 + 1;
          out.push({ b, x1, x2 });
        }
        return out;
      };

      const bandsPlugin = {
        id: "slmBands",
        // Fundo das faixas (atrás das barras)
        beforeDatasetsDraw(chart) {
          const { ctx, chartArea: a } = chart;
          if (!a) return;
          ctx.save();
          ctx.beginPath(); ctx.rect(a.left, a.top, a.width, a.height); ctx.clip();
          for (const { b, x1, x2 } of bandGeom(chart)) {
            if (b.type === "interrupcao") {
              ctx.fillStyle = "rgba(244, 63, 94, 0.14)";
              ctx.fillRect(x1, a.top, x2 - x1, a.height);
            } else if (b.type === "microqueda") {
              ctx.fillStyle = "rgba(251, 146, 60, 0.11)";
              ctx.fillRect(x1, a.top, x2 - x1, a.height);
            } else {
              ctx.fillStyle = "rgba(100, 116, 139, 0.10)";
              ctx.fillRect(x1, a.top, x2 - x1, a.height);
              ctx.strokeStyle = "rgba(100, 116, 139, 0.28)";
              ctx.lineWidth = 1;
              ctx.save(); ctx.beginPath(); ctx.rect(x1, a.top, x2 - x1, a.height); ctx.clip();
              ctx.beginPath();
              for (let i = x1 - a.height; i < x2; i += 9) { ctx.moveTo(i, a.bottom); ctx.lineTo(i + a.height, a.top); }
              ctx.stroke(); ctx.restore();
            }
          }
          ctx.restore();
        },
        // Contorno e rótulo (na frente das barras, para nunca ficarem escondidos)
        afterDatasetsDraw(chart) {
          const { ctx, chartArea: a } = chart;
          if (!a) return;
          ctx.save();
          ctx.beginPath(); ctx.rect(a.left, a.top, a.width, a.height); ctx.clip();
          for (const { b, x1, x2 } of bandGeom(chart)) {
            const color = b.type === "interrupcao" ? "244, 63, 94" : b.type === "microqueda" ? "251, 146, 60" : "100, 116, 139";
            ctx.fillStyle = `rgba(${color}, 0.95)`;
            ctx.fillRect(x1, a.top, x2 - x1, 3);
            ctx.strokeStyle = `rgba(${color}, 0.55)`;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(Math.round(x1) + 0.5, a.top); ctx.lineTo(Math.round(x1) + 0.5, a.bottom);
            if (b.end != null) { ctx.moveTo(Math.round(x2) - 0.5, a.top); ctx.lineTo(Math.round(x2) - 0.5, a.bottom); }
            ctx.stroke();
            const label = b.type === "interrupcao" ? "INTERRUPÇÃO" : b.type === "microqueda" ? "MICROQUEDA" : "MONITOR INATIVO";
            ctx.font = "600 10px Inter, sans-serif";
            const tw = ctx.measureText(label).width;
            const w = x2 - x1;
            if (w > tw + 14 || b.type === "interrupcao") {
              const lx = Math.min(Math.max(x1 + (w - tw) / 2 - 6, a.left + 2), a.right - tw - 14);
              ctx.fillStyle = "rgba(7, 11, 16, 0.88)";
              ctx.fillRect(lx, a.top + 7, tw + 12, 17);
              ctx.fillStyle = b.type === "interrupcao" ? "#fda4af" : b.type === "microqueda" ? "#fdba74" : "#94a3b8";
              ctx.fillText(label, lx + 6, a.top + 19);
            }
          }
          ctx.restore();
        },
      };

      const thresholds = () => [[self.normalMs, "251, 191, 36"], [self.elevatedMs, "251, 146, 60"]];
      const thresholdPlugin = {
        id: "slmThresholds",
        // linhas atrás das barras
        beforeDatasetsDraw(chart) {
          const { ctx, chartArea: a, scales: { y } } = chart;
          if (!a) return;
          ctx.save();
          ctx.setLineDash([4, 5]);
          ctx.lineWidth = 1;
          for (const [v, rgb] of thresholds()) {
            if (v >= y.max) continue;
            const py = Math.round(y.getPixelForValue(v)) + 0.5;
            ctx.strokeStyle = `rgba(${rgb}, .45)`;
            ctx.beginPath(); ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); ctx.stroke();
          }
          ctx.restore();
        },
        // rótulos na frente, no canto esquerdo (as barras novas ficam à direita)
        afterDatasetsDraw(chart) {
          const { ctx, chartArea: a, scales: { y } } = chart;
          if (!a) return;
          ctx.save();
          ctx.font = "500 10px 'JetBrains Mono', monospace";
          for (const [v, rgb] of thresholds()) {
            if (v >= y.max) continue;
            const py = Math.round(y.getPixelForValue(v));
            const txt = v === self.normalMs ? `limite normal ${v} ms` : `limite elevado ${v} ms`;
            const tw = ctx.measureText(txt).width;
            ctx.fillStyle = "rgba(7, 11, 16, 0.85)";
            ctx.fillRect(a.left + 4, py - 15, tw + 10, 14);
            ctx.fillStyle = `rgba(${rgb}, .95)`;
            ctx.fillText(txt, a.left + 9, py - 5);
          }
          ctx.restore();
        },
      };

      const hoverPlugin = {
        id: "slmHover",
        afterDatasetsDraw(chart) {
          if (!self.hovered) return;
          const meta = chart.getDatasetMeta(0);
          const idx = self.points.indexOf(self.hovered);
          const el = idx >= 0 ? meta.data[idx] : null;
          if (!el) return;
          const { ctx, chartArea: a } = chart;
          ctx.save();
          ctx.strokeStyle = "rgba(230, 237, 243, 0.9)";
          ctx.lineWidth = 1.5;
          const w = Math.max(el.width, 3);
          ctx.strokeRect(el.x - w / 2 - 1.5, Math.min(el.y, el.base) - 1.5, w + 3, Math.abs(el.base - el.y) + 1.5);
          ctx.setLineDash([2, 4]);
          ctx.strokeStyle = "rgba(230, 237, 243, 0.25)";
          ctx.beginPath(); ctx.moveTo(el.x + 0.5, a.top); ctx.lineTo(el.x + 0.5, a.bottom); ctx.stroke();
          ctx.restore();
        },
      };

      this.chart = new Chart(this.canvas, {
        type: "bar",
        data: {
          datasets: [{
            data: this.points,
            parsing: false,
            backgroundColor: (ctx) => self._color(ctx),
            borderRadius: { topLeft: 2, topRight: 2 },
            borderSkipped: "bottom",
            barThickness: 4,
            grouped: false,
          }],
        },
        options: {
          animation: false,
          responsive: true,
          maintainAspectRatio: false,
          normalized: true,
          parsing: false,
          events: [],
          layout: { padding: { top: 4, right: 4, left: 0, bottom: 0 } },
          scales: {
            x: {
              type: "linear",
              offset: false,
              grid: { color: COLORS.grid, drawTicks: false },
              border: { display: false },
              ticks: {
                color: COLORS.tick, font: { family: "JetBrains Mono", size: 11 }, padding: 8,
                includeBounds: false, autoSkip: false, maxRotation: 0, stepSize: 30000,
                callback: (v) => F.time(v),
              },
            },
            y: {
              min: 0, max: 150,
              grid: { color: COLORS.grid, drawTicks: false },
              border: { display: false },
              ticks: { color: COLORS.tick, font: { family: "JetBrains Mono", size: 11 }, padding: 8, maxTicksLimit: 6,
                       callback: (v) => (Math.abs(v - Math.round(v / 10) * 10) < 0.01 ? `${Math.round(v)} ms` : "") },
            },
          },
          plugins: { legend: { display: false }, tooltip: { enabled: false } },
        },
        plugins: [bandsPlugin, thresholdPlugin, hoverPlugin],
      });
    }

    _stepFor(windowMs) {
      const s = windowMs / 1000;
      if (s <= 60) return 10000;
      if (s <= 180) return 30000;
      if (s <= 300) return 60000;
      return 120000;
    }

    _frame(t) {
      requestAnimationFrame(this._frame);
      if (t - this.lastFrame < 30) return;   // ~30 quadros/s: suave e leve
      const dt = this.lastFrame ? Math.min(t - this.lastFrame, 250) : 0;
      this.lastFrame = t;
      if (this.lastTs == null) return;

      // Relógio de exibição: avança suavemente e corrige devagar, sem voltar no tempo.
      if (!this.paused) {
        const target = this.lastTs + Math.min(performance.now() - this.lastArrive, this.intervalMs * 1.5);
        if (this.view == null) this.view = target;
        let next = this.view + dt;
        next += (target - next) * 0.08;
        if (next > this.view) this.view = next;
        if (this.view > this.lastTs + this.intervalMs * 1.5) this.view = this.lastTs + this.intervalMs * 1.5;
      } else if (t - (this._lastEmit || 0) > 500) {
        this._lastEmit = t;
        this._emit();   // atualiza o "atrasado em" enquanto pausado
      }

      const xMax = this.view + this.intervalMs * 0.6;
      const xMin = xMax - this.windowMs;

      // Descarta o que ficou muito para trás (mantém 11 min para trocar a janela).
      const cutoff = (this.lastTs ?? xMax) - KEEP_MS;
      let drop = 0;
      while (drop < this.points.length && this.points[drop].x < cutoff) drop++;
      if (drop) this.points.splice(0, drop);

      // Escala vertical automática, suavizada.
      let maxLat = 0;
      const vis = [];
      for (const p of this.points) {
        if (p.x < xMin - this.intervalMs) continue;
        if (p.s.lat != null) { vis.push(p.s.lat); if (p.s.lat > maxLat) maxLat = p.s.lat; }
      }
      let targetMax = Math.max(150, this.normalMs * 1.3);
      if (vis.length) {
        vis.sort((a, b) => a - b);
        const p95 = vis[Math.floor(vis.length * 0.95)];
        const capped = Math.min(maxLat, Math.max(p95 * 3, this.elevatedMs * 1.2));
        targetMax = Math.max(targetMax, niceCeil(capped * 1.12));
      }
      this.yMax += (targetMax - this.yMax) * 0.12;
      if (Math.abs(targetMax - this.yMax) < 0.5) this.yMax = targetMax;

      const now = performance.now();
      const floor = this.yMax * 0.012;
      for (const p of this.points) {
        const k = p.born ? easeOut(Math.min(1, (now - p.born) / GROW_MS)) : 1;
        const full = p.s.status === 4 || p.s.lat == null ? this.yMax : Math.max(p.s.lat, floor);
        p.y = Math.min(full, this.yMax) * k;
      }

      const area = this.chart.chartArea;
      const width = area ? area.width : this.canvas.clientWidth;
      const per = width / (this.windowMs / this.intervalMs);
      const ds = this.chart.data.datasets[0];
      ds.barThickness = Math.max(1.5, per * 0.64);
      ds.borderRadius = per > 5 ? { topLeft: 2, topRight: 2 } : 0;

      const sx = this.chart.options.scales.x;
      sx.min = xMin; sx.max = xMax; sx.ticks.stepSize = this._stepFor(this.windowMs);
      this.chart.options.scales.y.max = this.yMax;
      this.chart.update("none");
      this._updateHover();
      this._syncScroll();
    }

    // ---------------------------------------------------------- dica
    _bindHover() {
      this.canvas.addEventListener("mousemove", (e) => {
        const r = this.canvas.getBoundingClientRect();
        // corrige o zoom da página (a área visível do canvas pode estar escalada)
        const kx = r.width ? this.canvas.clientWidth / r.width : 1;
        const ky = r.height ? this.canvas.clientHeight / r.height : 1;
        this.mouse = { x: (e.clientX - r.left) * kx, y: (e.clientY - r.top) * ky };
      });
      // Roda do mouse: com o gráfico pausado, volta/avança no tempo.
      this.canvas.addEventListener("wheel", (e) => {
        if (!this.paused) return;
        e.preventDefault();
        const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
        this.scrollBy((d > 0 ? 1 : -1) * this.windowMs * 0.12);
      }, { passive: false });
      this.canvas.addEventListener("mouseleave", () => {
        this.mouse = null;
        this.hovered = null;
        this.hoverKey = null;
        if (this.tooltip) this.tooltip.hidden = true;
      });
    }

    _updateHover() {
      if (!this.mouse || !this.tooltip) return;
      const { chartArea: a, scales: { x } } = this.chart;
      if (!a || this.mouse.x < a.left || this.mouse.x > a.right) {
        this.hovered = null; this.tooltip.hidden = true; return;
      }
      const v = x.getValueForPixel(this.mouse.x);
      let best = null, bestD = Infinity;
      for (let i = this.points.length - 1; i >= 0; i--) {
        const d = Math.abs(this.points[i].x - v);
        if (d < bestD) { bestD = d; best = this.points[i]; }
        if (this.points[i].x < v - this.intervalMs * 2) break;
      }
      if (!best || bestD > this.intervalMs * 0.75) {
        this.hovered = null; this.tooltip.hidden = true; return;
      }
      this.hovered = best;
      if (this.hoverKey !== best.x) {
        this.hoverKey = best.x;
        this.tooltip.innerHTML = this._tooltipHtml(best.s);
      }
      this.tooltip.hidden = false;
      const px = x.getPixelForValue(best.x);
      const tw = this.tooltip.offsetWidth, th = this.tooltip.offsetHeight;
      let left = px + 14;
      if (left + tw > this.wrap.clientWidth - 4) left = px - tw - 14;
      let top = Math.min(Math.max(this.mouse.y - th / 2, 0), this.wrap.clientHeight - th);
      this.tooltip.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
    }

    _tooltipHtml(s) {
      const rows = [];
      for (const id of ["cf", "gg", "gw", "dish"]) {
        const t = s.t && s.t[id];
        if (!t || t.m == null) continue;
        if (t.m === 0) {
          rows.push(`<div class="tt-row"><span>${F.esc(this.names[id] || id)}</span><b class="fail">sem resposta</b></div>`);
        } else {
          rows.push(`<div class="tt-row"><span>${F.esc(this.names[id] || id)}</span><b>${F.ms(t.rtt)} ms<em>${METHOD[t.m]}</em></b></div>`);
        }
      }
      const foot = [];
      if (s.lat != null) foot.push(`Latência principal: ${F.ms(s.lat)} ms`);
      if (s.wifi != null) foot.push(`Sinal Wi-Fi: ${s.wifi}%`);
      if (s.conn === "offline") foot.push("Dentro de interrupção confirmada");
      return `<div class="tt-time">${F.time(s.ts)} <span style="color:#6b7d8f;font-weight:400">${F.dateShort(s.ts)}</span></div>
        <div class="tt-status" style="color:${STATUS_COLOR[s.status]}">${STATUS_LABEL[s.status]}</div>
        ${rows.join("")}
        ${foot.length ? `<div class="tt-foot">${foot.join(" · ")}</div>` : ""}`;
    }
  }

  window.RealtimeBars = RealtimeBars;
})();
