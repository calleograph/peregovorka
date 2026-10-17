/* Надстройка Outlook (Office.js): команда «Добавить конференцию» в окне создания встречи.
 *
 * Выполняется в скрытом окне Outlook (function command). Порядок:
 *  1. токен надстройки из хранилища или вход в окне Office dialog (страница auth.html на этом же сервере: пароль, 2FA, CAPTCHA — как в браузере);
 *  2. ключ события — случайная строка в пользовательских свойствах встречи (переживает несохранённый черновик, повторное нажатие и удаление ссылки из текста);
 *  3. POST /api/v1/outlook/meetings: комната создаётся один раз на ключ, повторный вызов возвращает ту же комнату и обновляет время и название;
 *  4. в тело встречи вставляется блок со ссылкой между маркерами (повторное нажатие заменяет блок, а не дублирует), в «Место» пишется ссылка.
 * Любая ошибка показывается уведомлением в самом письме, встреча не блокируется.
 */
(function (root) {
  "use strict";
  var START = "<!--pg-conf-->";
  var END = "<!--/pg-conf-->";
  var TOKEN_KEY = "pg_outlook_token";
  var API = "/api/v1/outlook";

  /* --------------------------------------------------------------- чистые функции (проверяются тестами) */
  function newKey() {
    var c = root.crypto;
    if (c && typeof c.randomUUID === "function") return c.randomUUID().replace(/-/g, "") + "pg";
    var s = "";
    for (var i = 0; i < 34; i++) s += "abcdefghijklmnopqrstuvwxyz0123456789".charAt(Math.floor(Math.random() * 36));
    return s;
  }

  function wrapBlock(inviteHtml) {
    return START + inviteHtml + END;
  }

  /** Заменяет прежний блок конференции новым (или добавляет в конец): повторное нажатие не плодит дубли. */
  function replaceBlock(bodyHtml, inviteHtml) {
    var block = wrapBlock(inviteHtml);
    var re = new RegExp(START.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&") + "[\\s\\S]*?" + END.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&"));
    if (re.test(bodyHtml)) return bodyHtml.replace(re, function () { return block; });
    return (bodyHtml || "") + block;
  }

  function hasBlock(bodyHtml) {
    return (bodyHtml || "").indexOf(START) !== -1;
  }

  function toIso(d) {
    return new Date(d).toISOString();
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

  function saveToken(storage, token, expiresIn, now) {
    try { storage.setItem(TOKEN_KEY, JSON.stringify({ token: token, exp: now + expiresIn * 1000 })); } catch (e) { /* хранилище недоступно — вход придётся повторять */ }
  }

  /* --------------------------------------------------------------- работа с Office */
  function getStorage() {
    try { return root.localStorage; } catch (e) { return null; }
  }

  function asyncCall(fn) {
    return new Promise(function (resolve, reject) {
      fn(function (res) {
        if (res.status === Office.AsyncResultStatus.Succeeded) resolve(res.value);
        else reject(new Error(res.error && res.error.message ? res.error.message : "Ошибка Outlook"));
      });
    });
  }

  function login() {
    return new Promise(function (resolve, reject) {
      Office.context.ui.displayDialogAsync(root.location.origin + "/outlook/auth.html", { height: 62, width: 30, promptBeforeOpen: false }, function (res) {
        if (res.status !== Office.AsyncResultStatus.Succeeded) { reject(new Error("Не удалось открыть окно входа")); return; }
        var dlg = res.value;
        dlg.addEventHandler(Office.EventType.DialogEventReceived, function () { reject(new Error("Окно входа закрыто")); });
        dlg.addEventHandler(Office.EventType.DialogMessageReceived, function (msg) {
          dlg.close();
          try {
            var data = JSON.parse(msg.message);
            if (data.token) { saveToken(getStorage(), data.token, data.expires_in || 3600, Date.now()); resolve(data.token); }
            else reject(new Error(data.error || "Вход не выполнен"));
          } catch (e) { reject(new Error("Некорректный ответ окна входа")); }
        });
      });
    });
  }

  function api(method, path, token, body) {
    return root.fetch(API + path, {
      method: method,
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { return { status: r.status, body: j }; });
    });
  }

  function errorText(res) {
    var d = res.body && res.body.detail;
    return (d && (d.message || (typeof d === "string" ? d : ""))) || "Ошибка сервера (" + res.status + ")";
  }

  function notify(item, text) {
    try {
      item.notificationMessages.replaceAsync("pgconf", { type: "errorMessage", message: String(text).slice(0, 150) });
    } catch (e) { /* уведомления не поддерживаются этим клиентом */ }
  }

  /** Что серверу нужно знать о событии: время, тема, признак серии, организатор, часовой пояс и идентификаторы для серверной сверки (если встреча уже сохранена). */
  function collect(item, key) {
    var mb = Office.context.mailbox;
    return Promise.all([
      asyncCall(function (cb) { item.subject.getAsync(cb); }),
      asyncCall(function (cb) { item.start.getAsync(cb); }),
      asyncCall(function (cb) { item.end.getAsync(cb); }),
      item.recurrence && item.recurrence.getAsync ? asyncCall(function (cb) { item.recurrence.getAsync(cb); }).catch(function () { return null; }) : Promise.resolve(null),
      item.getItemIdAsync ? asyncCall(function (cb) { item.getItemIdAsync(cb); }).catch(function () { return null; }) : Promise.resolve(null),
    ]).then(function (v) {
      var ews = v[4] || null;
      var rest = null;
      try { if (ews && mb.convertToRestId) rest = mb.convertToRestId(ews, Office.MailboxEnums.RestVersion.v2_0); } catch (e) { rest = null; }
      var tz = "";
      try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { tz = ""; }
      var payload = { key: key, subject: v[0], start: toIso(v[1]), end: toIso(v[2]), recurring: !!v[3], tz: tz };
      try { if (mb.userProfile && mb.userProfile.emailAddress) payload.organizer = mb.userProfile.emailAddress; } catch (e) { /* не критично */ }
      if (ews) payload.ews_id = ews;
      if (rest) payload.rest_id = rest;
      try { if (item.seriesId) payload.series_id = item.seriesId; } catch (e) { /* не критично */ }
      return payload;
    });
  }

  /** Сообщение об ошибке уходит в журнал сервера (Администрирование → Microsoft Outlook), чтобы администратор видел то же, что пользователь. Сбой самого отчёта молча игнорируется. */
  function report(token, where, text) {
    if (!token) return Promise.resolve();
    return api("POST", "/log", token, { where: where, message: String(text) }).catch(function () { /* сервер недоступен */ });
  }

  function run(event) {
    var item = Office.context.mailbox.item;
    var props;
    var key;
    return asyncCall(function (cb) { item.loadCustomPropertiesAsync(cb); })
      .then(function (p) {
        props = p;
        key = props.get("pg_key");
        if (!key) { key = newKey(); props.set("pg_key", key); }
        return collect(item, key);
      })
      .then(function (payload) {
        var attempt = function (token) { return api("POST", "/meetings", token, payload); };
        var token = readToken(getStorage(), Date.now());
        var first = token ? attempt(token) : login().then(attempt);
        return first.then(function (res) {
          if (res.status === 401) return login().then(attempt);        // токен истёк или отозван
          return res;
        });
      })
      .then(function (res) {
        if (res.status !== 200) throw new Error(errorText(res));
        var m = res.body;
        return asyncCall(function (cb) { props.saveAsync(cb); })
          .then(function () { return asyncCall(function (cb) { item.body.getAsync("html", cb); }); })
          .then(function (html) { return asyncCall(function (cb) { item.body.setAsync(replaceBlock(html, m.invite_html), { coercionType: "html" }, cb); }); })
          .then(function () { return asyncCall(function (cb) { item.location.setAsync(m.url, cb); }).catch(function () { /* место не обязательно */ }); });
      })
      .then(function () { event.completed({ allowEvent: true }); })
      .catch(function (e) {
        var text = e && e.message ? e.message : e;
        notify(item, "Peregovorka: " + text);
        report(readToken(getStorage(), Date.now()), "insert", text).then(function () { event.completed({ allowEvent: false }); });
      });
  }

  /** Отправка приглашения или обновления (Smart Alerts, OnAppointmentSend): время, тема и идентификаторы события уходят на сервер. Никогда не блокирует отправку и не показывает окон
   *  (в обработчиках событий окна входа запрещены: без действующего токена ничего не делаем — токен появится при следующем нажатии кнопки). */
  function onAppointmentSendHandler(event) {
    var item = Office.context.mailbox.item;
    var token = readToken(getStorage(), Date.now());
    var done = function () { event.completed({ allowEvent: true }); };
    if (!token) { done(); return; }
    asyncCall(function (cb) { item.loadCustomPropertiesAsync(cb); })
      .then(function (props) {
        var key = props.get("pg_key");
        if (!key) return null;                                         // встреча без конференции Peregovorka
        return collect(item, key).then(function (payload) { return api("POST", "/meetings", token, payload); });
      })
      .then(function (res) { if (res && res.status !== 200 && res.status !== 401) return report(token, "send", errorText(res)); })
      .then(done, function (e) { report(token, "send", e && e.message ? e.message : e).then(done); });
  }

  /* имя функции указано в манифесте (FunctionName) */
  function insertPeregovorkaMeeting(event) { run(event); }

  var api_ = { collect: collect, newKey: newKey, wrapBlock: wrapBlock, replaceBlock: replaceBlock, hasBlock: hasBlock, toIso: toIso, readToken: readToken, saveToken: saveToken, errorText: errorText, START: START, END: END };
  if (typeof module !== "undefined" && module.exports) module.exports = api_;
  root.PGOutlook = api_;
  if (typeof Office !== "undefined" && Office.actions && Office.actions.associate) {
    /* на верхнем уровне, а не в Office.onReady: в среде обработчиков событий (отправка встречи) onReady не вызывается */
    Office.actions.associate("insertPeregovorkaMeeting", insertPeregovorkaMeeting);
    Office.actions.associate("onAppointmentSendHandler", onAppointmentSendHandler);
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
