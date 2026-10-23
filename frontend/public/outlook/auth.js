/* Окно входа надстройки Outlook (Office dialog). Страница открыта на этом же сервере, поэтому браузерная сессия, пароль, 2FA и CAPTCHA работают как обычно.
 * Нет сессии — переходим на страницу входа с возвратом сюда; есть — выпускаем токен надстройки и отдаём его панели через messageParent.
 * Ошибка уходит панели кодом (auth_unavailable, forbidden, disabled, auth_failed): текст для пользователя панель берёт из своего словаря. */
(function () {
  "use strict";
  var sent = false;

  function say(text, isErr) {
    var el = document.getElementById("status");
    if (el) { el.textContent = text; el.className = isErr ? "err" : ""; }
    var box = document.getElementById("auth");
    if (box && isErr) box.className = "auth done";
  }

  function done(payload) {
    if (sent) return;
    sent = true;
    try { Office.context.ui.messageParent(JSON.stringify(payload)); } catch (e) { say("Окно можно закрыть.", false); }
  }

  function fail(code, text) {
    say(text, true);
    done({ error: text, code: code });
  }

  /* fetch, а где его нет (Internet Explorer 11) — XMLHttpRequest с тем же видом ответа ({status, json()}) */
  function request(url, opts) {
    if (typeof window.fetch === "function") return fetch(url, opts);
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest(), h, hs = (opts && opts.headers) || {};
      x.open((opts && opts.method) || "GET", url, true);
      x.withCredentials = true;
      for (h in hs) { if (hs.hasOwnProperty(h)) x.setRequestHeader(h, hs[h]); }
      x.onload = function () {
        resolve({ status: x.status, json: function () { return new Promise(function (ok, bad) { try { ok(JSON.parse(x.responseText || "{}")); } catch (e) { bad(e); } }); } });
      };
      x.onerror = function () { reject(new Error("network")); };
      x.send((opts && opts.body) || null);
    });
  }

  function get(url, opts) {
    /* сервер может зависнуть за прокси: окно не ждёт дольше 20 с */
    return Promise.race([request(url, opts), new Promise(function (_, reject) { setTimeout(function () { reject(new Error("timeout")); }, 20000); })]);
  }

  function run() {
    get("/api/v1/auth/me", { credentials: "same-origin" }).then(function (r) {
      if (r.status === 401) { location.replace("/?next=" + encodeURIComponent("/outlook/auth.html")); return null; }
      if (r.status >= 500) throw new Error("server");
      return r.json().then(function (me) {
        say("Выдаём доступ для Outlook…", false);
        return get("/api/v1/outlook/token", { method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": me.csrf_token } });
      });
    }).then(function (r) {
      if (!r) return;
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 200 && j.token) { say("Готово, возвращаемся в Outlook…", false); done({ token: j.token, expires_in: j.expires_in }); return; }
        var msg = String((j.detail && (j.detail.message || j.detail)) || "Не удалось получить доступ.");
        fail(r.status === 403 ? "forbidden" : r.status === 404 ? "disabled" : r.status >= 500 ? "auth_unavailable" : "auth_failed", msg);
      });
    }).catch(function () { fail("auth_unavailable", "Сервер авторизации временно недоступен."); });
  }

  if (typeof Office !== "undefined" && Office.onReady) Office.onReady(run); else run();
})();
