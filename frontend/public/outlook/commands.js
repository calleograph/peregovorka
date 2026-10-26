/* Надстройка Outlook (Office.js): общее ядро боковой панели (taskpane.js) и скрытых команд.
 *
 * Кнопка «Добавить конференцию» открывает боковую панель (ShowTaskpane). Порядок работы (функция prepare → requestRoom → applyInvite):
 *  1. сведения о встрече (тема, начало, окончание, серия) и проверка времени по тем же правилам, что на сервере;
 *  2. ключ события — случайная строка в пользовательских свойствах встречи, сохраняется ДО запроса к серверу: сбой сети между созданием комнаты и ответом
 *     не приводит к дублю, повтор вернёт ту же комнату;
 *  3. POST /api/v1/outlook/meetings с токеном надстройки: комната одна на ключ. Вход — логином и паролем прямо в панели (POST /outlook/login, без сеанса браузера);
 *     окно Office dialog (страница auth.html этого же сервера) — только когда нужен дополнительный шаг: код 2FA, CAPTCHA, смена пароля. Токен живёт срок из настроек
 *     (по умолчанию 7 суток) и продлевается, когда прошла половина срока (не дольше 30 дней от входа паролем);
 *  4. в тело встречи — блок со ссылкой между маркерами (повтор заменяет блок), в «Место» — ссылка (если клиент позволяет).
 * У каждого шага свой тайм-аут: ни один шаг не ждёт бесконечно. Ошибка — объект с кодом и этапом, текст для пользователя даёт explain().
 *
 * Скрытые команды (этот же файл подключает commands.html — FunctionFile манифеста):
 *  - insertPeregovorkaMeeting — кнопка из манифестов до 0.30 (ExecuteFunction). Работает и после обновления сервера без переустановки манифеста;
 *    функция объявлена глобально (клиенты с Mailbox ≤ 1.9 ищут её по имени в window, а не через Office.actions.associate), event.completed()
 *    вызывается всегда и не ждёт отправки журнала;
 *  - onAppointmentSendHandler — Smart Alerts (OnAppointmentSend): никогда не блокирует отправку и не показывает окон.
 * Код — ES5 (без стрелочных функций и let/const): старые встроенные браузеры Outlook не должны падать на разборе файла.
 */
