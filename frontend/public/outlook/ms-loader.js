/* Peregovorka для Outlook: подключение Office.js. Подключается на страницах надстройки вместо прямой ссылки на CDN.
 * Написан в стиле ES3 (классический Outlook открывает страницы в Internet Explorer 11). Скрипты пишутся через document.write, чтобы выполниться по порядку
 * до остальных скриптов страницы.
 * Источник выбирается параметром ?officejs= в адресе страницы (его подставляет манифест из настройки «Источник Office.js»), по умолчанию — local:
 *   local     — Office.js с этого сервера (/outlook/assets/, кладётся в образ при сборке: frontend/scripts/vendor-officejs.mjs); если локальной копии нет —
 *               с CDN Microsoft (ms-fallback.js), так что обновление без доступа к CDN при сборке не ломает панель;
 *   cdn       — Office.js с CDN Microsoft (officeapis.public.onecdn.static.microsoft);
 *   cdn_only  — Office.js с прежнего CDN Microsoft (appsforoffice.microsoft.com), как было до 0.40.1.
 * Нужна именно сборка с officeapis…: прежняя в Outlook Web App требует MicrosoftAjax.js, а тот разбирает JSON через eval, что запрещает CSP страниц надстройки. */
(function () {
  var w = window, d = document, m = /[?&]officejs=([a-z_]+)/.exec(w.location.search || ""), mode = m ? m[1] : "local";
  if (mode !== "cdn" && mode !== "cdn_only") mode = "local";
  var CDN = "https://officeapis.public.onecdn.static.microsoft/1/office.js", CDN_OLD = "https://appsforoffice.microsoft.com/lib/1/hosted/office.js";
  w.PG_OFFICE = { mode: mode, src: "" };
  function tag(src) { d.write('<script src="' + src + '"><\/script>'); }
  if (mode === "cdn_only") { w.PG_OFFICE.src = "cdn"; tag(CDN_OLD); return; }
  if (mode === "cdn") { w.PG_OFFICE.src = "cdn"; tag(CDN); return; }
  tag("/outlook/assets/office-js/office.js");
  tag("/outlook/ms-fallback.js");
})();
