import { useEffect, useState } from "react";
import { Modal } from "./Dialogs";

export const PROJECT_URL = "https://github.com/calleograph/peregovorka";
export const CHANGELOG_URL = `${PROJECT_URL}/blob/main/CHANGELOG.md`;

export interface BuildInfo { version: string; commit: string; built_at?: string }
const short = (c?: string | null) => (c && c !== "unknown" ? c.slice(0, 7) : "");

/** Название и версия продукта мелким шрифтом. На входе: «Peregovorka 0.7.0 · Проект»; commit — только во всплывающей подсказке. */
export function LoginFooter({ info }: { info: BuildInfo | null }) {
  return (
    <footer className="login-foot">
      <span title={info && short(info.commit) ? `сборка ${short(info.commit)}${info.built_at ? ` · ${info.built_at}` : ""}` : undefined}>Peregovorka{info?.version ? ` ${info.version}` : ""}</span>
      <span aria-hidden> · </span>
      <a href={PROJECT_URL} target="_blank" rel="noopener noreferrer">Проект</a>
    </footer>
  );
}

/** Окно «О сервисе»: версия, сборка, ссылка на страницу проекта и на список изменений. */
export function AboutDialog({ info, onClose }: { info: BuildInfo | null; onClose: () => void }) {
  return (
    <Modal title="О сервисе" onClose={onClose}>
      <dl className="profile-dl">
        <div><dt>Продукт</dt><dd>Peregovorka — видеовстречи с автоматической стенограммой и протоколом</dd></div>
        <div><dt>Версия</dt><dd>{info?.version || "—"}</dd></div>
        <div><dt>Сборка</dt><dd>{short(info?.commit) || "неизвестна"}{info?.built_at ? <span className="muted"> · {info.built_at}</span> : null}</dd></div>
        <div><dt>Страница проекта</dt><dd><a href={PROJECT_URL} target="_blank" rel="noopener noreferrer">{PROJECT_URL.replace("https://", "")}</a></dd></div>
        <div><dt>Что нового</dt><dd><a href={CHANGELOG_URL} target="_blank" rel="noopener noreferrer">Список изменений версий</a></dd></div>
      </dl>
      <div className="row"><button className="btn ghost" onClick={onClose}>Закрыть</button></div>
    </Modal>
  );
}

/** Ненавязчивая строка внизу рабочих страниц: «Peregovorka 0.7.0»; по нажатию — окно «О сервисе». */
export function AppFooter({ info }: { info: BuildInfo | null }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <footer className="app-foot">
        <button type="button" onClick={() => setOpen(true)} title={short(info?.commit) ? `О сервисе · сборка ${short(info?.commit)}` : "О сервисе"}>Peregovorka{info?.version ? ` ${info.version}` : ""}</button>
      </footer>
      {open && <AboutDialog info={info} onClose={() => setOpen(false)} />}
    </>
  );
}

import { Link } from "react-router-dom";
import { DEFAULT_COOKIE_TEXT, loadPrivacy } from "../privacy";

const KEY = "pg:cookieNotice";

/** Компактное уведомление о cookie: сервис использует только технические cookie (сессия, защита от подделки запросов); аналитических и рекламных нет. Решение запоминается. */
export function CookieNotice() {
  const [shown, setShown] = useState(false);
  const [text, setText] = useState(DEFAULT_COOKIE_TEXT);
  useEffect(() => {
    try { setShown(localStorage.getItem(KEY) !== "1"); } catch { setShown(true); }
    void loadPrivacy().then((p) => { if (p.cookie_text.trim()) setText(p.cookie_text); });
  }, []);
  if (!shown) return null;
  return (
    <div className="cookie-note" role="region" aria-label="Уведомление о cookie">
      <span>{text}</span>
      <span className="cookie-actions">
        <Link to="/privacy" className="btn mini ghost">Подробнее</Link>
        <button type="button" className="btn mini primary" onClick={() => { try { localStorage.setItem(KEY, "1"); } catch { /* не запомнится — покажем снова */ } setShown(false); }}>Понятно</button>
      </span>
    </div>
  );
}
