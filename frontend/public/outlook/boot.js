/* Peregovorka для Outlook: самодиагностика запуска. Подключается ПЕРВЫМ на страницах надстройки и написан так, чтобы выполниться в любом встроенном
 * браузере Outlook — включая Internet Explorer 11 в режиме совместимости (документ IE7): только ES3, без JSON, addEventListener и textContent.
 *  1) сразу сообщает серверу, в какой среде открыта страница (режим документа, есть ли Promise/fetch/XHR, загрузился ли Office.js);
 *  2) если Office за 10 с не инициализировал надстройку (Outlook тогда показывает «Ошибка надстройки»), пишет на странице сведения для администратора
 *     и отправляет их серверу. Так причина видна в «Администрирование → Интеграции → Microsoft Outlook → журнал», а не угадывается.
 * Сведения — только технические (без токенов, адресов почты и содержимого встречи). Маячок — картинка: работает без fetch и XHR. */
(function () {
  var w = window, d = document, t0 = new Date().getTime(), page = "";
  try { page = (d.location.pathname.match(/[a-z]+\.html/) || [""])[0].replace(".html", ""); } catch (e) { page = ""; }
  function enc(v) { return encodeURIComponent(String(v === undefined || v === null ? "" : v).substring(0, 160)); }
  function env() {
    return { dm: d.documentMode || "", p: typeof w.Promise, f: typeof w.fetch, x: typeof w.XMLHttpRequest,
      o: typeof w.Office, oc: (w.Office && w.Office.context) ? 1 : 0, sys: typeof w.Sys, os: w.PG_OFFICE ? w.PG_OFFICE.mode + ":" + w.PG_OFFICE.src : "", ls: (function () { try { return w.localStorage ? 1 : 0; } catch (e) { return 0; } })() };
  }
  function beacon(e, extra) {
    try {
      var k, s = "e=" + enc(e) + "&pg=" + enc(page) + "&ms=" + (new Date().getTime() - t0), v = env();
      for (k in v) { if (v.hasOwnProperty(k)) s += "&" + k + "=" + enc(v[k]); }
      if (extra) { for (k in extra) { if (extra.hasOwnProperty(k)) s += "&" + k + "=" + enc(extra[k]); } }
      var img = new Image();
      img.src = "/api/v1/outlook/diag?" + s + "&r=" + Math.floor(Math.random() * 1e9);
      w.PG_BEACONS = w.PG_BEACONS || []; w.PG_BEACONS.push(img);                    // ссылка держится, пока запрос не ушёл
    } catch (e) { /* диагностика не должна ломать страницу */ }
  }
  function say(text) {
    try {
      var box = d.getElementById("pg-diag");
      if (!box) { box = d.createElement("div"); box.id = "pg-diag"; box.className = "pg-diag"; (d.body || d.documentElement).appendChild(box); }
      while (box.firstChild) box.removeChild(box.firstChild);
      box.appendChild(d.createTextNode(text));
      box.style.display = "block";
    } catch (e) { /* нет документа */ }
  }
  w.PG_BOOT = { t0: t0, beacon: beacon, env: env, say: say, ready: false };
  beacon("boot");
  if (page === "commands") return;              // страница функций невидима: сторож нужен только видимым страницам
  setTimeout(function () {
    if (w.PG_BOOT.ready) return;
    var v = env(), why;
    if (v.dm && v.dm < 9) why = "Outlook открыл страницу в режиме совместимости Internet Explorer " + v.dm + " (нужен режим IE 11 или новее).";
    else if (v.o === "undefined") why = "Библиотека Office.js не загрузилась (" + (v.os || "источник неизвестен") + "): нет файлов на сервере и нет доступа к CDN Microsoft с этого компьютера, либо их блокирует прокси.";
    else if (!v.oc) why = "Office.js загружена, но Outlook не передал странице контекст надстройки. В Outlook Web App локального Exchange при первом запуске браузер спрашивает, доверять ли домену Outlook: ответьте «ОК» и откройте панель заново.";
    else why = "Office инициализирован, но страница не завершила запуск.";
    beacon("init_timeout", { why: why });
    say("Peregovorka не смогла запуститься. " + why + " Сведения для администратора: режим документа " + (v.dm || "стандартный") +
        ", Promise: " + v.p + ", fetch: " + v.f + ", Office.js: " + (v.o === "undefined" ? "не загружена" : "загружена") + ". Эти сведения уже отправлены на сервер (журнал Outlook в администрировании).");
  }, 10000);
})();
