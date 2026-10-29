import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { loadSite } from "./site";
import ErrorBoundary from "./components/ErrorBoundary";
import "./styles.css";
import "./design.css";
import "./glass.css";
import "./linkcopy.css";
import "./calendar.css";
import "./motion.css";
import { installCrystalsHotkey } from "./crystalsHotkey";
import { installMicCaptureGuard } from "./micCapture";
import { reportEvent } from "./diagnostics";

installCrystalsHotkey();
// Захват микрофона по одному запросу и с ограниченным повтором временных отказов (micCapture.ts). Успех после повтора — признак гонки
// освобождения устройства, а не чужой монопольной блокировки: такие случаи считаются отдельно («Диагностика клиентов → Микрофон по клиентам»).
installMicCaptureGuard((r) => {
  if (r.recovered) reportEvent("mic_retry_recovered", { reason: r.kind, detail: `попыток ${r.attempts}: ${r.message}` });
  else if (r.attempts > 1) reportEvent("mic_retry_failed", { reason: r.kind, detail: `попыток ${r.attempts}: ${r.message}` });
});
// Страница внутри мобильного приложения (WebView): оно само показывает разделы и заголовки, поэтому собственная навигация сайта скрывается (см. glass.css, `.in-app`)
if (/\bPeregovorkaApp\//.test(navigator.userAgent)) document.documentElement.classList.add("in-app");
void loadSite();       // оформление установки: применяется к странице без пересборки (при недоступности сервера остаётся стандартное)

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ErrorBoundary><App /></ErrorBoundary>
    </BrowserRouter>
  </React.StrictMode>,
);
