import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Modal } from "./Dialogs";
import HelpDialog from "./HelpDialog";
import { useSite } from "../site";

export const PROJECT_URL = "https://github.com/calleograph/peregovorka";
export const CHANGELOG_URL = `${PROJECT_URL}/blob/main/CHANGELOG.md`;

export interface BuildInfo { version: string; commit: string; built_at?: string }
const short = (c?: string | null) => (c && c !== "unknown" ? c.slice(0, 7) : "");

/** Подвал входа: название системы (организации), ссылки на опубликованные документы, помощь; сведения о проекте Peregovorka и лицензии остаются всегда. */
export function LoginFooter({ info }: { info: BuildInfo | null }) {
  const site = useSite();
  const [help, setHelp] = useState(false);
  return (
    <footer className="login-foot">
      <span>{site.org.short || site.name}{info?.version ? ` · ${info.version}` : ""}</span>
      {site.documents.map((d) => <span key={d.kind}><span aria-hidden> · </span><Link to={`/legal/${d.kind}`}>{d.title}</Link></span>)}
      {(site.support || site.documents.length > 0) && <span><span aria-hidden> · </span><button type="button" className="linklike" onClick={() => setHelp(true)}>Помощь и поддержка</button></span>}
      <span aria-hidden> · </span><a href={PROJECT_URL} target="_blank" rel="noopener noreferrer">Peregovorka (open source)</a>
      {site.footer_text && <div className="login-foot-extra">{site.footer_text}</div>}
      {help && <HelpDialog onClose={() => setHelp(false)} />}
    </footer>
  );
}

/** Окно «О системе»: сведения об установке и организации; продукт Peregovorka, его репозиторий и лицензия указаны всегда. */
export function AboutDialog({ info, onClose }: { info: BuildInfo | null; onClose: () => void }) {
  const site = useSite();
  return (
    <Modal title="О системе" onClose={onClose}>
      <dl className="profile-dl">
        <div><dt>Система</dt><dd>{site.name}{site.subtitle ? ` — ${site.subtitle}` : ""}</dd></div>
        {(site.org.full || site.org.short) && <div><dt>Организация</dt><dd>{site.org.full || site.org.short}</dd></div>}
        {site.org.unit && <div><dt>Подразделение</dt><dd>{site.org.unit}</dd></div>}
        {site.org.legal_name && <div><dt>Юридическое наименование</dt><dd>{site.org.legal_name}</dd></div>}
        {site.org.address && <div><dt>Адрес</dt><dd>{site.org.address}</dd></div>}
        {site.org.url && <div><dt>Сайт организации</dt><dd><a href={site.org.url} target="_blank" rel="noopener noreferrer">{site.org.url}</a></dd></div>}
        <div><dt>Версия</dt><dd>{info?.version || "—"}</dd></div>
        <div><dt>Сборка</dt><dd>{short(info?.commit) || "неизвестна"}{info?.built_at ? <span className="muted"> · {info.built_at}</span> : null}</dd></div>
        <div><dt>Программное обеспечение</dt><dd>Peregovorka — свободный проект с открытым исходным кодом · <a href={PROJECT_URL} target="_blank" rel="noopener noreferrer">{PROJECT_URL.replace("https://", "")}</a></dd></div>
        <div><dt>Что нового</dt><dd><a href={CHANGELOG_URL} target="_blank" rel="noopener noreferrer">Список изменений версий</a></dd></div>
      </dl>
      <div className="row"><button className="btn ghost" onClick={onClose}>Закрыть</button></div>
    </Modal>
  );
}

/** Спокойный подвал рабочих страниц: слева — организация (или название системы), версия, документы и помощь; справа — сведения о проекте Peregovorka. */
export function AppFooter({ info }: { info: BuildInfo | null }) {
  const [open, setOpen] = useState(false);
  const [help, setHelp] = useState(false);
  const site = useSite();
  return (
    <>
      <footer className="app-foot">
        <div className="app-foot-in">
          <span>
            <button type="button" onClick={() => setOpen(true)} title="О системе">{site.org.short || site.name}{info?.version ? ` ${info.version}` : ""}</button>
            {site.documents.map((d) => <span key={d.kind}><span className="sep" aria-hidden>·</span><Link to={`/legal/${d.kind}`}>{d.title}</Link></span>)}
            {(site.support || site.documents.length > 0) && <><span className="sep" aria-hidden>·</span><button type="button" onClick={() => setHelp(true)}>Помощь и поддержка</button></>}
            {site.footer_text && <><span className="sep" aria-hidden>·</span><span>{site.footer_text}</span></>}
            {site.org.url && <><span className="sep" aria-hidden>·</span><a href={site.org.url} target="_blank" rel="noopener noreferrer">{site.org.url.replace(/^https?:\/\//, "")}</a></>}
          </span>
          <span><a href={PROJECT_URL} target="_blank" rel="noopener noreferrer">Peregovorka (open source)</a><span className="sep" aria-hidden>·</span><a href={CHANGELOG_URL} target="_blank" rel="noopener noreferrer">Что нового</a></span>
        </div>
      </footer>
      {open && <AboutDialog info={info} onClose={() => setOpen(false)} />}
      {help && <HelpDialog onClose={() => setHelp(false)} />}
    </>
  );
}

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
