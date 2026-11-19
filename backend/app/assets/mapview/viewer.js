/* Просмотр «Карты разговора». Один файл для двух режимов:
 *   • во встроенном окне страницы встречи (iframe с sandbox без allow-same-origin): данные приходят через postMessage от страницы, действия
 *     («перейти к реплике», «сохранить правку», «скопировать итоги») уходят ей же — у самого окна нет ни cookie, ни доступа к API;
 *   • как самостоятельный HTML-файл (выгрузка): данные лежат в <script type="application/json" id="pg-map-data">, файл открывается без сервера.
 * Порядок чтения: итог встречи и показатели → темы (поиск, фильтры, шкала) → содержимое темы; отдельные режимы «Решения и задачи» и «Обзор встречи».
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
  function secondsOfDay(startedAt, tz) {
    try {
      var p = {}; new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZone: tz || undefined })
        .formatToParts(new Date(startedAt)).forEach(function (x) { p[x.type] = +x.value; });
      return (p.hour || 0) * 3600 + (p.minute || 0) * 60 + (p.second || 0);
    } catch (e) { return 0; }
  }
  function fullDate(startedAt, tz) {
    try { return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric", timeZone: tz || undefined }).format(new Date(startedAt)); }
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
      if (w >= h) {
        var cw = s / h, cy = y;
        row.forEach(function (n) { var ch = n.area / cw; out.push({ id: n.id, x: x, y: cy, w: cw, h: ch }); cy += ch; });
        x += cw; w -= cw;
      } else {
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

  /* Шкала времени: КАЖДЫЙ отрезок темы — свой участок (к теме возвращались — участков несколько); проценты от длительности, не меньше 0.4 %. */
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

  /* Деления шкалы: каждые 5/10/15/30/60/120 минут — столько, чтобы было не больше max подписей (8; на узком экране меньше, чтобы не налезали). */
  /* phase — секунды суток начала встречи: деления встают на круглое время (10:10, 10:20), а не «от начала» (10:07, 10:17). */
  function ticks(total, max, phase) {
    max = max || 8;
    var steps = [300, 600, 900, 1800, 3600, 7200, 14400], step = steps[steps.length - 1];
    for (var i = 0; i < steps.length; i++) { if (total / steps[i] <= max) { step = steps[i]; break; } }
    var out = [];
    for (var s = ((step - (phase || 0) % step) % step); s <= total; s += step) out.push(s);
    return out;
  }

  function topicSpan(t) { return [t.segments[0].start_s, t.segments[t.segments.length - 1].end_s]; }

  var KINDS = ["decisions", "tasks", "proposals", "questions"];
  function items(t, kind) { return (t && t[kind]) || []; }

  /* Подходит ли тема под поиск, категорию и участника. Поиск — по названию, сути, заметке, пунктам и именам (без учёта регистра и «ё»). */
  function norm(s) { return String(s || "").toLowerCase().replace(/ё/g, "е"); }
  function matchTopic(t, f) {
    if (f.cat && t.category !== f.cat) return false;
    if (f.person && !t.speakers.some(function (s) { return s.name === f.person; })) return false;
    var q = norm(f.q).trim();
    if (!q) return true;
    var hay = [t.title, t.ai_title, t.summary, t.note].concat(t.speakers.map(function (s) { return s.name; }));
    KINDS.forEach(function (k) { items(t, k).forEach(function (i) { hay.push(i.text, i.assignee); }); });
    var text = norm(hay.join(" \u0001 "));
    return q.split(/\s+/).every(function (w) { return text.indexOf(w) >= 0; });
  }

  function sortTopics(topics, mode) {
    var list = topics.slice();
    if (mode === "long") list.sort(function (a, b) { return b.total_s - a.total_s; });
    else if (mode === "items") list.sort(function (a, b) { return weight(b) - weight(a) || a.segments[0].start_s - b.segments[0].start_s; });
    else list.sort(function (a, b) { return a.segments[0].start_s - b.segments[0].start_s; });
    return list;
    function weight(t) { return items(t, "decisions").length * 3 + items(t, "tasks").length * 2 + items(t, "questions").length; }
  }

  /* Все решения, задачи, предложения и вопросы встречи в одном месте: [{item, topic}] по времени; topic = null — пункт вне тем. */
  function collect(data) {
    var out = { decisions: [], tasks: [], proposals: [], questions: [] };
    data.topics.forEach(function (t) { KINDS.forEach(function (k) { items(t, k).forEach(function (i) { out[k].push({ item: i, topic: t }); }); }); });
    var un = data.unassigned || {};
    KINDS.forEach(function (k) {
      (un[k] || []).forEach(function (i) { out[k].push({ item: i, topic: null }); });
      out[k].sort(function (a, b) { return (a.item.sec == null ? 1e9 : a.item.sec) - (b.item.sec == null ? 1e9 : b.item.sec); });
    });
    return out;
  }

  function counts(data) {
    var c = collect(data);
    return {
      topics: data.topics.length, returns: data.topics.filter(function (t) { return t.segments.length > 1; }).length,
      decisions: c.decisions.length, tasks: c.tasks.length, proposals: c.proposals.length, questions: c.questions.length,
      owned: c.tasks.filter(function (x) { return x.item.assignee; }).length, dated: c.tasks.filter(function (x) { return x.item.deadline; }).length
    };
  }

  /* Итог встречи: из карты (вывод модели по проверенным пунктам), у старых карт — по числу пунктов. */
  function outcome(data) {
    if (data.outcome && data.outcome.text) return data.outcome;
    var n = counts(data);
    if (!n.decisions && !n.tasks) return { text: "Решений и поручений в стенограмме не зафиксировано." + (n.questions ? " Остались открытые вопросы." : ""), basis: "code" };
    return { text: "Зафиксировано решений: " + n.decisions + ", поручений: " + n.tasks + ", открытых вопросов: " + n.questions + ".", basis: "code" };
  }

  /* Текст для «Копировать итоги»: итог, решения, задачи (ответственный и срок — как есть, неизвестное так и названо), предложения, вопросы. */
  function summaryText(data) {
    var m = data.meeting, c = collect(data), L = [];
    L.push("Карта разговора — " + m.title);
    L.push(fullDate(m.started_at, m.timezone) + ", " + wallClock(m.started_at, m.timezone, 0) + "–" + wallClock(m.started_at, m.timezone, m.duration_s) + " (" + duration(m.duration_s) + ")");
    L.push("");
    L.push("Итог: " + outcome(data).text);
    function block(title, list, fmt) {
      if (!list.length) return;
      L.push(""); L.push(title + ":");
      list.forEach(function (x, i) { L.push((i + 1) + ". " + fmt(x.item)); });
    }
    block("Решения", c.decisions, function (i) { return i.text; });
    block("Задачи", c.tasks, function (i) { return i.text + " — ответственный: " + (i.assignee || "не назван") + "; срок: " + (i.deadline || "не назван"); });
    block("Предложено, не согласовано", c.proposals, function (i) { return i.text; });
    block("Открытые вопросы", c.questions, function (i) { return i.text; });
    L.push(""); L.push("Сформировано по стенограмме; формулировки — вывод языковой модели, сверяйте с репликами.");
    return L.join("\n");
  }

  /* Последовательность категорий по времени (подряд идущие одинаковые склеиваются) — «как шла встреча». */
  function flow(topics) {
    var segs = [];
    topics.forEach(function (t) { t.segments.forEach(function (s) { segs.push({ cat: t.category, start: s.start_s, len: s.end_s - s.start_s }); }); });
    segs.sort(function (a, b) { return a.start - b.start; });
    var out = [];
    segs.forEach(function (s) { if (out.length && out[out.length - 1].cat === s.cat) out[out.length - 1].len += s.len; else out.push({ cat: s.cat, len: s.len }); });
    return out;
  }

  function relatedPairs(topics) {
    var seen = {}, out = [];
    topics.forEach(function (t) {
      (t.related || []).forEach(function (id) {
        var key = t.id < id ? t.id + "|" + id : id + "|" + t.id;
        if (!seen[key] && topics.some(function (o) { return o.id === id; })) { seen[key] = true; out.push([t.id, id]); }
      });
    });
    return out;
  }

  var util = { clockOffset: clockOffset, duration: duration, plural: plural, squarify: squarify, timelineBars: timelineBars, ticks: ticks, wallClock: wallClock, secondsOfDay: secondsOfDay,
               topicSpan: topicSpan, matchTopic: matchTopic, sortTopics: sortTopics, collect: collect, counts: counts, outcome: outcome, summaryText: summaryText,
               flow: flow, relatedPairs: relatedPairs };
  root.PgMapUtil = util;
  if (typeof document === "undefined") return;

  // ------------------------------------------------------------------ отображение
  var STATE = { data: null, meta: null, canEdit: false, standalone: false, selected: null, tab: "topic", q: "", cat: null, person: null, sort: "time", editing: false, tip: null };
  var app, UI = {};
  var TABS = [["topic", "Тема"], ["outcomes", "Решения и задачи"], ["overview", "Обзор встречи"]];

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function btn(text, cls, fn) { var b = el("button", cls || "", text); b.type = "button"; b.addEventListener("click", fn); return b; }
  function add(parent) { for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.appendChild(arguments[i]); return parent; }
  function post(msg) { if (!STATE.standalone && root.parent && root.parent !== root) { msg.pg = "map"; root.parent.postMessage(msg, "*"); } }
  function topicById(id) { return STATE.data.topics.filter(function (t) { return t.id === id; })[0] || null; }
  function catLabel(id) { var c = (STATE.categories || STATE.data.categories).filter(function (x) { return x.id === id; })[0]; return c ? c.label : id; }
  function clockAt(sec) { var m = STATE.data.meeting; return wallClock(m.started_at, m.timezone, sec); }
  function range(a, b) { return clockAt(a) + "–" + clockAt(b); }
  function narrow() { return root.innerWidth < 860; }
  function filters() { return { q: STATE.q, cat: STATE.cat, person: STATE.person }; }
  function filtering() { return !!(STATE.q.trim() || STATE.cat || STATE.person); }
  function visible() { return sortTopics(STATE.data.topics.filter(function (t) { return matchTopic(t, filters()); }), STATE.sort); }

  function tooltip(e, lines) {
    if (!STATE.tip) { STATE.tip = el("div", "tip"); document.body.appendChild(STATE.tip); }
    STATE.tip.textContent = "";
    lines.forEach(function (l, i) { if (l) STATE.tip.appendChild(el("div", i ? "" : "b", l)); });
    STATE.tip.style.display = "block";
    var x = Math.min(e.clientX + 14, document.documentElement.clientWidth - STATE.tip.offsetWidth - 8);
    STATE.tip.style.left = Math.max(4, x) + "px";
    STATE.tip.style.top = (e.clientY + 16) + "px";
  }
  function hideTip() { if (STATE.tip) STATE.tip.style.display = "none"; }
  function hover(node, fn) { node.addEventListener("mousemove", function (e) { tooltip(e, fn()); }); node.addEventListener("mouseleave", hideTip); }

  /* Время реплики: в приложении — кнопка перехода к стенограмме; в выгруженном файле — просто время (ссылки на сервер там нет). */
  function when(sec, label, segmentId) {
    if (sec == null) return null;
    var text = clockAt(sec) + (label ? " · " + label : "");
    if (STATE.standalone) return el("span", "src static", text);
    var b = btn(text, "src", function () { post({ type: "goto", sec: sec, segmentId: segmentId || null }); });
    b.title = "Открыть реплику в стенограмме";
    return b;
  }

  function select(id, opts) {
    STATE.selected = id; STATE.editing = false; STATE.tab = "topic";
    updateNav(); updateMain();
    if (opts && opts.scroll !== false && narrow()) scrollToMain();
  }
  function scrollToMain() {
    var top = UI.main.getBoundingClientRect().top + (root.scrollY || 0);
    if (STATE.standalone) root.scrollTo({ top: top - 8, behavior: "smooth" }); else post({ type: "scroll", top: top });
  }
  function scrollToNav() {
    var top = UI.nav.getBoundingClientRect().top + (root.scrollY || 0);
    if (STATE.standalone) root.scrollTo({ top: top - 8, behavior: "smooth" }); else post({ type: "scroll", top: top });
  }

  // ---------------------------------------------------------------- шапка и показатели
  function header() {
    var d = STATE.data, m = d.meeting;
    var h = el("header", "hd");
    var t = el("div", "hd-text");
    add(t, el("div", "eyebrow", "Peregovorka · материалы встречи"), el("h1", "", "Карта разговора"),
        el("p", "sub", m.title + " · " + fullDate(m.started_at, m.timezone) + " · " + range(0, m.duration_s)));
    var tools = el("div", "tools noprint");
    var dark = function () { return root.document.documentElement.getAttribute("data-gd-theme") === "dark"; };
    var tb = btn(dark() ? "Светлая тема" : "Тёмная тема", "btn", function () {
      var next = !dark();
      root.document.documentElement.setAttribute("data-gd-theme", next ? "dark" : "light");
      tb.textContent = next ? "Светлая тема" : "Тёмная тема";
      try { root.localStorage.setItem("pg-gd-theme", next ? "dark" : "light"); } catch (e) { /* без хранилища тема просто не запоминается */ }
    });
    tools.appendChild(tb);
    if (STATE.standalone) tools.appendChild(btn("Печать", "btn", function () { root.print(); }));
    var cb = btn("Копировать итоги", "btn primary", function () { copy(summaryText(d), cb); });
    tools.appendChild(cb);
    return add(h, t, tools);
  }

  function copy(text, b) {
    var done = function (ok) { var was = "Копировать итоги"; b.textContent = ok ? "Скопировано" : "Не удалось скопировать"; setTimeout(function () { b.textContent = was; }, 1800); };
    if (!STATE.standalone) { post({ type: "copy", text: text }); done(true); return; }       // в окне без своего источника буфер обмена доступен только странице
    var fallback = function () {
      var ta = el("textarea"); ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      var ok = false; try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta); done(ok);
    };
    if (root.navigator && root.navigator.clipboard && root.navigator.clipboard.writeText) root.navigator.clipboard.writeText(text).then(function () { done(true); }, fallback);
    else fallback();
  }

  function kpi() {
    var d = STATE.data, n = counts(d), o = outcome(d), m = d.meeting;
    var box = el("section", "kpi");
    box.setAttribute("aria-label", "Итог встречи");
    var major = el("div", "tile major");
    add(major, el("div", "eyebrow", "Главное за встречу"), el("p", "headline", o.text),
        el("span", "badge " + (o.basis === "ai" ? "ai" : "plain"), o.basis === "ai" ? "Вывод модели по проверенным пунктам — сверяйте с репликами" : "Собрано по числу проверенных пунктов"));
    box.appendChild(major);
    function tile(k, v, s, tip, zero) {
      var t = el("div", "tile" + (zero ? " zero" : ""));
      add(t, el("div", "k", k), el("div", "v", String(v)), el("div", "s", s));
      if (tip) t.title = tip;
      box.appendChild(t);
    }
    tile("Длительность", duration(m.duration_s), range(0, m.duration_s), "По времени встречи");
    tile("Темы", n.topics, n.returns ? n.returns + " " + plural(n.returns, "с возвратом", "с возвратом", "с возвратом") : "без возвратов", "Смысловые темы; к теме «с возвратом» обращались несколько раз");
    tile("Решения", n.decisions, n.decisions ? "подтверждены репликами" : (d.items_ready === false ? "ещё не выделены" : "не зафиксировано"), "Только принятые решения; предложения — отдельно", !n.decisions);
    tile("Задачи", n.tasks, n.tasks ? n.owned + " с ответственным · " + n.dated + " со сроком" : "не поставлено", "Ответственный и срок — только если названы в стенограмме", !n.tasks);
    tile("Открытые вопросы", n.questions, n.proposals ? n.proposals + " " + plural(n.proposals, "предложение", "предложения", "предложений") + " без согласования" : (n.questions ? "требуют решения" : "нет"), "Вопросы без окончательного ответа", !n.questions);
    tile("Участники", m.participants, "говорили на встрече", "По стенограмме");
    return box;
  }

  // ---------------------------------------------------------------- шкала
  function timeline() {
    var d = STATE.data, total = Math.max(d.meeting.duration_s, 1);
    var sec = el("section", "card tlc");
    var head = el("div", "card-h");
    add(head, el("h2", "", "Ход встречи"), el("span", "muted small noprint", "длина участка — время обсуждения; нажмите, чтобы открыть тему"));
    sec.appendChild(head);
    var bar = el("div", "tl"); bar.setAttribute("role", "group"); bar.setAttribute("aria-label", "Шкала времени встречи");
    UI.segs = [];
    timelineBars(d.topics, total).forEach(function (b) {
      var t = topicById(b.topic);
      var s = el("button", "seg c-" + t.category);
      s.type = "button";
      s.style.left = b.left + "%"; s.style.width = b.width + "%";
      s.setAttribute("aria-label", t.title + ", " + range(b.start, b.end) + ", " + duration(b.end - b.start));
      s.addEventListener("click", function () { select(t.id); });
      hover(s, function () { return [t.title, range(b.start, b.end) + " · " + duration(b.end - b.start), catLabel(t.category) + (t.segments.length > 1 ? " · возвращались к теме" : "")]; });
      UI.segs.push({ node: s, topic: t });
      bar.appendChild(s);
    });
    (d.unprocessed || []).forEach(function (u) {
      var g = el("span", "gap"); g.style.left = (u.start_s / total * 100) + "%"; g.style.width = Math.max(0.4, (u.end_s - u.start_s) / total * 100) + "%";
      hover(g, function () { return ["Участок не разобран", range(u.start_s, u.end_s), u.why || ""]; });
      bar.appendChild(g);
    });
    sec.appendChild(bar);
    var ax = el("div", "ax"); ax.setAttribute("aria-hidden", "true");
    var tk = ticks(total, Math.max(3, Math.min(8, Math.floor(root.innerWidth / 110))), secondsOfDay(d.meeting.started_at, d.meeting.timezone));
    tk.forEach(function (s, i) {
      var x = el("span", "tick" + (s < total * 0.04 ? " start" : "") + (i === tk.length - 1 && s > total - total * 0.06 ? " end" : ""), clockAt(s));
      x.style.left = (s / total * 100) + "%";
      ax.appendChild(x);
    });
    sec.appendChild(ax);
    var skipped = (d.unprocessed || []).reduce(function (a, u) { return a + (u.end_s - u.start_s); }, 0);
    var plain = Math.max(0, (d.uncovered_s || 0) - skipped);
    var notes = el("div", "tl-notes");
    if (skipped > 0) notes.appendChild(el("p", "note warn", "Не удалось разобрать " + d.unprocessed.length + " " + plural(d.unprocessed.length, "участок", "участка", "участков") + " (≈ " + duration(skipped) + ") — штриховка на шкале. Темы там могут быть пропущены; сверьте со стенограммой."));
    if (plain > 60) notes.appendChild(el("p", "note", "Светлые промежутки — время вне тем (" + duration(plain) + "): паузы, приветствия, короткие реплики."));
    if (notes.childNodes.length) sec.appendChild(notes);
    return sec;
  }

  // ---------------------------------------------------------------- навигация по темам
  function nav() {
    var d = STATE.data;
    var box = el("aside", "card nav");
    box.setAttribute("aria-label", "Темы встречи");
    var head = el("div", "card-h");
    UI.count = el("span", "muted small");
    add(head, el("h2", "", "Темы обсуждения"), UI.count);
    box.appendChild(head);
    var search = el("input", "search");
    search.type = "search"; search.placeholder = "Найти тему, решение, задачу или участника"; search.setAttribute("aria-label", "Поиск по темам"); search.value = STATE.q;
    search.addEventListener("input", function () { STATE.q = search.value; updateNav(); if (STATE.tab === "outcomes") updateMain(); });
    box.appendChild(search);
    var chips = el("div", "filters"); chips.setAttribute("role", "group"); chips.setAttribute("aria-label", "Категории");
    UI.chips = [];
    var all = btn("Все", "filter", function () { STATE.cat = null; updateNav(); refreshAfterFilter(); });
    UI.chips.push({ node: all, id: null }); chips.appendChild(all);
    d.categories.forEach(function (c) {
      var mins = d.topics.filter(function (t) { return t.category === c.id; }).length;
      var b = btn("", "filter c-" + c.id, function () { STATE.cat = STATE.cat === c.id ? null : c.id; updateNav(); refreshAfterFilter(); });
      add(b, el("i", "dot"), document.createTextNode(c.label + " "), el("span", "n", String(mins)));
      UI.chips.push({ node: b, id: c.id }); chips.appendChild(b);
    });
    box.appendChild(chips);
    var row = el("div", "selects");
    var ps = el("select"); ps.setAttribute("aria-label", "Участник");
    var o0 = el("option", "", "Все участники"); o0.value = ""; ps.appendChild(o0);
    d.speakers.forEach(function (s) { var o = el("option", "", s.name); o.value = s.name; ps.appendChild(o); });
    ps.value = STATE.person || "";
    ps.addEventListener("change", function () { STATE.person = ps.value || null; updateNav(); refreshAfterFilter(); });
    UI.person = ps;
    var ss = el("select"); ss.setAttribute("aria-label", "Порядок тем");
    [["time", "По времени"], ["long", "Сначала долгие"], ["items", "Сначала с решениями"]].forEach(function (x) { var o = el("option", "", x[1]); o.value = x[0]; ss.appendChild(o); });
    ss.value = STATE.sort;
    ss.addEventListener("change", function () { STATE.sort = ss.value; updateNav(); });
    add(row, ps, ss);
    box.appendChild(row);
    UI.reset = btn("Сбросить фильтры", "linkbtn", function () { STATE.q = ""; STATE.cat = null; STATE.person = null; search.value = ""; ps.value = ""; updateNav(); refreshAfterFilter(); });
    box.appendChild(UI.reset);
    UI.list = el("div", "topics");
    UI.list.setAttribute("role", "listbox"); UI.list.setAttribute("aria-label", "Список тем");
    UI.list.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
      var bs = Array.prototype.slice.call(UI.list.querySelectorAll(".topic"));
      if (!bs.length) return;
      e.preventDefault();
      var i = bs.indexOf(document.activeElement);
      i = e.key === "Home" ? 0 : e.key === "End" ? bs.length - 1 : Math.max(0, Math.min(bs.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)));
      select(bs[i].getAttribute("data-id"), { scroll: false });
    });
    box.appendChild(UI.list);
    return box;
  }
  function refreshAfterFilter() { if (STATE.tab !== "topic") updateMain(); }

  function topicRow(t) {
    var a = topicSpan(t);
    var b = el("button", "topic" + (STATE.selected === t.id ? " sel" : ""));
    b.type = "button"; b.setAttribute("role", "option"); b.setAttribute("aria-selected", STATE.selected === t.id ? "true" : "false"); b.setAttribute("data-id", t.id);
    b.tabIndex = STATE.selected === t.id ? 0 : -1;
    b.addEventListener("click", function () { select(t.id); });
    var body = el("span", "tb");
    add(body, el("span", "name", t.title));
    var meta = el("span", "tm");
    add(meta, el("span", "", clockAt(a[0]) + " · " + duration(t.total_s)), el("span", "cat", catLabel(t.category)),
        t.segments.length > 1 ? el("span", "ret", "↻ " + t.segments.length + " " + plural(t.segments.length, "участок", "участка", "участков")) : null);
    body.appendChild(meta);
    var tags = el("span", "tags");
    var nd = items(t, "decisions").length, nt = items(t, "tasks").length, nq = items(t, "questions").length;
    if (nd) tags.appendChild(el("span", "tag ok", nd + " " + plural(nd, "решение", "решения", "решений")));
    if (nt) tags.appendChild(el("span", "tag todo", nt + " " + plural(nt, "задача", "задачи", "задач")));
    if (nq) tags.appendChild(el("span", "tag", nq + " " + plural(nq, "вопрос", "вопроса", "вопросов")));
    if (t.edited) tags.appendChild(el("span", "tag", "изменено"));
    if (tags.childNodes.length) body.appendChild(tags);
    add(b, el("span", "stripe c-" + t.category), body);
    return b;
  }

  function updateNav() {
    var d = STATE.data, list = visible(), shown = {}, hadFocus = UI.list.contains(document.activeElement);
    list.forEach(function (t) { shown[t.id] = true; });
    UI.count.textContent = filtering() ? list.length + " из " + d.topics.length : d.topics.length + " " + plural(d.topics.length, "тема", "темы", "тем");
    UI.reset.hidden = !filtering();
    UI.chips.forEach(function (c) { var on = c.id === STATE.cat || (c.id === null && !STATE.cat); c.node.classList.toggle("on", on); c.node.setAttribute("aria-pressed", on ? "true" : "false"); });
    UI.list.textContent = "";
    if (!list.length) UI.list.appendChild(el("p", "empty", "Совпадений нет. Измените запрос или сбросьте фильтры."));
    list.forEach(function (t) { UI.list.appendChild(topicRow(t)); });
    if (!list.some(function (t) { return t.id === STATE.selected; }) && UI.list.firstChild && UI.list.firstChild.tabIndex !== undefined) UI.list.firstChild.tabIndex = 0;
    UI.segs.forEach(function (s) {
      s.node.classList.toggle("sel", s.topic.id === STATE.selected);
      s.node.classList.toggle("dim", filtering() && !shown[s.topic.id]);
    });
    var selEl = UI.list.querySelector(".topic.sel");
    if (hadFocus && selEl) selEl.focus({ preventScroll: true });                // список перерисован — фокус клавиатуры остаётся на выбранной теме
    if (selEl && selEl.scrollIntoView && UI.list.scrollHeight > UI.list.clientHeight) {
      var r = selEl.getBoundingClientRect(), lr = UI.list.getBoundingClientRect();
      if (r.top < lr.top || r.bottom > lr.bottom) UI.list.scrollTop += r.top - lr.top - 8;
    }
  }

  // ---------------------------------------------------------------- правая часть: вкладки
  function main() {
    var box = el("section", "card main");
    var tabs = el("div", "tabs noprint"); tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "Представление карты");
    UI.tabs = [];
    TABS.forEach(function (x, i) {
      var b = btn(x[1], "tab", function () { STATE.tab = x[0]; STATE.editing = false; updateMain(); });
      b.setAttribute("role", "tab"); b.id = "tab-" + x[0]; b.setAttribute("aria-controls", "tabbody");
      b.addEventListener("keydown", function (e) {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        var j = (i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length;
        STATE.tab = TABS[j][0]; STATE.editing = false; updateMain(); UI.tabs[j].focus();
      });
      UI.tabs.push(b); tabs.appendChild(b);
    });
    box.appendChild(tabs);
    UI.body = el("div", "tabbody"); UI.body.id = "tabbody"; UI.body.setAttribute("role", "tabpanel");
    box.appendChild(UI.body);
    return box;
  }

  function updateMain() {
    hideTip();
    UI.tabs.forEach(function (b, i) { var on = TABS[i][0] === STATE.tab; b.classList.toggle("on", on); b.setAttribute("aria-selected", on ? "true" : "false"); b.tabIndex = on ? 0 : -1; });
    UI.body.setAttribute("aria-labelledby", "tab-" + STATE.tab);
    UI.body.textContent = "";
    if (STATE.tab === "outcomes") UI.body.appendChild(outcomesView());
    else if (STATE.tab === "overview") { UI.body.appendChild(overviewView()); if (UI.tmFill) UI.tmFill(); }
    else UI.body.appendChild(topicView(STATE.selected && topicById(STATE.selected)));
    sendHeight();
  }

  function section(title, count) {
    var s = el("div", "section");
    var h = el("h3", "", title);
    if (count != null) h.appendChild(el("span", "cnt", String(count)));
    s.appendChild(h);
    return s;
  }

  /* Пункт: формулировка, для задачи — ответственный и срок (неизвестное так и названо), основание (время, кто сказал, цитата), тема. */
  function point(kind, it, topic, showTopic) {
    var p = el("div", "point " + kind);
    add(p, el("span", "dot"));
    var body = el("div", "pbody");
    body.appendChild(el("p", "ptext", it.text));
    if (kind === "tasks") {
      var who = el("div", "pmeta");
      add(who, el("span", it.assignee ? "" : "unk", "Ответственный: " + (it.assignee || "не назван")), el("span", it.deadline ? "" : "unk", "Срок: " + (it.deadline || "не назван")));
      body.appendChild(who);
    }
    var src = el("div", "psrc");
    add(src, el("span", "badge ok", "есть реплика-основание"), when(it.sec, it.speaker, it.segment_id));
    if (showTopic) {
      if (topic) src.appendChild(btn("Тема: " + topic.title, "linkbtn", function () { select(topic.id); }));
      else src.appendChild(el("span", "muted small", "вне выделенных тем"));
    }
    body.appendChild(src);
    if (it.quote) body.appendChild(el("blockquote", "", "«" + it.quote + "»"));
    p.appendChild(body);
    return p;
  }

  var KIND_TITLE = { decisions: "Принятые решения", tasks: "Задачи и поручения", proposals: "Предложено, но не согласовано", questions: "Открытые вопросы" };
  var KIND_HINT = {
    decisions: "Участники договорились — есть реплика, где решение принято.",
    tasks: "Конкретное действие. Ответственный и срок — только если они названы в стенограмме.",
    proposals: "Высказано как идея («можно попробовать», «предлагаю»), согласия не было. Это не решение.",
    questions: "Ответа во время встречи не получено — нужно уточнить или решить позже."
  };

  function topicView(t) {
    var d = STATE.data, box = el("article", "detail");
    if (!t) { box.appendChild(el("p", "empty", "Выберите тему слева или участок на шкале — здесь появятся её суть, решения, задачи, участники и реплики.")); return box; }
    if (narrow()) box.appendChild(btn("← К списку тем", "linkbtn back noprint", scrollToNav));
    var order = sortTopics(d.topics, "time").map(function (x) { return x.id; });
    var top = el("div", "dt-top");
    var head = el("div", "");
    add(head, el("div", "kicker c-" + t.category, catLabel(t.category) + " · тема " + (order.indexOf(t.id) + 1) + " из " + d.topics.length), el("h2", "dt-title", t.title));
    top.appendChild(head);
    if (STATE.canEdit && !STATE.standalone && !STATE.editing) top.appendChild(btn("Править", "btn small noprint", function () { STATE.editing = true; updateMain(); }));
    box.appendChild(top);
    if (t.edited && t.ai_title && t.ai_title !== t.title) box.appendChild(el("p", "note", "Название изменено пользователем. У модели было: «" + t.ai_title + "»."));
    var pills = el("div", "pills");
    add(pills, el("span", "pill", t.segments.length > 1 ? t.segments.length + " " + plural(t.segments.length, "участок", "участка", "участков") + " · с " + clockAt(t.segments[0].start_s) : range(t.segments[0].start_s, t.segments[0].end_s)),
        el("span", "pill", duration(t.total_s)), t.speakers.length ? el("span", "pill", t.speakers.length + " " + plural(t.speakers.length, "участник", "участника", "участников")) : null);
    box.appendChild(pills);
    if (STATE.editing) { box.appendChild(editor(t)); return box; }
    if (t.note) { var n = el("div", "usernote"); add(n, el("span", "badge user", "Заметка пользователя"), el("p", "", t.note)); box.appendChild(n); }
    if (t.summary) {
      var s = section("Суть обсуждения");
      add(s, el("p", "summary", t.summary), el("span", "badge ai", "Вывод языковой модели — сверяйте с репликами"));
      box.appendChild(s);
    }
    var any = false;
    ["decisions", "tasks", "proposals", "questions"].forEach(function (k) {
      var list = items(t, k);
      if (!list.length) return;
      any = true;
      var s = section(KIND_TITLE[k], list.length);
      list.forEach(function (it) { s.appendChild(point(k, it, t, false)); });
      box.appendChild(s);
    });
    if (!any) box.appendChild(el("p", "note", d.items_ready === false ? "Решения и задачи выделяются вместе с картой — переформируйте её, чтобы они появились." : "В этой теме решений, задач и открытых вопросов не выделено."));
    if (t.members && t.members.length) {
      var mm = section("Короткие темы внутри", t.members.length);
      var ul = el("ul", "members");
      t.members.forEach(function (x) { var li = el("li"); add(li, el("b", "", x.title), document.createTextNode(" · " + range(x.start_s, x.end_s) + (x.summary ? " — " + x.summary : ""))); ul.appendChild(li); });
      mm.appendChild(ul); box.appendChild(mm);
    }
    var w = section(t.segments.length > 1 ? "Когда обсуждали — возвращались к теме" : "Когда обсуждали");
    var chips = el("div", "chips");
    t.segments.forEach(function (x) { chips.appendChild(when(x.start_s, "до " + clockAt(x.end_s) + ", " + duration(x.end_s - x.start_s))); });
    w.appendChild(chips); box.appendChild(w);
    if (t.speakers.length) {
      var p = section("Кто участвовал");
      var pl = el("div", "chips");
      t.speakers.slice(0, 12).forEach(function (sp) { var c = el("span", "pill"); add(c, document.createTextNode(sp.name + " "), el("span", "muted", duration(sp.seconds))); pl.appendChild(c); });
      p.appendChild(pl); box.appendChild(p);
    }
    if (t.sources && t.sources.length) {
      var so = section("Подтверждающие реплики");
      t.sources.forEach(function (x) { var r = el("div", "quote"); add(r, x.quote ? el("p", "", "«" + x.quote + "»") : null, when(x.sec, x.speaker, x.segment_id)); so.appendChild(r); });
      box.appendChild(so);
    }
    var rel = (t.related || []).map(topicById).filter(Boolean);
    if (rel.length) {
      var rl = section("Связанные обсуждения");
      var ch = el("div", "chips");
      rel.forEach(function (o) { var b = btn("", "conn c-" + o.category, function () { select(o.id); }); add(b, el("i", "dot"), document.createTextNode(o.title + " · " + clockAt(o.segments[0].start_s))); ch.appendChild(b); });
      rl.appendChild(ch);
      rl.appendChild(el("p", "note", "Связь — общий предмет обсуждения (по смыслу названий и сути), а не соседство по времени."));
      box.appendChild(rl);
    }
    return box;
  }

  function editor(t) {
    var f = el("div", "edit noprint");            // не <form>: в окне с sandbox без allow-forms отправка формы блокируется
    var ti = el("input"); ti.value = t.title; ti.maxLength = 120; ti.setAttribute("aria-label", "Название темы");
    var cs = el("select"); cs.setAttribute("aria-label", "Категория");
    (STATE.categories || STATE.data.categories).forEach(function (c) { var o = el("option", "", c.label); o.value = c.id; if (c.id === t.category) o.selected = true; cs.appendChild(o); });
    var nt = el("textarea"); nt.value = t.note || ""; nt.maxLength = 1000; nt.rows = 3; nt.placeholder = "Заметка к теме"; nt.setAttribute("aria-label", "Заметка");
    add(f, el("label", "", "Название"), ti, el("label", "", "Категория"), cs, el("label", "", "Заметка"), nt);
    var row = el("div", "row");
    function doSave() { post({ type: "edit", topicId: t.id, patch: { title: ti.value, category: cs.value, note: nt.value } }); STATE.editing = false; }
    row.appendChild(btn("Сохранить", "btn primary", doSave));
    ti.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); doSave(); } });
    row.appendChild(btn("Отмена", "btn", function () { STATE.editing = false; updateMain(); }));
    row.appendChild(btn("Вернуть как у модели", "btn", function () { post({ type: "edit", topicId: t.id, patch: { title: "", category: t.ai_category, note: "" } }); STATE.editing = false; }));
    f.appendChild(row);
    f.appendChild(el("p", "note", "Правки хранятся отдельно от ответа модели и сохраняются при переформировании карты."));
    setTimeout(function () { if (!("ontouchstart" in root)) ti.focus(); }, 0);
    return f;
  }

  function outcomesView() {
    var d = STATE.data, c = collect(d), box = el("article", "outcomes");
    var shown = {}; visible().forEach(function (t) { shown[t.id] = true; });
    var q = norm(STATE.q).trim();
    function keep(x) {
      if (!filtering()) return true;
      if (x.topic) return !!shown[x.topic.id];
      return !STATE.cat && !STATE.person && !!q && norm(x.item.text).indexOf(q) >= 0;
    }
    add(box, el("div", "kicker", "Результат встречи"), el("h2", "dt-title", "Решения и дальнейшие действия"));
    box.appendChild(el("p", "muted", filtering() ? "Показаны пункты тем, подходящих под поиск и фильтры." : "Все результаты встречи в одном месте. Повторы между частями встречи объединены; у каждого пункта — реплика-основание."));
    var empty = true;
    ["decisions", "tasks", "proposals", "questions"].forEach(function (k) {
      var list = c[k].filter(keep);
      if (k === "proposals" && !list.length) return;
      var s = section(KIND_TITLE[k], list.length);
      s.classList.add("group-" + k);
      s.appendChild(el("p", "hint", KIND_HINT[k]));
      if (!list.length) s.appendChild(el("p", "empty small", k === "decisions" ? "Принятых решений не зафиксировано." : k === "tasks" ? "Задач не поставлено." : "Открытых вопросов нет."));
      list.forEach(function (x) { s.appendChild(point(k, x.item, x.topic, true)); empty = false; });
      box.appendChild(s);
    });
    if (empty && d.items_ready === false) box.appendChild(el("p", "note", "Эта карта построена без решений и задач. Переформируйте её — они появятся вместе с цитатами-основаниями."));
    return box;
  }

  function overviewView() {
    var d = STATE.data, box = el("article", "overview"), total = Math.max(1, d.meeting.duration_s);
    add(box, el("div", "kicker", "Общая картина"), el("h2", "dt-title", "Как прошла встреча"), el("p", "summary", outcome(d).text));
    // последовательность обсуждения по категориям
    var fl = flow(d.topics).filter(function (x) { return x.len >= 30; });
    if (fl.length > 1) {
      var fs = section("Последовательность обсуждения");
      var fw = el("div", "flow");
      fl.slice(0, 14).forEach(function (x, i) { if (i) fw.appendChild(el("b", "", "›")); var sp = el("span", "c-" + x.cat); add(sp, el("i", "dot"), document.createTextNode(catLabel(x.cat) + " · " + duration(x.len))); fw.appendChild(sp); });
      fs.appendChild(fw); box.appendChild(fs);
    }
    // время по категориям
    var by = {};
    d.topics.forEach(function (t) { by[t.category] = (by[t.category] || 0) + t.total_s; });
    var cats = Object.keys(by).sort(function (a, b) { return by[b] - by[a]; });
    var cs = section("Время по категориям");
    var bar = el("div", "stack");
    cats.forEach(function (c) { var s = el("span", "c-" + c); s.style.flex = String(by[c]); s.title = catLabel(c) + " · " + duration(by[c]); bar.appendChild(s); });
    cs.appendChild(bar);
    var rows = el("div", "rows");
    cats.forEach(function (c) {
      var n = d.topics.filter(function (t) { return t.category === c; }).length;
      var r = btn("", "crow c-" + c + (STATE.cat === c ? " on" : ""), function () { STATE.cat = STATE.cat === c ? null : c; STATE.tab = "topic"; updateNav(); var f = visible()[0]; if (f) select(f.id, { scroll: false }); else updateMain(); });
      add(r, el("i", "dot"), el("span", "cl", catLabel(c)), el("span", "muted", n + " " + plural(n, "тема", "темы", "тем")), el("b", "", duration(by[c]) + " · " + Math.round(by[c] / total * 100) + "%"));
      r.title = "Показать темы этой категории";
      rows.appendChild(r);
    });
    cs.appendChild(rows); box.appendChild(cs);
    // активность участников
    if (d.speakers.length) {
      var ps = section("Активность участников");
      var max = Math.max.apply(null, d.speakers.map(function (s) { return s.seconds; }).concat([1]));
      var pl = el("div", "people");
      d.speakers.forEach(function (s) {
        var r = btn("", "person" + (STATE.person === s.name ? " on" : ""), function () { STATE.person = STATE.person === s.name ? null : s.name; UI.person.value = STATE.person || ""; STATE.tab = "topic"; updateNav(); var f = visible()[0]; if (f) select(f.id, { scroll: false }); else updateMain(); });
        var pb = el("span", "pbar"); var i = el("i"); i.style.width = (s.seconds / max * 100) + "%"; pb.appendChild(i);
        add(r, el("span", "pn", s.name), pb, el("span", "pt", duration(s.seconds) + " · " + Math.round((s.share || 0) * 100) + "%"));
        r.title = "Показать темы, где говорил участник";
        pl.appendChild(r);
      });
      ps.appendChild(pl);
      ps.appendChild(el("p", "note", "Доля — по длительности реплик в стенограмме. Количество и длительность реплик не являются оценкой полезности участия или вклада человека."));
      box.appendChild(ps);
    }
    // связи
    var pairs = relatedPairs(d.topics);
    if (pairs.length) {
      var rs = section("Связи между обсуждениями", pairs.length);
      var ul = el("div", "pairs");
      pairs.slice(0, 16).forEach(function (p) {
        var a = topicById(p[0]), b = topicById(p[1]);
        var r = el("div", "pair");
        add(r, btn(a.title, "linkbtn", function () { select(a.id); }), el("span", "muted", "↔"), btn(b.title, "linkbtn", function () { select(b.id); }));
        ul.appendChild(r);
      });
      rs.appendChild(ul); box.appendChild(rs);
    }
    // площадь тем (вспомогательно)
    var ts = section("Площадь тем по времени");
    ts.appendChild(el("p", "hint", "Площадь блока отражает продолжительность обсуждения. Для чтения удобнее список тем слева; здесь видно соотношение."));
    var tm = el("div", "tm");
    ts.appendChild(tm); box.appendChild(ts);
    UI.tmFill = function () {
      var W = Math.max(240, tm.clientWidth), H = W < 520 ? 260 : 300;
      tm.style.height = H + "px"; tm.textContent = "";
      squarify(d.topics.map(function (t) { return { id: t.id, value: t.total_s }; }), W, H).forEach(function (r) {
        var t = topicById(r.id);
        var c = btn("", "cell c-" + t.category + (STATE.selected === t.id ? " sel" : ""), function () { select(t.id); });
        c.style.left = (r.x / W * 100) + "%"; c.style.top = (r.y / H * 100) + "%"; c.style.width = (r.w / W * 100) + "%"; c.style.height = (r.h / H * 100) + "%";
        c.setAttribute("aria-label", t.title + ", " + duration(t.total_s));
        var inner = el("span", "in");
        if (r.w >= 120 && r.h >= 40) inner.appendChild(el("span", "ct", t.title));
        if (r.w > 140 && r.h > 66) inner.appendChild(el("span", "cm", duration(t.total_s)));
        c.appendChild(inner);
        hover(c, function () { return [t.title, duration(t.total_s), catLabel(t.category)]; });
        tm.appendChild(c);
      });
    };
    // достоверность
    var meta = STATE.meta || {};
    var q = section("Достоверность");
    var ql = el("ul", "facts");
    add(ql, el("li", "", "Время тем, участники и цитаты взяты из стенограммы; названия тем, суть и итог — вывод языковой модели."),
        el("li", "", "Решение, задача, предложение и вопрос попадают на карту, только если найдена реплика-основание; ответственный и срок не додумываются."),
        meta.coverage_pct != null ? el("li", "", "Разобрано стенограммы: " + meta.coverage_pct + "%.") : null,
        meta.anonymized ? el("li", "", "Карта построена по обезличенной стенограмме: имена и другие персональные данные заменены обозначениями.") : null);
    q.appendChild(ql); box.appendChild(q);
    return box;
  }

  function footer() {
    var meta = STATE.meta || {}, f = el("footer", "ft");
    (meta.warnings || []).forEach(function (w) { f.appendChild(el("div", "warn", w)); });
    f.appendChild(el("div", "", "Карта — аналитическое представление стенограммы, а не протокол. Важное сверяйте с подтверждающими репликами."));
    return f;
  }

  /* Печать: вместо интерактивной части — итог, все результаты и все темы подряд (собирается перед печатью и убирается после). */
  function printAll() {
    var box = el("div", "printall");
    box.appendChild(outcomesView());
    sortTopics(STATE.data.topics, "time").forEach(function (t) { var a = topicView(t); a.classList.add("ptopic"); box.appendChild(a); });
    return box;
  }

  function render() {
    hideTip();
    app.textContent = "";
    if (!STATE.data) { app.appendChild(el("p", "note", "Загрузка…")); return; }
    if (!STATE.selected || !topicById(STATE.selected)) { var f = sortTopics(STATE.data.topics, "time")[0]; STATE.selected = f ? f.id : null; }
    add(app, header(), kpi(), timeline());
    var lay = el("div", "layout");
    UI.nav = nav(); UI.main = main();
    add(lay, UI.nav, UI.main);
    app.appendChild(lay);
    app.appendChild(footer());
    updateNav(); updateMain();
  }

  var lastH = 0;
  function sendHeight() {
    var h = Math.ceil(document.body.getBoundingClientRect().height);
    if (h !== lastH) { lastH = h; post({ type: "height", height: h }); }
  }

  function load(payload) {
    var keepTab = STATE.data ? STATE.tab : "topic";
    STATE.data = payload.data; STATE.meta = payload.meta || null; STATE.canEdit = !!payload.canEdit; STATE.categories = payload.categories || null;
    STATE.data.topics.forEach(function (t) { t.speakers = t.speakers || []; t.segments = t.segments && t.segments.length ? t.segments : [{ start_s: 0, end_s: t.total_s || 0 }]; });
    STATE.data.speakers = STATE.data.speakers || [];
    STATE.data.categories = STATE.data.categories || [];
    STATE.tab = keepTab; STATE.editing = false;
    render();
  }

  function boot() {
    app = document.getElementById("app");
    try { if (root.localStorage.getItem("pg-gd-theme") === "dark") root.document.documentElement.setAttribute("data-gd-theme", "dark"); } catch (e) { /* хранилища нет — остаётся светлая тема */ }
    var printBox = null;
    root.addEventListener("beforeprint", function () { if (STATE.data && !printBox) { printBox = printAll(); app.insertBefore(printBox, app.querySelector(".ft")); } });
    root.addEventListener("afterprint", function () { if (printBox && printBox.parentNode) printBox.parentNode.removeChild(printBox); printBox = null; });
    var embedded = document.getElementById("pg-map-data");
    if (embedded) {
      STATE.standalone = true;
      document.documentElement.classList.add("standalone");
      try { load(JSON.parse(embedded.textContent)); } catch (e) { app.textContent = "Не удалось прочитать данные карты."; return; }
      var h = (root.location && root.location.hash || "").slice(1);          // ссылка на тему: файл.html#<id темы>
      if (h && topicById(h)) select(h, { scroll: false });
    } else {
      root.addEventListener("message", function (e) {
        if (e.source !== root.parent) return;
        var m = e.data;
        if (!m || m.pg !== "map") return;
        if (m.type === "data") load(m.payload);
        if (m.type === "select" && m.topicId && STATE.data && topicById(m.topicId)) select(m.topicId, { scroll: false });
      });
      if (typeof ResizeObserver !== "undefined") new ResizeObserver(sendHeight).observe(document.body);
      render();
      post({ type: "ready" });
    }
    var lastW = root.innerWidth;
    root.addEventListener("resize", function () {                            // только при смене ширины (не высоты — экранная клавиатура телефона): подписи шкалы, площадь тем, «к списку»
      if (root.innerWidth === lastW || !STATE.data) return;
      lastW = root.innerWidth;
      render();
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})(typeof window !== "undefined" ? window : globalThis);