(function (root) {
  "use strict";
  var START = "<!--pg-conf-->";
  var END = "<!--/pg-conf-->";
  var TOKEN_KEY = "pg_outlook_token";
  var API = "/api/v1/outlook";
  var HOUR = 3600000;
  /* сколько ждать каждый шаг (мс): Office отвечает за секунды, вход человека — минуты, сервер создаёт комнату за доли секунды, но сеть бывает медленной */
  var T = { ready: 15000, office: 10000, dialogOpen: 15000, dialogWait: 300000, api: 20000, report: 5000, command: 60000 };

  /* --------------------------------------------------------------- чистые функции (проверяются тестами) */
  function newKey() {
    var c = root.crypto || root.msCrypto;              /* msCrypto — Internet Explorer 11 (классический Outlook 2016/2019) */
    if (c && typeof c.randomUUID === "function") return c.randomUUID().replace(/-/g, "") + "pg";
    var s = "", abc = "abcdefghijklmnopqrstuvwxyz0123456789", buf = null, i;
    if (c && typeof c.getRandomValues === "function" && typeof Uint8Array !== "undefined") { buf = new Uint8Array(34); c.getRandomValues(buf); }
    for (i = 0; i < 34; i++) s += abc.charAt(buf ? buf[i] % 36 : Math.floor(Math.random() * 36));
    return s;
  }

  function wrapBlock(inviteHtml) {
    return START + inviteHtml + END;
  }

  function esc(s) {
    return s.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
  }

  /** Заменяет прежний блок конференции новым (или добавляет в конец): повторное нажатие не плодит дубли, остальной текст не трогается. */
  function replaceBlock(bodyHtml, inviteHtml) {
    var block = wrapBlock(inviteHtml);
    var re = new RegExp(esc(START) + "[\\s\\S]*?" + esc(END));
    if (re.test(bodyHtml)) return bodyHtml.replace(re, function () { return block; });
    return (bodyHtml || "") + block;
  }

  function hasBlock(bodyHtml) {
    return (bodyHtml || "").indexOf(START) !== -1;
  }

  function toIso(d) {
    return new Date(d).toISOString();
  }

  function validDate(d) {
    return d instanceof Date ? !isNaN(d.getTime()) : !!d && !isNaN(new Date(d).getTime());
  }

  /** Те же правила, что services/outlook.validate_times: прошедшей считается встреча, закончившаяся больше часа назад (начавшаяся — нет). Возвращает код ошибки или null. */
  function checkTimes(start, end, recurring, now) {
    if (!validDate(start) || !validDate(end)) return "no_time";
    var s = new Date(start).getTime(), e = new Date(end).getTime();
    if (e <= s) return "bad_time";
    if (!recurring && e - s > 24 * HOUR) return "too_long";
    if (e < now - HOUR) return "in_past";
    if (s > now + 730 * 24 * HOUR) return "too_far";
    return null;
  }

  function readToken(storage, now) {
    try {
      var raw = storage && storage.getItem(TOKEN_KEY);
      if (!raw) return null;
      var t = JSON.parse(raw);
      return t && t.token && t.exp > now + 60000 ? t.token : null;
    } catch (e) {
      return null;
    }
  }

  /** Был ли вход раньше (токен есть, но истёк): панель скажет «срок истёк», а не «войдите». */
  function hadToken(storage) {
    try { return !!(storage && storage.getItem(TOKEN_KEY)); } catch (e) { return false; }
  }

  function saveToken(storage, token, expiresIn, now) {
    try { storage.setItem(TOKEN_KEY, JSON.stringify({ token: token, exp: now + expiresIn * 1000, life: expiresIn * 1000 })); } catch (e) { /* хранилище недоступно — вход придётся повторять */ }
  }

  /** Пора продлить вход: прошла половина срока токена (сам токен ещё действует). */
  function needsRefresh(storage, now) {
    try {
      var t = JSON.parse(storage.getItem(TOKEN_KEY) || "null");
      return !!(t && t.token && t.life && t.exp > now + 60000 && t.exp - now < t.life / 2);
    } catch (e) { return false; }
  }

  function clearToken(storage) {
    try { storage.removeItem(TOKEN_KEY); } catch (e) { /* нечего удалять */ }
  }

  function errorText(res) {
    var d = res.body && res.body.detail;
    return (d && (d.message || (typeof d === "string" ? d : ""))) || "Ошибка сервера (" + res.status + ")";
  }

  function PgError(code, stage, detail) {
    var e = new Error(detail || code);
    e.code = code;
    e.stage = stage || "";
    e.detail = detail || "";
    return e;
  }

  /** Ответ сервера → код ошибки. 422 несёт собственный код (in_past, bad_time, …); сообщение сервера сохраняется в detail. */
  function fromResponse(res, stage) {
    var d = res.body && res.body.detail;
    var detail = errorText(res);
    if (res.status === 401) return PgError("auth_expired", stage, detail);
    if (res.status === 403) return PgError("forbidden", stage, detail);
    if (res.status === 404) return PgError("disabled", stage, detail);
    if (res.status === 429) return PgError("rate", stage, detail);
    if (res.status === 422) return PgError(d && d.code ? String(d.code) : "invalid", stage, detail);
    return PgError(res.status >= 500 ? "server" : "unexpected", stage, detail + " [HTTP " + res.status + "]");
  }

  var TEXTS = {
    auth_required: ["Нужен вход", "Для создания конференции войдите в Peregovorka.", "auth"],
    auth_expired: ["Вход истёк", "Срок действия входа истёк. Авторизуйтесь повторно.", "auth"],
    dialog_failed: ["Окно входа не открылось", "Не удалось открыть окно авторизации. Разрешите всплывающие окна для Outlook и попробуйте снова.", "auth"],
    auth_cancelled: ["Вход отменён", "Авторизация отменена. Чтобы создать конференцию, войдите ещё раз.", "info"],
    auth_unavailable: ["Сервер входа недоступен", "Сервер авторизации временно недоступен. Повторите через минуту.", "network"],
    auth_timeout: ["Вход не завершён", "Окно входа ждало слишком долго. Попробуйте ещё раз.", "auth"],
    auth_failed: ["Вход не выполнен", "Не удалось войти в Peregovorka. Попробуйте ещё раз.", "auth"],
    wrong_password: ["Неверный логин или пароль", "Проверьте логин и пароль и попробуйте снова.", "auth"],
    throttled: ["Слишком много попыток", "Вход временно заблокирован после нескольких неудачных попыток. Подождите несколько минут.", "auth"],
    browser_required: ["Нужен ещё один шаг", "Для вашей учётной записи вход завершается в отдельном окне (код подтверждения или проверка). Нажмите «Продолжить вход в окне».", "auth"],
    account_disabled: ["Учётная запись отключена", "Ваша учётная запись отключена. Обратитесь к администратору.", "auth"],
    no_credentials: ["Введите логин и пароль", "Заполните оба поля и нажмите «Войти».", "input"],
    no_time: ["Время встречи не определено", "Не удалось определить время встречи. Проверьте дату, время и часовой пояс.", "input"],
    bad_time: ["Неверное время", "Время окончания должно быть позже времени начала.", "input"],
    in_past: ["Встреча уже прошла", "Встреча уже завершилась. Укажите актуальные дату и время, затем попробуйте снова.", "input"],
    too_long: ["Слишком длинная встреча", "Встреча дольше суток. Для длинных событий создайте серию.", "input"],
    too_far: ["Слишком далёкая дата", "Встречу нельзя запланировать больше чем на два года вперёд.", "input"],
    invalid: ["Неверные данные встречи", "Сервер не принял данные встречи. Проверьте тему, дату и время.", "input"],
    network: ["Нет связи с сервером", "Не удалось связаться с Peregovorka. Проверьте подключение к сети и повторите.", "network"],
    timeout: ["Сервер не ответил", "Peregovorka не ответила вовремя. Повторите попытку — дубль комнаты не создастся.", "network"],
    server: ["Ошибка сервера", "На сервере Peregovorka произошла ошибка. Повторите позже; если не поможет — сообщите администратору.", "server"],
    unexpected: ["Неожиданный ответ сервера", "Сервер ответил необычно. Повторите позже или сообщите администратору.", "server"],
    rate: ["Слишком часто", "Слишком много запросов подряд. Подождите минуту и повторите.", "server"],
    forbidden: ["Нет прав", "Создавать конференции из Outlook вам не разрешено. Обратитесь к администратору.", "server"],
    disabled: ["Интеграция выключена", "Администратор выключил интеграцию с Outlook в Peregovorka.", "server"],
    no_office: ["Откройте в Outlook", "Эта страница работает только внутри Outlook.", "client"],
    office_js_missing: ["Office.js не загрузился", "Не загрузилась библиотека Microsoft Office.js. Проверьте доступ к appsforoffice.microsoft.com или сообщите администратору.", "client"],
    office_timeout: ["Outlook не ответил", "Outlook не ответил вовремя. Закройте и снова откройте окно встречи.", "client"],
    office_error: ["Outlook вернул ошибку", "Outlook не выполнил действие. Закройте и снова откройте окно встречи.", "client"],
    unsupported: ["Функция недоступна", "Эта версия Outlook не поддерживает нужное действие. Обновите Outlook или воспользуйтесь Outlook в браузере.", "client"],
    legacy_browser: ["Устаревший Outlook", "Outlook использует устаревший встроенный браузер. Обновите Outlook или воспользуйтесь Outlook в браузере.", "client"],
    no_item: ["Нет встречи", "Откройте встречу в календаре Outlook и нажмите кнопку ещё раз.", "client"],
    invite_failed: ["Ссылка не вставлена", "Конференция создана, но вставить ссылку в приглашение не удалось. Вставьте её ещё раз или скопируйте вручную.", "partial"],
  };

  /** Код ошибки → { title, text, kind } для пользователя; сырой ответ сервера, HTTP-код и этап остаются в подробностях. Для 403/422 без своего текста берётся сообщение сервера. */
  function explain(err) {
    var code = err && err.code ? err.code : "unexpected";
    var t = TEXTS[code];
    if (!t) t = [TEXTS.invalid[0], err && err.detail ? String(err.detail) : TEXTS.invalid[1], "input"];
    var text = t[1];
    if (code === "forbidden" && err.detail && err.detail.indexOf("HTTP") === -1) text = String(err.detail);
    return { code: code, stage: (err && err.stage) || "", title: t[0], text: text, kind: t[2], detail: (err && err.detail) || "" };
  }

  /* --------------------------------------------------------------- ожидание с пределом */
  function timeout(p, ms, code, stage) {
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () { reject(PgError(code || "timeout", stage, "превышено время ожидания " + Math.round(ms / 1000) + " с")); }, ms);
      p.then(function (v) { clearTimeout(timer); resolve(v); }, function (e) { clearTimeout(timer); reject(e); });
    });
  }

  function asyncCall(fn, stage, ms) {
    return timeout(new Promise(function (resolve, reject) {
      try {
        fn(function (res) {
          if (res && res.status === Office.AsyncResultStatus.Succeeded) resolve(res.value);
          else reject(PgError("office_error", stage, res && res.error ? (res.error.code ? res.error.code + " " : "") + (res.error.message || "") : ""));
        });
      } catch (e) { reject(PgError("unsupported", stage, e && e.message)); }
    }), ms || T.office, "office_timeout", stage);
  }

  /* --------------------------------------------------------------- работа с Office */
  function getStorage() {
    try { return root.localStorage; } catch (e) { return null; }
  }

  function hasOffice() {
    return typeof Office !== "undefined" && !!Office.context;
  }

  /** Office.js готов к работе (onReady с пределом ожидания). */
  function ready() {
    if (typeof Office === "undefined" || !Office.onReady) return Promise.reject(PgError("no_office", "init"));
    return timeout(new Promise(function (resolve) { Office.onReady(function (info) { resolve(info || {}); }); }), T.ready, "office_timeout", "init");
  }

  /** Сведения о клиенте для журнала администратора: тип и версия Outlook, наибольшие поддерживаемые наборы Mailbox и DialogApi. Без личных данных. */
  function clientInfo() {
    var parts = [];
    try {
      var d = Office.context.diagnostics || {};
      var mb = Office.context.mailbox && Office.context.mailbox.diagnostics;
      parts.push((mb && mb.hostName) || d.host || "Outlook");
      if ((mb && mb.hostVersion) || d.version) parts.push((mb && mb.hostVersion) || d.version);
      if (d.platform) parts.push(String(d.platform));
      var req = Office.context.requirements;
      if (req && req.isSetSupported) {
        var top = "";
        for (var i = 1; i <= 15; i++) if (req.isSetSupported("Mailbox", "1." + i)) top = "1." + i;
        if (top) parts.push("Mailbox " + top);
        if (req.isSetSupported("DialogApi", "1.1")) parts.push("DialogApi 1.1");
      }
    } catch (e) { /* не критично */ }
    return parts.join(" · ").slice(0, 160);
  }

  function supports(set, ver) {
    try { return !!(Office.context.requirements && Office.context.requirements.isSetSupported(set, ver)); } catch (e) { return false; }
  }

  function currentItem() {
    var item = hasOffice() && Office.context.mailbox ? Office.context.mailbox.item : null;
    if (!item || !item.start || !item.end || !item.start.getAsync) throw PgError("no_item", "meeting");
    return item;
  }

  /** Шаблон серии из Office.js (Recurrence + SeriesTime) — простым объектом для сервера: тип, параметры, даты, время начала, длительность и пояс серии
   *  (recurrenceTimeZone: время серии задано в нём, а не в поясе браузера). Сбой — null. */
  function plainRecurrence(r) {
    if (!r || !r.recurrenceType) return null;
    try {
      var st = r.seriesTime;
      var call = function (name) { return st && typeof st[name] === "function" ? st[name]() : null; };
      var out = { type: r.recurrenceType, props: r.recurrenceProperties || {}, start_date: call("getStartDate"), end_date: call("getEndDate"),
        start_time: call("getStartTime"), duration: call("getDuration") };
      if (r.recurrenceTimeZone && r.recurrenceTimeZone.name) out.tz = String(r.recurrenceTimeZone.name);
      return out;
    } catch (e) { return null; }
  }

  /** Тема, начало, окончание, серия текущей встречи (в окне редактирования — то, что сейчас в форме, даже несохранённое). */
  function readMeeting(item) {
    return Promise.all([
      asyncCall(function (cb) { item.subject.getAsync(cb); }, "meeting"),
      asyncCall(function (cb) { item.start.getAsync(cb); }, "meeting"),
      asyncCall(function (cb) { item.end.getAsync(cb); }, "meeting"),
      item.recurrence && item.recurrence.getAsync ? asyncCall(function (cb) { item.recurrence.getAsync(cb); }, "meeting").catch(function () { return null; }) : Promise.resolve(null),
    ]).then(function (v) {
      return { subject: v[0] || "", start: v[1], end: v[2], recurring: !!v[3], recurrence: plainRecurrence(v[3]) };
    });
  }

  /** Приглашённые (обязательные и необязательные): адрес и имя — для «Предстоящих встреч» Peregovorka. В окне редактирования — то, что сейчас в форме
   *  (getAsync), в окне просмотра — готовый список. Не удалось прочитать — null: сервер прежний список тогда не меняет. */
  function readAttendees(item) {
    function one(field, optional) {
      var f = item[field];
      var map = function (list) {
        var out = [];
        for (var i = 0; list && i < list.length && out.length < 500; i++) {
          if (list[i] && list[i].emailAddress) out.push({ email: list[i].emailAddress, name: list[i].displayName || "", optional: optional });
        }
        return out;
      };
      if (!f) return Promise.resolve([]);
      if (f.getAsync) return asyncCall(function (cb) { f.getAsync(cb); }, "meeting").then(map);
      return Promise.resolve(map(f));
    }
    return Promise.all([one("requiredAttendees", false), one("optionalAttendees", true)])
      .then(function (v) { return v[0].concat(v[1]); }, function () { return null; });
  }

  /** Что серверу нужно знать о событии: время, тема, признак серии, организатор, часовой пояс, приглашённые и идентификаторы для серверной сверки (если встреча уже сохранена). */
  function collect(item, key) {
    var mb = Office.context.mailbox;
    return Promise.all([
      readMeeting(item),
      item.getItemIdAsync ? asyncCall(function (cb) { item.getItemIdAsync(cb); }, "meeting").catch(function () { return null; }) : Promise.resolve(null),
      readAttendees(item),
    ]).then(function (v) {
      var m = v[0];
      var ews = v[1] || null;
      if (!validDate(m.start) || !validDate(m.end)) throw PgError("no_time", "meeting");
      var rest = null;
      try { if (ews && mb.convertToRestId) rest = mb.convertToRestId(ews, Office.MailboxEnums.RestVersion.v2_0); } catch (e) { rest = null; }
      var tz = "";
      try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { tz = ""; }
      var payload = { key: key, subject: m.subject, start: toIso(m.start), end: toIso(m.end), recurring: m.recurring, tz: tz };
      try { if (mb.userProfile && mb.userProfile.emailAddress) payload.organizer = mb.userProfile.emailAddress; } catch (e) { /* не критично */ }
      if (ews) payload.ews_id = ews;
      if (rest) payload.rest_id = rest;
      /* одно занятие серии: Office.js отдаёт ему шаблон всей серии, поэтому сервер должен знать, что меняется только занятие */
      try { if (item.seriesId) { payload.series_id = item.seriesId; payload.instance = true; } } catch (e) { /* не критично */ }
      if (v[2]) payload.attendees = v[2];
      if (m.recurrence) payload.recurrence = m.recurrence;
      return payload;
    });
  }

  /** Ключ события из пользовательских свойств; новый ключ сохраняется сразу (до запроса к серверу). Сбой сохранения не фатален: ключ живёт в памяти до закрытия окна. */
  function loadKey(item) {
    return asyncCall(function (cb) { item.loadCustomPropertiesAsync(cb); }, "key").then(function (props) {
      var key = props.get("pg_key");
      if (key) return { props: props, key: key, isNew: false };
      key = newKey();
      props.set("pg_key", key);
      return asyncCall(function (cb) { props.saveAsync(cb); }, "key").catch(function () { return null; })
        .then(function () { return { props: props, key: key, isNew: true }; });
    });
  }

  /** Шаг 1–2: ключ, данные встречи, проверка времени. */
  function prepare(item, now) {
    return loadKey(item).then(function (k) {
      return collect(item, k.key).then(function (payload) {
        var bad = checkTimes(payload.start, payload.end, payload.recurring, now || Date.now());
        if (bad) throw PgError(bad, "meeting");
        return { props: k.props, key: k.key, isNew: k.isNew, payload: payload };
      });
    });
  }

  /** step = "browser": панель уже знает, что нужен дополнительный шаг (2FA, CAPTCHA) — окно сразу открывает основное приложение, без своей формы. */
  function login(step) {
    return new Promise(function (resolve, reject) {
      var dlg = null;
      var settled = false;
      var finish = function (fn, v) {
        if (settled) return;
        settled = true;
        clearTimeout(waitTimer);
        try { if (dlg) dlg.close(); } catch (e) { /* окно уже закрыто */ }
        fn(v);
      };
      var waitTimer = setTimeout(function () { finish(reject, PgError(dlg ? "auth_timeout" : "dialog_failed", "auth", "окно входа не ответило")); }, T.dialogOpen);
      try {
        Office.context.ui.displayDialogAsync(root.location.origin + "/outlook/auth.html" + (step ? "?step=" + step : ""), { height: 70, width: 32, promptBeforeOpen: false }, function (res) {
          if (res.status !== Office.AsyncResultStatus.Succeeded) {
            finish(reject, PgError("dialog_failed", "auth", res.error ? res.error.code + " " + res.error.message : ""));
            return;
          }
          dlg = res.value;
          clearTimeout(waitTimer);
          waitTimer = setTimeout(function () { finish(reject, PgError("auth_timeout", "auth")); }, T.dialogWait);
          dlg.addEventHandler(Office.EventType.DialogEventReceived, function (ev) {
            /* 12006 — окно закрыл пользователь; остальное — сбой загрузки страницы входа */
            finish(reject, ev && ev.error && ev.error !== 12006 ? PgError("dialog_failed", "auth", String(ev.error)) : PgError("auth_cancelled", "auth"));
          });
          dlg.addEventHandler(Office.EventType.DialogMessageReceived, function (msg) {
            var data = null;
            try { data = JSON.parse(msg.message); } catch (e) { data = null; }
            if (data && data.token) {
              saveToken(getStorage(), data.token, data.expires_in || 3600, Date.now());
              finish(resolve, data.token);
            } else {
              finish(reject, PgError((data && data.code) || "auth_failed", "auth", data && data.error ? String(data.error) : ""));
            }
          });
        });
      } catch (e) {
        finish(reject, PgError("unsupported", "auth", e && e.message));
      }
    });
  }

  /* Запрос через XMLHttpRequest — там, где нет fetch (Internet Explorer 11 в классическом Outlook 2016/2019): тот же итог {status, body}. */
  function xhr(method, url, headers, body, ms, stage) {
    return new Promise(function (resolve, reject) {
      var x = new root.XMLHttpRequest(), h;
      x.open(method, url, true);
      for (h in headers) { if (headers.hasOwnProperty(h)) x.setRequestHeader(h, headers[h]); }
      x.timeout = ms;
      x.onload = function () {
        var j = {};
        try { j = x.responseText ? JSON.parse(x.responseText) : {}; } catch (e) { j = {}; }
        resolve({ status: x.status, body: j });
      };
      x.onerror = function () { reject(PgError("network", stage, "XMLHttpRequest")); };
      x.ontimeout = function () { reject(PgError("timeout", stage)); };
      x.send(body ? JSON.stringify(body) : null);
    });
  }

  function http(method, path, token, body, ms, stage) {
    if (typeof root.fetch !== "function") {
      if (!root.XMLHttpRequest) return Promise.reject(PgError("legacy_browser", stage || "api"));
      var hx = { "Content-Type": "application/json" };
      if (token) hx.Authorization = "Bearer " + token;
      return timeout(xhr(method, API + path, hx, body, ms || T.api, stage || "api"), (ms || T.api) + 1000, "timeout", stage || "api");
    }
    var ctl = typeof AbortController === "function" ? new AbortController() : null;
    var headers = { "Content-Type": "application/json" };
    if (token) headers.Authorization = "Bearer " + token;
    var req = root.fetch(API + path, {
      method: method,
      headers: headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctl ? ctl.signal : undefined,
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { return { status: r.status, body: j }; });
    }, function (e) {
      throw PgError("network", stage || "api", e && e.message);
    });
    return timeout(req, ms || T.api, "timeout", stage || "api").catch(function (e) {
      if (e.code === "timeout" && ctl) { try { ctl.abort(); } catch (x) { /* уже завершён */ } }
      throw e;
    });
  }

  /** Шаг 3: комната на сервере (идемпотентно по ключу). 401 → auth_expired: вызывающий заново входит и повторяет. */
  function requestRoom(payload, token) {
    return http("POST", "/meetings", token, payload, T.api, "api").then(function (res) {
      if (res.status !== 200) throw fromResponse(res, "api");
      return res.body;
    });
  }

  /** Гостевая ссылка встречи: issue — выпустить, если её нет; rotate — новая (прежняя перестаёт работать); revoke — отозвать. Возвращает обновлённую встречу (с новым текстом приглашения). */
  function guestLink(roomId, action, token) {
    return http("POST", "/meetings/" + encodeURIComponent(roomId) + "/guest-link", token, { action: action }, T.api, "api").then(function (res) {
      if (res.status !== 200) throw fromResponse(res, "api");
      return res.body;
    });
  }

  /** Связанная с ключом конференция (для повторного открытия панели); null — комнаты ещё нет. */
  function findRoom(key, token) {
    return http("GET", "/meetings/by-key/" + encodeURIComponent(key), token, null, T.api, "api").then(function (res) {
      if (res.status === 404) return null;
      if (res.status !== 200) throw fromResponse(res, "api");
      return res.body;
    });
  }

  /** Вход логином и паролем в самой панели: токен сразу, без окна и без сеанса браузера. Пароль никуда не сохраняется. */
  function passwordLogin(login, password) {
    if (!login || !password) return Promise.reject(PgError("no_credentials", "auth"));
    return http("POST", "/login", null, { login: login, password: password }, T.api, "auth").then(function (res) {
      if (res.status === 200 && res.body && res.body.token) {
        saveToken(getStorage(), res.body.token, res.body.expires_in || 3600, Date.now());
        return res.body.token;
      }
      var d = (res.body && res.body.detail) || {};
      var code = d.code || "";
      if (res.status === 409 || res.status === 428 || code === "browser_required" || code === "captcha_required") throw PgError("browser_required", "auth", code + (d.reason ? " " + d.reason : ""));
      if (code === "invalid_credentials") throw PgError("wrong_password", "auth", "");
      if (code === "throttled" || res.status === 429) throw PgError("throttled", "auth", errorText(res));
      if (code === "account_disabled" || code === "account_disabled_local") throw PgError("account_disabled", "auth", errorText(res));
      if (code === "directory_unavailable" || res.status >= 500) throw PgError("auth_unavailable", "auth", errorText(res));
      if (res.status === 403) throw PgError("forbidden", "auth", errorText(res));
      if (res.status === 404) throw PgError("disabled", "auth", errorText(res));
      throw PgError("auth_failed", "auth", errorText(res) + " [HTTP " + res.status + "]");
    });
  }

  /** Продление входа (половина срока прошла): новый токен заменяет прежний. Сбой не мешает работе — токен ещё действует. */
  function refresh(token) {
    return http("POST", "/token/refresh", token, null, T.api, "auth").then(function (res) {
      if (res.status === 200 && res.body && res.body.token) { saveToken(getStorage(), res.body.token, res.body.expires_in || 3600, Date.now()); return res.body.token; }
      return token;
    }, function () { return token; });
  }

  /** Кто вошёл (проверка токена при открытии панели). */
  function whoami(token) {
    return http("GET", "/me", token, null, T.api, "auth").then(function (res) {
      if (res.status !== 200) throw fromResponse(res, "auth");
      return res.body;
    });
  }

  function logout(token) {
    clearToken(getStorage());
    if (!token) return Promise.resolve();
    return http("DELETE", "/token", token, null, T.report, "auth").catch(function () { /* токен удалён локально; на сервере истечёт сам */ });
  }

  /** Шаг 4: блок со ссылкой в тело встречи (обязательно) и ссылка в «Место» (по возможности). Ошибка тела — invite_failed: комната уже есть, её ссылку показываем. */
  function applyInvite(item, room) {
    return asyncCall(function (cb) { item.body.getAsync("html", cb); }, "invite")
      .then(function (html) { return asyncCall(function (cb) { item.body.setAsync(replaceBlock(html, room.invite_html), { coercionType: "html" }, cb); }, "invite"); })
      .then(null, function (e) { throw PgError("invite_failed", "invite", e && (e.detail || e.message)); })
      .then(function () {
        if (!item.location || !item.location.setAsync) return false;
        return asyncCall(function (cb) { item.location.setAsync(room.url, cb); }, "location").then(function () { return true; }, function () { return false; });
      })
      .then(function (loc) { return { body: true, location: loc }; });
  }

  /** Событие для журнала администратора (Администрирование → Microsoft Outlook). Ничего не ждёт: ошибка отправки игнорируется, основной сценарий не блокирует. */
  function report(token, stage, code, text) {
    if (!token) return;
    try {
      http("POST", "/log", token, { where: stage || "", code: code || "", message: String(text || code || ""), client: hasOffice() ? clientInfo() : "" }, T.report, "log").catch(function () { /* сервер журнала недоступен */ });
    } catch (e) { /* не мешаем основной операции */ }
  }

  function notify(item, text, kind) {
    try {
      item.notificationMessages.replaceAsync("pgconf", kind === "info"
        ? { type: "informationalMessage", message: String(text).slice(0, 150), icon: "icon-16", persistent: false }
        : { type: "errorMessage", message: String(text).slice(0, 150) });
    } catch (e) { /* уведомления не поддерживаются этим клиентом */ }
  }

  /** Полный сценарий для скрытой команды: токен из хранилища или окно входа; при 401 — один повторный вход. */
  function runAll(item, interactive) {
    var token = readToken(getStorage(), Date.now());
    var getToken = function () { return token ? Promise.resolve(token) : (interactive ? login() : Promise.reject(PgError("auth_required", "auth"))); };
    return prepare(item).then(function (prep) {
      return getToken().then(function (t) {
        token = t;
        return requestRoom(prep.payload, t).catch(function (e) {
          if (e.code !== "auth_expired" || !interactive) throw e;
          clearToken(getStorage());
          return login().then(function (t2) { token = t2; return requestRoom(prep.payload, t2); });
        });
      }).then(function (room) { return applyInvite(item, room).then(function (r) { return { room: room, applied: r, token: token }; }); });
    }).catch(function (e) { e.token = token; throw e; });
  }

  /* --------------------------------------------------------------- скрытые команды */
  /** Кнопка манифестов до 0.30 (ExecuteFunction). event.completed() — ровно один раз: по завершении, по ошибке или по общему пределу времени. */
  function insertPeregovorkaMeeting(event) {
    var completed = false;
    var complete = function () { if (completed) return; completed = true; clearTimeout(guard); try { event.completed(); } catch (e) { /* клиент уже закрыл команду */ } };
    var guard = setTimeout(complete, T.command);
    var item = null;
    try {
      if (typeof Promise === "undefined") throw PgError("legacy_browser", "init");
      item = currentItem();
      runAll(item, true).then(function (r) {
        notify(item, r.applied.location ? "Peregovorka: конференция добавлена в приглашение." : "Peregovorka: ссылка добавлена в приглашение (поле «Место» этот Outlook изменить не дал).", "info");
        report(r.token, "legacy_command", "legacy_manifest", "Кнопка из старого манифеста: обновите манифест надстройки (пакет установки → Install-OutlookAddin.ps1 -Replace).");
        complete();
      }, function (e) {
        var x = explain(e);
        notify(item, "Peregovorka: " + x.text);
        report(e.token, x.stage, x.code, x.detail || x.text);
        complete();
      });
    } catch (e) {
      if (item) notify(item, "Peregovorka: " + explain(e).text);
      complete();
    }
  }

  /** Отправка приглашения или обновления (Smart Alerts, OnAppointmentSend): время, тема и идентификаторы события уходят на сервер. Никогда не блокирует отправку и не показывает окон
   *  (в обработчиках событий окна входа запрещены: без действующего токена ничего не делаем — токен появится при следующем нажатии кнопки). */
  function onAppointmentSendHandler(event) {
    var completed = false;
    var done = function () { if (completed) return; completed = true; clearTimeout(guard); try { event.completed({ allowEvent: true }); } catch (e) { /* уже завершено */ } };
    var guard = setTimeout(done, T.api + T.office);
    var token = readToken(getStorage(), Date.now());
    if (!token || typeof Promise === "undefined") { done(); return; }
    var item = Office.context.mailbox.item;
    asyncCall(function (cb) { item.loadCustomPropertiesAsync(cb); }, "send")
      .then(function (props) {
        var key = props.get("pg_key");
        if (!key) return null;                                         // встреча без конференции Peregovorka
        return collect(item, key).then(function (payload) { return http("POST", "/meetings", token, payload, T.api, "send"); });
      })
      .then(function (res) { if (res && res.status !== 200 && res.status !== 401) report(token, "send", fromResponse(res, "send").code, errorText(res)); done(); },
        function (e) { report(token, "send", e && e.code, e && (e.detail || e.message)); done(); });
  }

  var api_ = {
    newKey: newKey, wrapBlock: wrapBlock, replaceBlock: replaceBlock, hasBlock: hasBlock, toIso: toIso, checkTimes: checkTimes, readToken: readToken, hadToken: hadToken,
    saveToken: saveToken, clearToken: clearToken, errorText: errorText, fromResponse: fromResponse, explain: explain, PgError: PgError, timeout: timeout, T: T,
    ready: ready, clientInfo: clientInfo, supports: supports, currentItem: currentItem, readMeeting: readMeeting, readAttendees: readAttendees, collect: collect, prepare: prepare, login: login,
    passwordLogin: passwordLogin, refresh: refresh, needsRefresh: needsRefresh,
    requestRoom: requestRoom, findRoom: findRoom, guestLink: guestLink, whoami: whoami, logout: logout, applyInvite: applyInvite, report: report, getStorage: getStorage, START: START, END: END,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api_;
  root.PGOutlook = api_;
  /* Глобальные имена — для клиентов, которые вызывают функцию команды по имени (Mailbox до 1.10, в том числе локальный Exchange 2016/2019). */
  root.insertPeregovorkaMeeting = insertPeregovorkaMeeting;
  root.onAppointmentSendHandler = onAppointmentSendHandler;
  if (typeof Office !== "undefined") {
    /* на верхнем уровне, а не в Office.onReady: в среде обработчиков событий (отправка встречи) onReady не вызывается */
    if (Office.actions && Office.actions.associate) {
      try {
        Office.actions.associate("insertPeregovorkaMeeting", insertPeregovorkaMeeting);
        Office.actions.associate("onAppointmentSendHandler", onAppointmentSendHandler);
      } catch (e) { /* клиент без associate — сработают глобальные имена */ }
    }
    /* страница функций обязана сообщить Office о готовности: без этого часть клиентов не вызывает команду и показывает «выполняется» до тайм-аута */
    if (Office.onReady) { try { Office.onReady(function () { /* готово */ }); } catch (e) { /* не в Office */ } }
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
