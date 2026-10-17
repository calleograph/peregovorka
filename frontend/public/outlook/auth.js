/* Окно входа надстройки Outlook (Office dialog). Страница открыта на этом же сервере, поэтому браузерная сессия, пароль, 2FA и CAPTCHA работают как обычно.
 * Нет сессии — переходим на страницу входа с возвратом сюда; есть — выпускаем токен надстройки и отдаём его окну Outlook через messageParent. */
(function () {
  "use strict";
  var statusEl = function () { return document.getElementById("status"); };

  function say(text, isErr) {
    var el = statusEl();
    if (el) { el.textContent = text; el.className = isErr ? "err" : ""; }
  }

  function done(payload) {
    try { Office.context.ui.messageParent(JSON.stringify(payload)); } catch (e) { say("Окно можно закрыть.", false); }
  }

  function run() {
    fetch("/api/v1/auth/me", { credentials: "same-origin" }).then(function (r) {
      if (r.status === 401) { location.replace("/?next=" + encodeURIComponent("/outlook/auth.html")); return null; }
      return r.json().then(function (me) {
        return fetch("/api/v1/outlook/token", { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": me.csrf_token } });
      });
    }).then(function (r) {
      if (!r) return;
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 200 && j.token) { say("Готово, возвращаемся в Outlook…", false); done({ token: j.token, expires_in: j.expires_in }); return; }
        var msg = (j.detail && (j.detail.message || j.detail)) || "Не удалось получить доступ (" + r.status + ")";
        say(String(msg), true);
        done({ error: String(msg) });
      });
    }).catch(function () { say("Нет связи с сервером.", true); done({ error: "Нет связи с сервером" }); });
  }

  if (typeof Office !== "undefined" && Office.onReady) Office.onReady(run); else run();
})();
