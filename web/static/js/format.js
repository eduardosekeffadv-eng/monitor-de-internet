/* Formatação em português brasileiro. */
(function () {
  "use strict";
  const nf0 = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
  const nf1 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const nf2 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const nf3 = new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });

  const pad = (n) => String(n).padStart(2, "0");

  const F = {
    int: (v) => (v == null ? "—" : nf0.format(v)),
    dec1: (v) => (v == null ? "—" : nf1.format(v)),
    dec2: (v) => (v == null ? "—" : nf2.format(v)),
    dec3: (v) => (v == null ? "—" : nf3.format(v)),

    /** Latência: nunca mostra 0 para resposta abaixo de 1 ms. */
    ms(v) {
      if (v == null) return "—";
      if (v < 1) return "<1";
      return v < 10 ? nf1.format(v) : nf0.format(v);
    },

    time(ts) {
      const d = new Date(ts);
      return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    },
    hm(ts) {
      const d = new Date(ts);
      return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    },
    date(ts) {
      const d = new Date(ts);
      return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}`;
    },
    dateShort(ts) {
      const d = new Date(ts);
      return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}`;
    },
    isToday(ts) {
      const a = new Date(ts), b = new Date();
      return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    },
    dayLabel(ts) {
      if (F.isToday(ts)) return "hoje";
      const y = new Date(); y.setDate(y.getDate() - 1);
      const d = new Date(ts);
      if (d.getFullYear() === y.getFullYear() && d.getMonth() === y.getMonth() && d.getDate() === y.getDate()) return "ontem";
      return F.date(ts);
    },

    /** Duração legível: 8 s · 3 min 12 s · 2 h 05 min · 1 d 3 h */
    duration(ms) {
      if (ms == null) return "—";
      let s = Math.max(0, Math.round(ms / 1000));
      if (s < 60) return `${s} s`;
      const d = Math.floor(s / 86400); s -= d * 86400;
      const h = Math.floor(s / 3600); s -= h * 3600;
      const m = Math.floor(s / 60); s -= m * 60;
      if (d > 0) return `${d} d ${h} h`;
      if (h > 0) return `${h} h ${pad(m)} min`;
      return `${m} min ${pad(s)} s`;
    },
    /** Margem de precisão, ex.: "±1 s" */
    margin(ms) {
      if (!ms) return "";
      const s = ms / 1000;
      return s >= 60 ? `±${Math.round(s / 60)} min` : `±${s % 1 === 0 ? s : nf1.format(s)} s`;
    },
    bytes(b) {
      if (b == null) return "—";
      if (b < 1024 * 1024) return `${nf0.format(b / 1024)} KB`;
      if (b < 1024 * 1024 * 1024) return `${nf1.format(b / 1048576)} MB`;
      return `${nf2.format(b / 1073741824)} GB`;
    },
    esc(s) {
      return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    },
  };

  window.F = F;
})();
