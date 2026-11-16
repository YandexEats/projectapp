(function () {
  "use strict";
  const params = new URLSearchParams(location.search);
  const TEST = params.get("test") === "1";
  const DATA = TEST ? "testdata/" : "data/";
  const T_ORDER = ["moto", "car", "ebike", "bike", "walking"];
  const T_RU = { moto: "Мото", car: "Авто", ebike: "Электровело", bike: "Вело", walking: "Пешие" };
  const T_ICON = { moto: "🏍", car: "🚗", ebike: "🚲", bike: "🚴", walking: "🚶" };
  const PALETTE = ["#7000ff", "#e8590c", "#0b8a5f", "#1c7ed6", "#c2255c", "#8f6a00", "#5f3dc4", "#0c8599"];
  const FIELD_RU = {
    bonus: "Бонус", orders: "Условие (заказы)", days: "Срок (дни)", tier_added: "Новый уровень", tier_removed: "Уровень убран",
    transport_added: "Транспорт добавлен", transport_removed: "Транспорт убран", city_added: "Город добавлен",
    city_removed: "Город убран", text: "Текст",
  };

  const $ = (id) => document.getElementById(id);
  const fmt = (n) => (n === null || n === undefined ? "—" : Math.round(n).toLocaleString("ru-RU").replace(/ |,/g, " "));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (d) => { if (!d) return "—"; const [y, m, dd] = d.split("-"); return `${dd}.${m}.${y}`; };
  const fmtDateTime = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString("ru-RU", { timeZone: "Asia/Tashkent", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) + " (Ташкент)";
  };
  const dateMs = (d) => Date.parse(d + "T00:00:00Z");
  const maxTier = (tiers) => (tiers && tiers.length ? tiers.reduce((a, b) => (b.bonus > a.bonus ? b : a)) : null);

  async function load(name, fallback) {
    try {
      const r = await fetch(DATA + name + "?t=" + Date.now(), { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      return await r.json();
    } catch (e) { return fallback; }
  }

  let history = [], changes = [], last = {}, charts = {};

  function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

  function cityName(slug) {
    for (let i = history.length - 1; i >= 0; i--) {
      const c = history[i].cities && history[i].cities[slug];
      if (c && c.name_ru) return c.name_ru;
    }
    return slug;
  }
  function allCities() {
    const s = [];
    history.forEach((h) => Object.keys(h.cities || {}).forEach((c) => { if (!s.includes(c)) s.push(c); }));
    return s;
  }
  function transportsOf(snap) {
    const s = new Set();
    Object.values(snap.cities || {}).forEach((c) => Object.keys(c.transports || {}).forEach((t) => s.add(t)));
    return T_ORDER.filter((t) => s.has(t)).concat([...s].filter((t) => !T_ORDER.includes(t)));
  }

  function renderHeader() {
    $("last-check").textContent = fmtDateTime(last.checked_at);
    const st = { no_change: "без изменений", changed: "есть изменения", baseline: "базовый снимок", error: "ошибка чтения сайта", unconfirmed: "не подтверждено" }[last.status] || last.status || "";
    $("last-status").textContent = st;
    if (last.status === "error") $("last-status").classList.add("error");
    const lastChange = changes.length ? changes.map((c) => c.date).sort().pop() : null;
    $("last-change").textContent = lastChange ? fmtDate(lastChange) : "изменений пока не было";
    if (history.length) $("history-start").textContent = "История начинается с " + fmtDate(history[0].date) + (history.length === 1 ? " (пока один снимок)" : ` · снимков: ${history.length}`);
  }

  function renderTable() {
    const cur = history[history.length - 1];
    const ts = transportsOf(cur).length ? transportsOf(cur) : T_ORDER;
    const today = last.checked_at ? new Date(last.checked_at) : new Date();
    const weekAgo = new Date(today.getTime() - 7 * 86400000).toISOString().slice(0, 10);
    const recent = new Set(changes.filter((c) => c.date >= weekAgo).map((c) => c.city + "|" + c.transport));
    let h = "<thead><tr><th class='city'>Город</th>" + ts.map((t) => `<th>${T_ICON[t] || ""} ${esc(T_RU[t] || t)}</th>`).join("") + "</tr></thead><tbody>";
    Object.entries(cur.cities).forEach(([slug, c]) => {
      h += `<tr><td class="city">${esc(c.name_ru || slug)}</td>`;
      ts.forEach((t) => {
        const tiers = (c.transports || {})[t] || [];
        const changed = recent.has(slug + "|" + t) ? " changed" : "";
        if (!tiers.length) { h += `<td class="empty${changed}">—</td>`; return; }
        const m = maxTier(tiers);
        h += `<td class="${changed}"><div class="amt">${fmt(m.bonus)} сум</div><div class="cond">${m.orders} заказов / ${m.days} дней</div>` +
          (tiers.length > 1 ? `<div class="more" title="${esc(tiers.map((x) => `${x.orders} заказов/${x.days} дн → ${fmt(x.bonus)}`).join("; "))}">уровней: ${tiers.length}</div>` : "") + "</td>";
      });
      h += "</tr>";
    });
    $("current-table").innerHTML = h + "</tbody>";
  }

  function fillSelect(sel, opts, value) {
    sel.innerHTML = opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
    if (value !== undefined && opts.some(([v]) => v === value)) sel.value = value;
  }

  function seriesFor(slug, t) {
    // ступенчатый ряд: значение в каждом снимке + продление до последней проверки
    const pts = history.map((h) => {
      const c = h.cities[slug];
      const m = c ? maxTier((c.transports || {})[t]) : null;
      return { x: dateMs(h.date), y: m ? m.bonus : null };
    });
    const endDate = (last.checked_at || "").slice(0, 10);
    if (pts.length && endDate && dateMs(endDate) > pts[pts.length - 1].x) pts.push({ x: dateMs(endDate), y: pts[pts.length - 1].y });
    if (pts.length === 1) pts.push({ x: pts[0].x + 86400000, y: pts[0].y });
    return pts;
  }

  const tickDate = (v) => { const d = new Date(v); return `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")}.${String(d.getUTCFullYear()).slice(2)}`; };

  function renderHistoryChart() {
    const city = $("f-city").value, t = $("f-transport").value;
    const slugs = city === "all" ? allCities() : [city];
    const datasets = slugs.map((s, i) => {
      const color = PALETTE[allCities().indexOf(s) % PALETTE.length];
      return { label: cityName(s), data: seriesFor(s, t), stepped: "before", borderColor: color, backgroundColor: color, pointRadius: 3, borderWidth: 2, spanGaps: false };
    });
    if (charts.line) charts.line.destroy();
    charts.line = new Chart($("history-chart"), {
      type: "line",
      data: { datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "nearest", intersect: false },
        scales: {
          x: { type: "linear", ticks: { callback: tickDate, maxTicksLimit: 6, color: cssVar("--muted") }, grid: { color: cssVar("--border") } },
          y: { beginAtZero: true, ticks: { callback: (v) => fmt(v), color: cssVar("--muted") }, grid: { color: cssVar("--border") } },
        },
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 12, color: cssVar("--text") } },
          tooltip: { callbacks: { title: (it) => tickDate(it[0].parsed.x), label: (it) => `${it.dataset.label}: ${it.parsed.y === null ? "нет" : fmt(it.parsed.y) + " сум"}` } },
        },
      },
    });
  }

  function renderBarChart() {
    const cur = history[history.length - 1];
    const slugs = Object.keys(cur.cities);
    const ts = transportsOf(cur).filter((t) => slugs.some((s) => ((cur.cities[s].transports || {})[t] || []).length));
    const datasets = ts.map((t, i) => ({
      label: T_RU[t] || t, backgroundColor: PALETTE[i % PALETTE.length], borderRadius: 3,
      data: slugs.map((s) => { const m = maxTier((cur.cities[s].transports || {})[t]); return m ? m.bonus : 0; }),
    }));
    if (charts.bar) charts.bar.destroy();
    charts.bar = new Chart($("bar-chart"), {
      type: "bar",
      data: { labels: slugs.map((s) => cur.cities[s].name_ru || s), datasets },
      options: {
        indexAxis: "y", responsive: true, maintainAspectRatio: false, animation: false,
        scales: {
          x: { beginAtZero: true, ticks: { callback: (v) => fmt(v / 1000) + "k", color: cssVar("--muted") }, grid: { color: cssVar("--border") } },
          y: { ticks: { color: cssVar("--text") }, grid: { display: false } },
        },
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 12, color: cssVar("--text") } },
          tooltip: { callbacks: { label: (it) => `${it.dataset.label}: ${it.parsed.x ? fmt(it.parsed.x) + " сум" : "нет"}` } },
        },
      },
    });
  }

  function changeText(c) {
    const unit = c.field === "orders" ? " заказов" : c.field === "days" ? " дней" : " сум";
    switch (c.field) {
      case "bonus": case "orders": case "days":
        return `${fmt(c.old)} → ${fmt(c.new)}${unit}`;
      case "transport_added": return `появился: ${fmt(c.new)} сум, ${c.orders} заказов / ${c.days} дней`;
      case "transport_removed": return `убран (было ${fmt(c.old)} сум)`;
      case "tier_added": return `уровень ${c.tier}: ${fmt(c.new)} сум, ${c.orders} заказов / ${c.days} дней`;
      case "tier_removed": return `уровень ${c.tier} убран (было ${fmt(c.old)} сум)`;
      case "city_added": return "город появился в программе";
      case "city_removed": return "город убран из программы";
      case "text": return `${esc(c.label || "")}: «${esc(c.old ?? "—")}» → «${esc(c.new ?? "—")}»`;
      default: return `${esc(c.old)} → ${esc(c.new)}`;
    }
  }

  function renderLog() {
    const city = $("f-log-city").value;
    const list = changes.map((c, i) => ({ ...c, _i: i })).filter((c) => city === "all" || c.city === city)
      .sort((a, b) => (a.date === b.date ? b._i - a._i : a.date < b.date ? 1 : -1));
    const ul = $("change-log");
    if (!list.length) {
      ul.innerHTML = `<li class="empty">Изменений пока не было${history.length ? " — история начинается с " + fmtDate(history[0].date) : ""}.</li>`;
      return;
    }
    ul.innerHTML = list.map((c) => {
      const d = typeof c.diff === "number" ? c.diff : null;
      const good = d === null ? null : (c.field === "orders" || c.field === "days") ? d < 0 : d > 0;
      const cls = d === null || d === 0 ? "neutral" : good ? "up" : "down";
      const sign = d === null ? "" : (d > 0 ? "+" : "−") + fmt(Math.abs(d)) + (c.field === "orders" ? " зак." : c.field === "days" ? " дн." : "");
      const tName = c.transport ? `${T_ICON[c.transport] || ""} ${T_RU[c.transport] || c.transport}` : FIELD_RU[c.field] || "";
      return `<li id="chg-${esc(c.date)}-${esc(c.city || "all")}" data-date="${esc(c.date)}" data-city="${esc(c.city || "")}">` +
        `<div class="when">${fmtDate(c.date)} · ${esc(c.city ? cityName(c.city) : "Общее")}</div>` +
        `<div class="what">${tName}${c.transport && c.field !== "bonus" ? " · " + esc(FIELD_RU[c.field] || c.field) : ""}</div>` +
        `<div class="vals">${changeText(c)}</div>` +
        (sign ? `<div class="delta ${cls}">${sign}</div>` : "") + "</li>";
    }).join("");
  }

  function renderOffices() {
    const cur = history[history.length - 1];
    const off = cur.offices || {};
    const slugs = Object.keys(off);
    $("offices").innerHTML = slugs.length ? slugs.map((s) => {
      const o = off[s];
      return `<div class="office"><b>${esc(cityName(s))}</b>${esc(o.address || "—")}<br><span class="hint">${esc(o.days || "")} ${esc(o.hours || "")}</span>` +
        (o.map ? `<br><a href="${esc(o.map)}" target="_blank" rel="noopener">Открыть на карте</a>` : "") + "</div>";
    }).join("") : '<p class="hint">Нет данных об офисах.</p>';
  }

  function applyDeepLink() {
    const city = params.get("city"), date = params.get("date");
    if (!date) { if (city) document.getElementById("block-table").scrollIntoView(); return; }
    const items = [...document.querySelectorAll("#change-log li[data-date]")];
    const target = items.find((li) => li.dataset.date === date && (!city || li.dataset.city === city)) || items.find((li) => li.dataset.date === date);
    if (target) {
      items.filter((li) => li.dataset.date === date && (!city || li.dataset.city === city)).forEach((li) => li.classList.add("target"));
      setTimeout(() => target.scrollIntoView({ behavior: "smooth", block: "center" }), 150);
    }
  }

  async function main() {
    if (TEST) { $("test-banner").hidden = false; document.title = "ТЕСТ · " + document.title; }
    [history, changes, last] = await Promise.all([load("history.json", []), load("changes.json", []), load("last_check.json", {})]);
    renderHeader();
    if (!history.length) {
      $("error").hidden = false;
      $("error").textContent = "Данных пока нет — первая проверка ещё не выполнялась.";
      document.querySelectorAll("main .card").forEach((c) => (c.hidden = true));
      return;
    }
    renderTable();
    const cities = allCities();
    const qCity = params.get("city");
    const cityOpts = [["all", "Все города"]].concat(cities.map((s) => [s, cityName(s)]));
    fillSelect($("f-city"), cityOpts, cities.includes(qCity) ? qCity : "all");
    fillSelect($("f-log-city"), cityOpts, cities.includes(qCity) ? qCity : "all");
    const ts = transportsOf(history[history.length - 1]);
    let defT = "moto";
    const qT = params.get("transport");
    if (qT && ts.includes(qT)) defT = qT;
    else if (qCity && params.get("date")) { const c = changes.find((x) => x.city === qCity && x.date === params.get("date") && x.transport); if (c) defT = c.transport; }
    fillSelect($("f-transport"), ts.map((t) => [t, T_RU[t] || t]), defT);
    $("f-city").addEventListener("change", renderHistoryChart);
    $("f-transport").addEventListener("change", renderHistoryChart);
    $("f-log-city").addEventListener("change", renderLog);
    if (window.Chart) { renderHistoryChart(); renderBarChart(); }
    else document.querySelectorAll(".chart-box").forEach((b) => (b.innerHTML = '<p class="hint">Не удалось загрузить библиотеку графиков.</p>'));
    renderLog();
    renderOffices();
    applyDeepLink();
  }
  main();
})();
