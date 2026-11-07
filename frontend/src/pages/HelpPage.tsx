import { useMemo, useState } from "react";
import index from "../help/helpIndex.json";
import { searchHelp, type HelpItem } from "../help/search";
import { helpUrl } from "../help/docs";
import { useFocusOnMount } from "../useInitialFocus";

const ITEMS = index.items as HelpItem[];
const ROLES = [...new Set(ITEMS.flatMap((i) => i.roles))].sort();
const SECTIONS = [...new Set(ITEMS.map((i) => i.sec))].filter((s) => s !== "Статьи");

/** «Справка»: поиск по инструкциям обычными словами. Индекс собран из вики (scripts/build_help_index.py), статьи открываются на GitHub. */
export default function HelpPage() {
  const focusOnMount = useFocusOnMount();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [sec, setSec] = useState("");
  const pool = useMemo(() => ITEMS.filter((i) => (!role || i.roles.includes(role)) && (!sec || i.sec === sec)), [role, sec]);
  const found = useMemo(() => (q.trim() ? searchHelp(pool, q) : role || sec ? pool.filter((i) => !i.also).slice(0, 30) : []), [pool, q, role, sec]);
  const empty = q.trim() && !found.length;
  return (
    <div className="help-page">
      <h1>Справка</h1>
      <input ref={focusOnMount} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Спросите обычными словами: почему меня не слышат?" aria-label="Поиск по инструкциям" />
      <div className="row help-filters">
        <label>Раздел<select value={sec} onChange={(e) => setSec(e.target.value)}><option value="">Все</option>{SECTIONS.map((s) => <option key={s}>{s}</option>)}</select></label>
        <label>Роль<select value={role} onChange={(e) => setRole(e.target.value)}><option value="">Любая</option>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></label>
      </div>
      {empty && <p className="muted">Ничего не нашлось. Попробуйте другие слова или откройте <a href={helpUrl("Поиск-по-вопросам")} target="_blank" rel="noopener noreferrer">все вопросы</a>.</p>}
      {found.length > 0 && <ul className="help-list">{found.map((i) => <li key={`${i.page}-${i.q}`}><a href={helpUrl(i.page)} target="_blank" rel="noopener noreferrer">{i.q}</a><span className="muted small">{i.sec === "Статьи" ? i.page.replace(/-/g, " ") : i.sec}</span></li>)}</ul>}
      {!q.trim() && !role && !sec && <>
        <h2>Популярные вопросы</h2>
        <ul className="help-list">{index.popular.map((p) => <li key={p.page}><a href={helpUrl(p.page)} target="_blank" rel="noopener noreferrer">{p.q}</a></li>)}</ul>
        <p className="muted small">Инструкции открываются в вики на GitHub. На сервере без выхода в интернет попросите администратора дать доступ или открывайте их с другого устройства.</p>
      </>}
    </div>
  );
}
