/* Боковая панель надстройки Outlook (кнопка «Добавить конференцию», ShowTaskpane). Логика шагов — в commands.js (PGOutlook), здесь — состояния интерфейса.
 *
 * Открытие панели: ждём Office.js → читаем встречу → проверяем вход (GET /outlook/me) → если у встречи уже есть ключ, показываем связанную конференцию.
 * Если конференции ещё нет, вход действует и время в порядке — создаём сразу: пользователь уже нажал «Добавить конференцию», второе нажатие лишнее.
 * Без действующего входа — форма «Логин / Пароль / Войти» прямо в панели (токен сразу, без окна). Окно входа — только по кнопке «Войти через окно»
 * или когда серверу нужен дополнительный шаг (2FA, CAPTCHA, смена пароля). После входа конференция создаётся сразу. Вход действует срок из настроек и продлевается.
 * Повторное нажатие безопасно: сервер возвращает ту же комнату по ключу. Во время операции кнопка заблокирована; у каждого шага свой тайм-аут.
 */
(function () {
  "use strict";
  var P = window.PGOutlook;
  var STEPS = ["meeting", "auth", "api", "invite"];
  var BUSY_LABEL = { meeting: "Проверяем встречу…", auth: "Ожидаем вход…", api: "Создаём конференцию…", invite: "Добавляем ссылку…" };
  var FATAL = { no_office: 1, office_js_missing: 1, no_item: 1, legacy_browser: 1, office_timeout: 1 };
  var st = { ready: false, busy: false, step: "", failed: "", item: null, token: null, auth: "unknown", user: "", key: null, room: null, applied: null,
    existing: false, stale: false, meeting: null, timeBad: null, err: null, fatal: false, csp: [] };

  function $(id) { return document.getElementById(id); }
  function show(el, on) { el.hidden = !on; }
  function say(text) { $("live").textContent = text; }

  /* ---------------------------------------------------------------- оформление */
  function applyTheme() {
    try {
      var th = Office.context.officeTheme;
      var hex = th && th.bodyBackgroundColor ? String(th.bodyBackgroundColor).replace("#", "") : "";
      if (hex.length === 6) {
        var r = parseInt(hex.substr(0, 2), 16), g = parseInt(hex.substr(2, 2), 16), b = parseInt(hex.substr(4, 2), 16);
        document.documentElement.setAttribute("data-theme", (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 < 0.45 ? "dark" : "light");
      }
    } catch (e) { /* тема по системе */ }
  }

  var fmtDay = null, fmtTime = null;
  try {
    fmtDay = new Intl.DateTimeFormat("ru-RU", { weekday: "short", day: "numeric", month: "short" });
    fmtTime = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });
  } catch (e) { /* без Intl — ISO-строка */ }

  function when(start, end) {
    var s = new Date(start), e = new Date(end);
    if (isNaN(s.getTime()) || isNaN(e.getTime())) return "время не указано";
    if (!fmtDay) return s.toISOString().slice(0, 16).replace("T", " ") + " – " + e.toISOString().slice(11, 16);
    var day = fmtDay.format(s).replace(/\.$/, "");
    if (s.toDateString() === e.toDateString()) return day + " · " + fmtTime.format(s) + "–" + fmtTime.format(e);
    return day + " " + fmtTime.format(s) + " – " + fmtDay.format(e).replace(/\.$/, "") + " " + fmtTime.format(e);
  }

  /* ---------------------------------------------------------------- отрисовка */
  function renderMeeting() {
    var m = st.meeting;
    if (!m) return;
    var subj = $("subj");
    subj.textContent = m.subject ? m.subject : "Без темы";
    subj.className = "meet-subj" + (m.subject ? "" : " empty");
    $("when").textContent = when(m.start, m.end);
    $("when-row").className = "meet-when" + (st.timeBad ? " bad" : "");
    show($("series"), !!m.recurring);
  }

  function renderAuth() {
    var box = $("acct"), text = $("acct-text");
    box.setAttribute("data-auth", st.auth);
    text.textContent = "";
    if (st.auth === "ok") {
      text.appendChild(document.createTextNode("Вы вошли: "));
      var b = document.createElement("b");
      b.textContent = st.user || "учётная запись Peregovorka";
      text.appendChild(b);
    } else {
      text.textContent = st.auth === "expired" ? "Срок входа истёк — войдите снова" : st.auth === "none" ? "Вход в Peregovorka не выполнен" : "Проверяем вход…";
    }
    show($("logout"), st.auth === "ok" && !st.busy);
  }

  function renderSteps() {
    var on = (st.busy && st.step !== "auth") || !!st.failed;
    show($("steps"), on);
    if (!on) return;
    var idx = STEPS.indexOf(st.failed || st.step);
    var items = $("steps").children;
    for (var i = 0; i < items.length; i++) {
      var s = i < idx ? "done" : i === idx ? (st.failed ? "fail" : "active") : "";
      if (s) items[i].setAttribute("data-s", s); else items[i].removeAttribute("data-s");
    }
  }

  function renderLogin() {
    var on = st.ready && !st.fatal && st.auth !== "ok";
    show($("login"), on);
    if (!on) return;
    var busy = st.busy && st.step === "auth";
    $("lg-go").disabled = st.busy;
    $("lg-go").setAttribute("aria-busy", busy ? "true" : "false");
    $("lg-label").textContent = busy ? "Входим…" : "Войти";
    $("lg-window").disabled = st.busy;
    $("lg-window").className = "linkbtn" + (st.err && st.err.code === "browser_required" ? " strong" : "");
    $("lg-window").textContent = st.err && st.err.code === "browser_required" ? "Продолжить вход в окне" : "Войти через окно";
  }

  function renderButton() {
    var btn = $("go"), label;
    var partial = st.room && !st.applied && st.err && st.err.code === "invite_failed";
    if (st.busy) label = BUSY_LABEL[st.step] || "Выполняется…";
    else if (!st.ready) label = "Подготовка…";
    else if (partial) label = "Вставить ссылку ещё раз";
    else if (st.err && st.err.kind === "auth") label = "Войти и создать конференцию";
    else if (st.room) label = "Обновить конференцию";
    else if (st.auth !== "ok") label = "Войти и создать конференцию";
    else if (st.err && st.err.kind !== "input") label = "Попробовать снова";
    else label = "Создать конференцию";
    $("go-label").textContent = label;
    btn.disabled = st.busy || !st.ready || st.fatal;
    btn.setAttribute("aria-busy", st.busy ? "true" : "false");
    btn.className = "btn-main" + (st.room && st.applied && !st.busy && !st.stale ? " secondary" : "");
    show(btn, !st.fatal && st.auth === "ok");
  }

  function renderMsg() {
    var x = st.err;
    show($("msg"), !!x);
    show($("tech"), !!x || st.csp.length > 0);
    if (!x) return;
    $("msg").setAttribute("data-kind", x.kind);
    $("msg").setAttribute("role", x.kind === "info" ? "status" : "alert");
    $("msg-title").textContent = x.title;
    $("msg-text").textContent = x.text;
    var dl = $("tech-list");
    dl.textContent = "";
    var rows = [["Код", x.code], ["Этап", x.stage || "—"], ["Сведения", x.detail || "—"], ["Клиент", st.client || "—"], ["Время", new Date().toLocaleString("ru-RU")]];
    if (st.csp.length) rows.push(["CSP", st.csp.slice(0, 3).join("; ")]);
    for (var i = 0; i < rows.length; i++) {
      var dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = rows[i][0];
      dd.textContent = String(rows[i][1]);
      dl.appendChild(dt);
      dl.appendChild(dd);
    }
  }

  function renderResult() {
    var room = st.room;
    show($("result"), !!room && !st.busy);
    if (!room) return;
    var partial = !st.applied;
    $("result").className = "result" + (partial ? " partial" : "");
    $("result-title").lastChild.textContent = partial ? (st.existing ? "Ссылки нет в приглашении" : "Конференция создана, ссылка не вставлена")
      : st.existing ? "Конференция уже добавлена" : "Конференция в приглашении";
    var link = $("link");
    link.href = room.url;
    link.textContent = room.url.replace(/^https?:\/\//, "");
    link.title = room.url;
    $("open").href = room.url;
    $("open-text").href = room.url;
    var a = st.applied || { body: false, location: false };
    check($("chk-body"), a.body, a.body ? "Ссылка в тексте приглашения" : "В тексте приглашения ссылки нет");
    show($("chk-loc"), a.location !== null);
    check($("chk-loc"), !!a.location, a.location ? "Ссылка в поле «Место»" : "Поле «Место» этот Outlook изменить не дал");
    $("hint").textContent = st.stale ? "Время или тема встречи изменились — нажмите «Обновить конференцию»."
      : partial ? "Скопируйте ссылку вручную или вставьте её ещё раз — новая комната не создастся."
      : room.recurring ? "Ссылка постоянная для всей серии. Отправьте приглашение — участники получат её в письме."
      : room.opens_at && fmtTime ? "Вход откроется в " + fmtTime.format(new Date(room.opens_at)) + ". Отправьте приглашение — участники получат ссылку."
      : "Отправьте приглашение — участники получат ссылку.";
  }

  function check(li, ok, text) {
    li.className = ok ? "" : "off";
    li.querySelector("use").setAttribute("href", ok ? "#i-check" : "#i-dash");
    li.querySelector("span").textContent = text;
  }

  function render() {
    document.body.setAttribute("data-state", st.busy ? "busy" : st.err ? "error" : st.room ? "done" : st.ready ? "ready" : "loading");
    renderMeeting();
    renderAuth();
    renderLogin();
    renderSteps();
    renderButton();
    renderMsg();
    renderResult();
  }

  /* ---------------------------------------------------------------- действия */
  function setStep(name) { st.step = name; render(); say(BUSY_LABEL[name] || ""); }

  function fail(e, token) {
    var x = P.explain(e);
    st.err = x;
    st.failed = STEPS.indexOf(x.stage) !== -1 ? x.stage : (st.step || "");
    if (FATAL[x.code]) st.fatal = true;
    if (x.code === "auth_expired") { P.clearToken(P.getStorage()); st.token = null; st.auth = "expired"; }
    if (x.code === "auth_cancelled" || x.code === "auth_required") st.failed = "";
    if (x.code !== "auth_cancelled") P.report(token || st.token, x.stage, x.code, x.detail || x.text);
    say(x.title + ". " + x.text);
  }

  function signedIn(token) {
    st.token = token;
    st.auth = "ok";
    P.whoami(token).then(function (me) { st.user = me && me.name ? me.name : ""; renderAuth(); }, function () { /* имя не обязательно */ });
  }

  function run() {
    if (st.busy || !st.ready || st.fatal) return;
    var token = P.readToken(P.getStorage(), Date.now());
    if (!token) {                                                       // вход истёк, пока панель была открыта: форма входа
      st.token = null;
      st.auth = P.hadToken(P.getStorage()) ? "expired" : "none";
      render();
      focusLogin();
      return;
    }
    st.busy = true;
    st.err = null;
    st.failed = "";
    st.stale = false;
    setStep("meeting");
    P.prepare(st.item).then(function (prep) {
      st.key = prep.key;
      st.meeting = { subject: prep.payload.subject, start: prep.payload.start, end: prep.payload.end, recurring: prep.payload.recurring };
      st.timeBad = null;
      setStep("api");
      return P.requestRoom(prep.payload, token);
    }).then(function (room) {
      st.room = room;
      st.existing = !room.created && st.existing;
      st.applied = null;
      setStep("invite");
      return P.applyInvite(st.item, room).then(function (a) { st.applied = a; });
    }).then(function () {
      st.busy = false;
      st.step = "";
      say(st.existing ? "Конференция обновлена." : "Конференция создана. Ссылка добавлена в приглашение.");
      P.report(token, "done", "ok", (st.existing ? "обновлена" : "создана") + "; место: " + (st.applied.location ? "да" : st.applied.location === false ? "нет" : "—"));
      render();
    }, function (e) {
      st.busy = false;
      if (e && /^(no_time|bad_time|in_past|too_long|too_far)$/.test(e.code)) st.timeBad = e.code;
      fail(e, token);
      render();
      if (e && e.code === "auth_expired") focusLogin();
    });
  }

  function focusLogin() {
    try { var u = $("lg-user"); (u.value ? $("lg-pass") : u).focus(); } catch (e) { /* не критично */ }
  }

  /** Вход выполнен (в панели или в окне): если конференции ещё нет и время встречи в порядке — создаём сразу, пользователь уже выразил это намерение. */
  function afterLogin(token) {
    st.busy = false;
    st.step = "";
    st.err = null;
    signedIn(token);
    $("lg-pass").value = "";
    render();
    if (!st.room && !st.timeBad) run();
  }

  function loginFailed(e) {
    st.busy = false;
    st.step = "";
    fail(e);
    st.failed = "";
    render();
    if (e && (e.code === "wrong_password" || e.code === "no_credentials")) { try { $("lg-pass").select(); } catch (x) { /* не критично */ } }
  }

  function passwordLogin(ev) {
    if (ev) ev.preventDefault();
    if (st.busy) return;
    st.busy = true;
    st.err = null;
    st.step = "auth";
    render();
    say("Входим…");
    P.passwordLogin($("lg-user").value.trim(), $("lg-pass").value).then(afterLogin, loginFailed);
  }

  function windowLogin() {
    if (st.busy) return;
    st.busy = true;
    st.err = null;
    st.step = "auth";
    render();
    P.login("browser").then(afterLogin, loginFailed);     // окно открывается по нажатию: браузер Outlook не блокирует его; нужен доп. шаг — сразу основное приложение
  }

  function logout() {
    var t = st.token;
    st.token = null;
    st.auth = "none";
    st.user = "";
    render();
    P.logout(t);
  }

  function copy() {
    var url = st.room && st.room.url;
    if (!url) return;
    var mark = function () { var b = $("copy"); b.classList.add("copied"); say("Ссылка скопирована"); setTimeout(function () { b.classList.remove("copied"); }, 1500); };
    var legacy = function () {
      var ta = document.createElement("textarea");
      ta.value = url;
      ta.setAttribute("readonly", "");
      ta.className = "sr";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      if (ok) mark(); else say("Не удалось скопировать: выделите ссылку вручную");
    };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(mark, legacy);
      else legacy();
    } catch (e) { legacy(); }
  }

  /* ---------------------------------------------------------------- открытие панели */
  /** Перечитать встречу (смена времени в форме, возврат фокуса в панель): показываем актуальные тему и время и проверяем их до запроса. */
  function refreshMeeting() {
    if (!st.item) return Promise.resolve();
    return P.readMeeting(st.item).then(function (m) {
      var before = st.meeting;
      st.meeting = m;
      st.timeBad = P.checkTimes(m.start, m.end, m.recurring, Date.now());
      if (st.room && before && (String(before.start) !== String(m.start) || String(before.end) !== String(m.end) || before.subject !== m.subject)) st.stale = true;
      if (!st.busy) {
        if (st.timeBad && (!st.err || st.err.kind === "input")) { st.err = P.explain(P.PgError(st.timeBad, "meeting")); st.failed = ""; }
        else if (!st.timeBad && st.err && st.err.kind === "input") st.err = null;
      }
      render();
    }, function () { /* сведения о встрече обновятся при нажатии кнопки */ });
  }

  function watch() {
    try {
      if (P.supports("Mailbox", "1.7") && st.item.addHandlerAsync) {
        st.item.addHandlerAsync(Office.EventType.AppointmentTimeChanged, refreshMeeting);
        st.item.addHandlerAsync(Office.EventType.RecurrenceChanged, refreshMeeting);
      }
    } catch (e) { /* старый клиент: обновим при фокусе */ }
    window.addEventListener("focus", refreshMeeting);
  }

  function checkAuth() {
    var token = P.readToken(P.getStorage(), Date.now());
    if (!token) { st.auth = P.hadToken(P.getStorage()) ? "expired" : "none"; return Promise.resolve(); }
    return P.whoami(token).then(function (me) {
      st.token = token;
      st.auth = "ok";
      st.user = me && me.name ? me.name : "";
      if (me && me.enabled === false) throw P.PgError("disabled", "auth");
      if (P.needsRefresh(P.getStorage(), Date.now())) P.refresh(token).then(function (t) { st.token = t; });      // продление в фоне: вход не прерывается
    }, function (e) {
      if (e.code === "auth_expired") { P.clearToken(P.getStorage()); st.auth = "expired"; return; }
      st.token = token;                                                   // сервер недоступен: токен оставляем, ошибку покажем
      st.auth = "ok";
      throw e;
    });
  }

  /** Связанная конференция по ключу встречи (повторное открытие): ключ только читаем, новый не создаём. */
  function loadExisting() {
    return new Promise(function (resolve) {
      try {
        st.item.loadCustomPropertiesAsync(function (res) {
          st.key = res && res.status === Office.AsyncResultStatus.Succeeded ? res.value.get("pg_key") || null : null;
          resolve();
        });
      } catch (e) { resolve(); }
    }).then(function () {
      if (!st.key || !st.token) return null;
      return P.findRoom(st.key, st.token).then(function (room) {
        if (!room) return null;
        st.room = room;
        st.existing = true;
        return new Promise(function (resolve) {
          try {
            st.item.body.getAsync("html", function (res) {
              var html = res && res.status === Office.AsyncResultStatus.Succeeded ? res.value : "";
              st.applied = P.hasBlock(html) ? { body: true, location: null } : null;
              if (!st.applied) st.err = { code: "invite_failed", stage: "invite", kind: "partial", detail: "блока со ссылкой нет в тексте встречи", title: "Ссылки нет в приглашении",
                text: "Конференция уже создана, но в тексте встречи её ссылки нет. Вставьте её ещё раз — новая комната не создастся." };
              resolve();
            });
          } catch (e) { st.applied = { body: true, location: null }; resolve(); }
        });
      }, function () { return null; });
    });
  }

  function init() {
    $("go").addEventListener("click", run);
    $("login").addEventListener("submit", passwordLogin);
    $("lg-window").addEventListener("click", windowLogin);
    $("logout").addEventListener("click", logout);
    $("copy").addEventListener("click", copy);
    document.addEventListener("securitypolicyviolation", function (e) {
      st.csp.push((e.violatedDirective || "") + " " + (e.blockedURI || ""));
      if (st.csp.length === 1) P.report(st.token, "csp", "csp_blocked", st.csp[0]);
    });
    if (typeof Office === "undefined") { fail(P.PgError("office_js_missing", "init")); st.ready = true; render(); return; }
    P.ready().then(function (info) {
      var B = window.PG_BOOT;                       /* сторож boot.js: запуск состоялся; среда — в журнал сервера (тип и версия клиента, без личных данных) */
      if (B) { B.ready = true; try { B.beacon("ready", { h: (info && info.host) || "", pl: (info && info.platform) || "", cl: P.clientInfo() }); } catch (e) { /* только диагностика */ } }
      applyTheme();
      try { st.client = P.clientInfo(); } catch (e) { st.client = ""; }
      st.item = P.currentItem();
      watch();
      return Promise.all([refreshMeeting(), checkAuth()]);
    }).then(function () {
      return loadExisting();
    }).then(function () {
      st.ready = true;
      render();
      if (!st.room && st.auth === "ok" && !st.timeBad && !st.err) run();
    }).then(null, function (e) {
      st.ready = true;
      if (window.PG_BOOT && !window.PG_BOOT.ready) { window.PG_BOOT.ready = true; window.PG_BOOT.beacon("init_failed", { code: (e && e.code) || "", st: (e && e.stage) || "" }); }
      fail(e);
      render();
    });
  }

  if (!P || typeof Promise === "undefined") {
    document.addEventListener("DOMContentLoaded", function () {
      var t = document.getElementById("msg-text");
      if (t) { document.getElementById("msg").hidden = false; document.getElementById("msg-title").textContent = "Устаревший Outlook"; t.textContent = "Outlook использует устаревший встроенный браузер. Обновите Outlook или воспользуйтесь Outlook в браузере."; }
      var b = document.getElementById("go"); if (b) b.hidden = true;
    });
    return;
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
