/* Выполняется сразу после локального office.js (см. ms-loader.js): если библиотеки с сервера не загрузились (нет файла в образе, прокси вернул страницу), берём Office.js с CDN Microsoft. */
(function () {
  var w = window;
  if (w.Office || !w.PG_OFFICE) { if (w.PG_OFFICE) w.PG_OFFICE.src = "local"; return; }
  w.PG_OFFICE.src = "cdn_fallback";
  document.write('<script src="https://officeapis.public.onecdn.static.microsoft/1/office.js"><\/script>');
})();
