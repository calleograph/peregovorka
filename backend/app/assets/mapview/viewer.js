/* Просмотр «Карты разговора». Один файл для двух режимов:
 *   • во встроенном окне страницы встречи (iframe с sandbox без allow-same-origin): данные приходят через postMessage от страницы, действия
 *     («перейти к реплике», «сохранить правку») уходят ей же — у самого окна нет ни cookie, ни доступа к API;
 *   • как самостоятельный HTML-файл (выгрузка): данные лежат в <script type="application/json" id="pg-map-data">, файл открывается без сервера.
 * Все тексты данных вставляются только как текст (textContent): внедрить HTML/JS через стенограмму или ответ модели нельзя. */
(function (root) {
  "use strict";

  // ------------------------------------------------------------------ чистые функции (проверяются тестами)
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function clockOffset(sec) {
    sec = Math.max(0, Math.round(sec));
    return pad(Math.floor(sec / 3600)) + ":" + pad(Math.floor(sec % 3600 / 60)) + ":" + pad(sec % 60);
  }
  function duration(sec) {
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return sec + " с";
    var m = Math.round(sec / 60);
    if (m < 60) return m + " мин";
    return Math.floor(m / 60) + " ч" + (m % 60 ? " " + (m % 60) + " мин" : "");
  }
  function plural(n, one, few, many) {
    var a = n % 10, b = n % 100;
    return a === 1 && b !== 11 ? one : (a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many);
  }
  function wallClock(startedAt, tz, sec) {
    var d = new Date(new Date(startedAt).getTime() + sec * 1000);
    try { return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: tz || undefined }).format(d); }
    catch (e) { return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  }
  function fullDate(startedAt, tz) {
    try { return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: tz || undefined }).format(new Date(startedAt)); }
    catch (e) { return String(startedAt).slice(0, 10); }
  }

  /* Squarified treemap (Bruls, Huizing, van Wijk): items = [{id, value}] → [{id, x, y, w, h}] в прямоугольнике w × h. */
  function squarify(items, W, H) {
    var list = items.filter(function (i) { return i.value > 0; }).sort(function (a, b) { return b.value - a.value; });
    var total = list.reduce(function (s, i) { return s + i.value; }, 0);
    var out = [];
    if (!total || W <= 0 || H <= 0) return out;
    var scale = W * H / total;
    var nodes = list.map(function (i) { return { id: i.id, area: i.value * scale }; });
    var x = 0, y = 0, w = W, h = H;
    function worst(row, side) {
      var s = row.reduce(function (a, n) { return a + n.area; }, 0), max = 0, min = Infinity;
      row.forEach(function (n) { max = Math.max(max, n.area); min = Math.min(min, n.area); });
      return Math.max(side * side * max / (s * s), s * s / (side * side * min));
    }
    function place(row) {
      var s = row.reduce(function (a, n) { return a + n.area; }, 0);
      if (w >= h) {                                   // столбец слева
        var cw = s / h, cy = y;
        row.forEach(function (n) { var ch = n.area / cw; out.push({ id: n.id, x: x, y: cy, w: cw, h: ch }); cy += ch; });
        x += cw; w -= cw;
      } else {                                        // строка сверху
        var rh = s / w, cx = x;
        row.forEach(function (n) { var cw2 = n.area / rh; out.push({ id: n.id, x: cx, y: y, w: cw2, h: rh }); cx += cw2; });
        y += rh; h -= rh;
      }
    }
    var row = [];
    nodes.forEach(function (n) {
      var side = Math.min(w, h);
      if (!row.length || worst(row.concat([n]), side) <= worst(row, side)) { row.push(n); }
      else { place(row); row = [n]; }
    });
    if (row.length) place(row);
    return out;
  }

  /* Шкала времени: отрезки тем → проценты от длительности встречи (не меньше 0.4 %, чтобы короткий отрезок был виден и нажимался). */
  function timelineBars(topics, total) {
    var bars = [];
    if (!total) return bars;
    topics.forEach(function (t) {
      t.segments.forEach(function (s) {
        bars.push({ topic: t.id, start: s.start_s, end: s.end_s, left: s.start_s / total * 100, width: Math.max(0.4, (s.end_s - s.start_s) / total * 100) });
      });
    });
    return bars.sort(function (a, b) { return a.start - b.start; });
  }

  /* Деления шкалы: каждые 5/10/15/30/60 минут — столько, чтобы было не больше ~8 подписей. */
  function ticks(total) {
    var steps = [300, 600, 900, 1800, 3600, 7200], step = steps[steps.length - 1];
    for (var i = 0; i < steps.length; i++) { if (total / steps[i] <= 8) { step = steps[i]; break; } }
    var out = [];
    for (var s = 0; s <= total; s += step) out.push(s);
    return out;
  }

  function topicSpan(t) {
    var a = t.segments[0].start_s, b = t.segments[t.segments.length - 1].end_s;
    return [a, b];
  }

  var util = { clockOffset: clockOffset, duration: duration, plural: plural, squarify: squarify, timelineBars: timelineBars, ticks: ticks, wallClock: wallClock, topicSpan: topicSpan };
  root.PgMapUtil = util;
  if (typeof document === "undefined") return;

  // ------------------------------------------------------------------ отображение
  var STATE = { data: null, meta: null, canEdit: false, standalone: false, selected: null, person: null, category: null, editing: false, tip: null };
  var app;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function btn(text, cls, fn) { var b = el("button", cls || "", text); b.type = "button"; b.addEventListener("click", fn); return b; }
  function post(msg) { if (!STATE.standalone && root.parent && root.parent !== root) { msg.pg = "map"; root.parent.postMessage(msg, "*"); } }
  function topicById(id) { return STATE.data.topics.filter(function (t) { return t.id === id; })[0] || null; }
  function catLabel(id) { var c = STATE.data.categories.filter(function (x) { return x.id === id; })[0]; return c ? c.label : id; }

  function related(sel) {
    if (!sel) return {};
    var set = {}; set[sel.id] = true;
    (sel.related || []).forEach(function (id) { set[id] = true; });
    return set;
  }
  function personTopics(name) {
    var set = {};
    STATE.data.topics.forEach(function (t) { if (t.speakers.some(function (s) { return s.name === name; })) set[t.id] = true; });
    return set;
  }
  /* какие темы «яркие»: выбранная тема + соседние / темы выбранного участника / категория */
  function lit() {
    if (STATE.selected) return related(topicById(STATE.selected));
    if (STATE.person) return personTopics(STATE.person);
    if (STATE.category) { var s = {}; STATE.data.topics.forEach(function (t) { if (t.category === STATE.category) s[t.id] = true; }); return s; }
    return null;
  }
  function select(id) { STATE.selected = STATE.selected === id ? null : id; STATE.person = null; STATE.editing = false; render(); }

  function tooltip(e, lines) {
    if (!STATE.tip) { STATE.tip = el("div", "tip"); document.body.appendChild(STATE.tip); }
    STATE.tip.textContent = "";
    lines.forEach(function (l, i) { STATE.tip.appendChild(el("div", i ? "" : "b", l)); });
    STATE.tip.style.display = "block";
    var x = Math.min(e.clientX + 14, document.documentElement.clientWidth - STATE.tip.offsetWidth - 8);
    STATE.tip.style.left = Math.max(4, x) + "px";
    STATE.tip.style.top = (e.clientY + 16) + "px";
  }
  function hideTip() { if (STATE.tip) STATE.tip.style.display = "none"; }
  function hover(node, fn) {
    node.addEventListener("mousemove", function (e) { tooltip(e, fn()); });
    node.addEventListener("mouseleave", hideTip);
  }

  function topicLines(t) {
    var a = topicSpan(t), m = STATE.data.meeting;
    var out = [t.title, wallClock(m.started_at, m.timezone, a[0]) + "–" + wallClock(m.started_at, m.timezone, a[1]) + " · " + duration(t.total_s), catLabel(t.category)];
    if (t.segments.length > 1) out.push(t.segments.length + " " + plural(t.segments.length, "отрезок", "отрезка", "отрезков") + " (вернулись к теме)");
    return out;
  }

  function header() {
    var d = STATE.data, m = d.meeting, meta = STATE.meta || {};
    var h = el("header", "hd");
    h.appendChild(el("h1", "", "Карта разговора"));
    h.appendChild(el("div", "sub", m.title));
    var facts = [fullDate(m.started_at, m.timezone), duration(m.duration_s), m.participants + " " + plural(m.participants, "участник", "участника", "участников")];
    if (meta.model_title || meta.model) facts.push(meta.model_title || meta.model);
    if (meta.duration_s != null) facts.push("сформировано за " + duration(meta.duration_s));
    var f = el("div", "facts");
    facts.forEach(function (x) { f.appendChild(el("span", "chip", x)); });
    h.appendChild(f);
    return h;
  }

  function timeline() {
    var d = STATE.data, total = Math.max(d.meeting.duration_s, 1), L = lit();
    var sec = el("section", "card");
    sec.appendChild(el("h2", "", "Как шла встреча"));
    var bar = el("div", "tl");
    timelineBars(d.topics, total).forEach(function (b) {
      var t = topicById(b.topic);
      var s = el("button", "seg c-" + t.category + (STATE.selected === t.id ? " sel" : "") + (L && !L[t.id] ? " dim" : ""));
      s.type = "button";
      s.style.left = b.left + "%"; s.style.width = b.width + "%";
      s.setAttribute("aria-label", t.title + ", " + duration(b.end - b.start));
      s.addEventListener("click", function () { select(t.id); });
      hover(s, function () { return [t.title, wallClock(d.meeting.started_at, d.meeting.timezone, b.start) + "–" + wallClock(d.meeting.started_at, d.meeting.timezone, b.end), duration(b.end - b.start)]; });
      bar.appendChild(s);
    });
    sec.appendChild(bar);
    var ax = el("div", "ax");
    ticks(total).forEach(function (s) {
      var tk = el("span", "tick", wallClock(d.meeting.started_at, d.meeting.timezone, s));
      tk.style.left = (s / total * 100) + "%";
      ax.appendChild(tk);
    });
    sec.appendChild(ax);
    if (d.uncovered_s > 60) sec.appendChild(el("p", "note", "Светлые промежутки — время, не отнесённое ни к одной теме (" + duration(d.uncovered_s) + "): паузы, приветствия, короткие реплики."));
    return sec;
  }

  function treemap() {
    var d = STATE.data, L = lit();
    var sec = el("section", "card");
    sec.appendChild(el("h2", "", "На что ушла встреча"));
    sec.appendChild(el("p", "note", "Размер блока — потраченное время, а не важность темы."));
    var box = el("div", "tm");
    sec.appendChild(box);
    STATE.tmFill = function () {                                    // после вставки в страницу: нужна реальная ширина, чтобы блоки были пропорциональны времени
      var W = Math.max(260, box.clientWidth), H = W < 520 ? 320 : 340;
      box.style.height = H + "px";
      box.textContent = "";
      squarify(d.topics.map(function (t) { return { id: t.id, value: t.total_s }; }), W, H).forEach(function (r) {
        var t = topicById(r.id);
        var c = el("button", "cell c-" + t.category + (STATE.selected === t.id ? " sel" : "") + (L && !L[t.id] ? " dim" : ""));
        c.type = "button";
        c.style.left = (r.x / W * 100) + "%"; c.style.top = (r.y / H * 100) + "%"; c.style.width = (r.w / W * 100) + "%"; c.style.height = (r.h / H * 100) + "%";
        c.addEventListener("click", function () { select(t.id); });
        var inner = el("span", "in");
        c.setAttribute("aria-label", t.title + ", " + duration(t.total_s));
        if (r.w >= 72 && r.h >= 32) inner.appendChild(el("span", "ct", t.title));      // в совсем маленьком блоке название — во всплывающей подсказке
        if (r.w > 90 && r.h > 54) {
          var a = topicSpan(t);
          inner.appendChild(el("span", "cm", duration(t.total_s) + " · " + wallClock(d.meeting.started_at, d.meeting.timezone, a[0]) + "–" + wallClock(d.meeting.started_at, d.meeting.timezone, a[1])));
        }
        c.appendChild(inner);
        hover(c, function () { return topicLines(t); });
        box.appendChild(c);
      });
    };
    return sec;
  }

  function legend() {
    var d = STATE.data, row = el("div", "legend");
    d.categories.forEach(function (c) {
      var b = btn(c.label, "lg c-" + c.id + (STATE.category === c.id ? " on" : ""), function () { STATE.category = STATE.category === c.id ? null : c.id; STATE.selected = null; STATE.person = null; render(); });
      row.appendChild(b);
    });
    return row;
  }

  function people() {
    var d = STATE.data, sec = el("section", "card");
    sec.appendChild(el("h2", "", "Кто говорил"));
    var max = Math.max.apply(null, d.speakers.map(function (s) { return s.seconds; }).concat([1]));
    var list = el("div", "ppl");
    d.speakers.forEach(function (s) {
      var r = btn("", "pp" + (STATE.person === s.name ? " on" : ""), function () { STATE.person = STATE.person === s.name ? null : s.name; STATE.selected = null; STATE.category = null; render(); });
      r.appendChild(el("span", "pn", s.name));
      var bar = el("span", "pb"); var fill = el("i"); fill.style.width = (s.seconds / max * 100) + "%"; bar.appendChild(fill);
      r.appendChild(bar);
      r.appendChild(el("span", "pt", duration(s.seconds)));
      list.appendChild(r);
    });
    sec.appendChild(list);
    sec.appendChild(el("p", "note", "Время — по длительности реплик в стенограмме. Это не оценка вклада."));
    return sec;
  }

  function goto(item) {
    var b = btn(clockOffset(item.sec) + (item.speaker ? "  " + item.speaker : ""), "src", function () { post({ type: "goto", sec: item.sec, segmentId: item.segment_id || null }); });
    if (STATE.standalone) { b.disabled = true; b.title = "Первоисточник — в стенограмме на сервере Peregovorka"; }
    return b;
  }
  function itemBlock(title, items, kind) {
    if (!items || !items.length) return null;
    var b = el("div", "blk");
    b.appendChild(el("h4", "", title));
    items.forEach(function (it) {
      var row = el("div", "it");
      var tx = it.text;
      if (kind === "tasks") {
        tx += "  — " + (it.assignee ? it.assignee : "ответственный не назван") + (it.deadline ? ", срок: " + it.deadline : "");
      }
      row.appendChild(el("div", "tx", tx));
      var sub = el("div", "meta");
      sub.appendChild(el("span", "badge ok", "подтверждено источником"));
      if (it.sec != null) sub.appendChild(goto(it));
      row.appendChild(sub);
      if (it.quote) row.appendChild(el("blockquote", "", "«" + it.quote + "»"));
      b.appendChild(row);
    });
    return b;
  }

  function editor(t) {
    var f = el("div", "edit");                  // не <form>: в окне с sandbox без allow-forms отправка формы блокируется
    var ti = el("input"); ti.value = t.title; ti.maxLength = 120; ti.setAttribute("aria-label", "Название темы");
    var cs = el("select"); cs.setAttribute("aria-label", "Категория");
    (STATE.categories || STATE.data.categories).forEach(function (c) { var o = el("option", "", c.label); o.value = c.id; if (c.id === t.category) o.selected = true; cs.appendChild(o); });
    var nt = el("textarea"); nt.value = t.note || ""; nt.maxLength = 1000; nt.rows = 3; nt.placeholder = "Заметка к теме"; nt.setAttribute("aria-label", "Заметка");
    f.appendChild(ti); f.appendChild(cs); f.appendChild(nt);
    var row = el("div", "row");
    function doSave() { post({ type: "edit", topicId: t.id, patch: { title: ti.value, category: cs.value, note: nt.value } }); STATE.editing = false; }
    row.appendChild(btn("Сохранить", "primary", doSave));
    ti.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); doSave(); } });
    row.appendChild(btn("Отмена", "", function () { STATE.editing = false; render(); }));
    row.appendChild(btn("Вернуть как у модели", "", function () { post({ type: "edit", topicId: t.id, patch: { title: "", category: t.ai_category, note: "" } }); STATE.editing = false; }));
    f.appendChild(row);
    return f;
  }

  function card() {
    var t = STATE.selected && topicById(STATE.selected), d = STATE.data, m = d.meeting;
    var sec = el("section", "card topic");
    if (!t) { sec.appendChild(el("p", "note", "Выберите тему на шкале или в блоке «На что ушла встреча» — здесь появятся её содержание, участники, решения и ссылки на реплики.")); return sec; }
    var a = topicSpan(t);
    var top = el("div", "tc-top");
    top.appendChild(el("h2", "c-" + t.category + " ttl", t.title));
    if (STATE.canEdit && !STATE.standalone) top.appendChild(btn("Править", "ghost", function () { STATE.editing = !STATE.editing; render(); }));
    sec.appendChild(top);
    if (t.edited && t.ai_title !== t.title) sec.appendChild(el("div", "note", "Название изменено пользователем. У модели было: «" + t.ai_title + "»."));
    sec.appendChild(el("div", "when", wallClock(m.started_at, m.timezone, a[0]) + "–" + wallClock(m.started_at, m.timezone, a[1]) + " · " + duration(t.total_s) + " · " + catLabel(t.category)));
    if (STATE.editing) { sec.appendChild(editor(t)); return sec; }
    if (t.note) { var n = el("div", "usernote"); n.appendChild(el("b", "", "Заметка: ")); n.appendChild(document.createTextNode(t.note)); sec.appendChild(n); }
    if (t.summary) {
      var s = el("div", "blk"); s.appendChild(el("h4", "", "Краткое содержание"));
      s.appendChild(el("p", "", t.summary));
      s.appendChild(el("span", "badge ai", "AI-интерпретация"));
      sec.appendChild(s);
    }
    var segs = el("div", "blk"); segs.appendChild(el("h4", "", t.segments.length > 1 ? "Фрагменты обсуждения (возвращались к теме)" : "Фрагмент обсуждения"));
    var sr = el("div", "chips");
    t.segments.forEach(function (x) {
      var c = btn(wallClock(m.started_at, m.timezone, x.start_s) + "–" + wallClock(m.started_at, m.timezone, x.end_s) + " · " + duration(x.end_s - x.start_s), "src", function () { post({ type: "goto", sec: x.start_s, segmentId: null }); });
      if (STATE.standalone) c.disabled = true;
      sr.appendChild(c);
    });
    segs.appendChild(sr); sec.appendChild(segs);
    if (t.speakers.length) {
      var p = el("div", "blk"); p.appendChild(el("h4", "", "Кто участвовал"));
      t.speakers.slice(0, 8).forEach(function (s) { var r = el("div", "sp"); r.appendChild(el("span", "", s.name)); r.appendChild(el("span", "pt", duration(s.seconds))); p.appendChild(r); });
      sec.appendChild(p);
    }
    [itemBlock("Решения", t.decisions, "d"), itemBlock("Задачи", t.tasks, "tasks"), itemBlock("Открытые вопросы", t.questions, "q")].forEach(function (b) { if (b) sec.appendChild(b); });
    if (!t.decisions.length && !t.tasks.length && !t.questions.length) {
      sec.appendChild(el("p", "note", d.items_from_protocol ? "Решений, задач и открытых вопросов по этой теме в протоколе нет." : "Решения и задачи подтягиваются из протокола. Сформируйте протокол локальной моделью — они появятся здесь вместе с цитатами-основаниями."));
    }
    if (t.sources && t.sources.length) {
      var so = el("div", "blk"); so.appendChild(el("h4", "", "Реплики-источники"));
      t.sources.forEach(function (x) { var r = el("div", "it"); r.appendChild(goto(x)); if (x.quote) r.appendChild(el("blockquote", "", "«" + x.quote + "»")); so.appendChild(r); });
      sec.appendChild(so);
    }
    if (t.related && t.related.length) {
      var rl = el("div", "blk"); rl.appendChild(el("h4", "", "Соседние по времени темы"));
      var ch = el("div", "chips");
      t.related.forEach(function (id) { var o = topicById(id); if (o) ch.appendChild(btn(o.title, "chipbtn c-" + o.category, function () { select(o.id); })); });
      rl.appendChild(ch); sec.appendChild(rl);
    }
    return sec;
  }

  function footer() {
    var meta = STATE.meta || {}, f = el("footer", "ft");
    var bits = [];
    if (meta.model_title || meta.model) bits.push("Модель: " + (meta.model_title || meta.model));
    if (meta.started_at) bits.push("Начато: " + new Date(meta.started_at).toLocaleTimeString("ru-RU"));
    if (meta.finished_at) bits.push("Готово: " + new Date(meta.finished_at).toLocaleTimeString("ru-RU"));
    if (meta.duration_s != null) bits.push("Время: " + duration(meta.duration_s));
    if (meta.chunks != null) bits.push("Фрагментов: " + meta.chunks + ", повторов: " + (meta.retries || 0));
    f.appendChild(el("div", "", bits.join(" · ")));
    (meta.warnings || []).forEach(function (w) { f.appendChild(el("div", "warn", w)); });
    f.appendChild(el("div", "", "Темы и краткое содержание — интерпретация модели; время, участники и цитаты берутся из стенограммы. Проверьте важные выводы по первоисточнику."));
    return f;
  }

  function render() {
    hideTip();
    app.textContent = "";
    if (!STATE.data) { app.appendChild(el("p", "note", "Загрузка…")); return; }
    app.appendChild(header());
    app.appendChild(legend());
    app.appendChild(timeline());
    var grid = el("div", "grid");
    var left = el("div", "col"); left.appendChild(treemap()); left.appendChild(people());
    var right = el("div", "col"); right.appendChild(card());
    grid.appendChild(left); grid.appendChild(right);
    app.appendChild(grid);
    if (STATE.tmFill) STATE.tmFill();
    app.appendChild(footer());
    sendHeight();
  }

  var lastH = 0;
  function sendHeight() {
    var h = Math.ceil(document.body.getBoundingClientRect().height);        // высота содержимого, а не окна: иначе после роста окно уже не уменьшалось бы
    if (h !== lastH) { lastH = h; post({ type: "height", height: h }); }
  }

  function load(payload) {
    STATE.data = payload.data; STATE.meta = payload.meta || null; STATE.canEdit = !!payload.canEdit; STATE.categories = payload.categories || null;
    if (STATE.selected && !topicById(STATE.selected)) STATE.selected = null;
    render();
  }

  function boot() {
    app = document.getElementById("app");
    var embedded = document.getElementById("pg-map-data");
    if (embedded) {
      STATE.standalone = true;
      try { load(JSON.parse(embedded.textContent)); } catch (e) { app.textContent = "Не удалось прочитать данные карты."; return; }
      var h = (root.location && root.location.hash || "").slice(1);          // ссылка на тему: файл.html#<id темы>
      if (h && topicById(h)) { STATE.selected = h; render(); }
      return;
    }
    root.addEventListener("message", function (e) {
      if (e.source !== root.parent) return;
      var m = e.data;
      if (!m || m.pg !== "map") return;
      if (m.type === "data") load(m.payload);
      if (m.type === "select" && m.topicId) { STATE.selected = m.topicId; render(); }
    });
    var lastW = root.innerWidth;
    root.addEventListener("resize", function () { if (root.innerWidth !== lastW) { lastW = root.innerWidth; render(); } });     // перерисовка только при смене ширины (не при смене высоты окна)
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(sendHeight).observe(document.body);
    render();
    post({ type: "ready" });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})(typeof window !== "undefined" ? window : globalThis);
