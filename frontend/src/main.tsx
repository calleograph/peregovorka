import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { loadSite } from "./site";
import ErrorBoundary from "./components/ErrorBoundary";
import "./styles.css";
import "./design.css";

void loadSite();       // оформление установки: применяется к странице без пересборки (при недоступности сервера остаётся стандартное)

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ErrorBoundary><App /></ErrorBoundary>
    </BrowserRouter>
  </React.StrictMode>,
);
