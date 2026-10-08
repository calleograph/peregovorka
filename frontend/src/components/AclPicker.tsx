import { useState } from "react";
import type { AclEntry, ApiError, DirHit } from "../api";

export const entryLabel = (e: AclEntry) => e.display_name || (e.subject_type === "group" ? e.subject_ref.split(",")[0].replace(/^cn=/i, "") : e.subject_ref);

/** Список «групп и людей» с кнопкой удаления у каждого. */
export function Chips({ items, onRemove, empty }: { items: AclEntry[]; onRemove: (i: number) => void; empty: string }) {
  if (!items.length) return <div className="muted small">{empty}</div>;
  return (
    <ul className="chips-list">
      {items.map((e, i) => (
        <li key={`${e.subject_type}:${e.subject_ref}`} title={e.subject_ref}>
          <span className="chip-kind">{e.subject_type === "group" ? "группа" : "человек"}</span> {entryLabel(e)}
          <button type="button" className="chip-x" onClick={() => onRemove(i)} aria-label={`Убрать ${entryLabel(e)}`}>✕</button>
        </li>
      ))}
    </ul>
  );
}

const sameEntry = (a: AclEntry, b: AclEntry) => a.subject_type === b.subject_type && a.subject_ref.toLowerCase() === b.subject_ref.toLowerCase();

interface Props {
  acl: AclEntry[];
  moderators: AclEntry[];
  onChange: (next: { acl: AclEntry[]; moderators: AclEntry[] }) => void;
  /** Поиск в каталоге AD (для администратора и для руководителя комнаты разные адреса API). */
  search: (kind: "group" | "user", q: string) => Promise<DirHit[]>;
  /** Тексты зависят от того, кто редактирует. */
  leaderHelp?: string;
  leaderEmpty?: string;
}

/** Доступ и руководители комнаты: два списка и один поиск по каталогу — «＋ В доступ» / «＋ Руководителем». */
export default function AclPicker({ acl, moderators, onChange, search, leaderHelp, leaderEmpty }: Props) {
  const [hits, setHits] = useState<DirHit[]>([]);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<"group" | "user">("group");
  const [msg, setMsg] = useState("");
  const [manual, setManual] = useState("");

  const run = async () => {
    setMsg("");
    try { const r = await search(kind, q); setHits(r); if (!r.length) setMsg("Ничего не найдено"); }
    catch (e) { setHits([]); setMsg((e as ApiError).message); }
  };
  const addTo = (list: "acl" | "moderators", entry: AclEntry) => {
    const cur = list === "acl" ? acl : moderators;
    const e = { ...entry, subject_ref: entry.subject_ref.trim() };
    if (cur.some((x) => sameEntry(x, e))) return;
    onChange({ acl, moderators, [list]: [...cur, e] });
  };
  const removeFrom = (list: "acl" | "moderators", i: number) => onChange({ acl, moderators, [list]: (list === "acl" ? acl : moderators).filter((_, k) => k !== i) });
  const hitEntry = (h: DirHit): AclEntry => ({ subject_type: h.kind, subject_ref: h.ref, display_name: h.name });
  const addManual = (list: "acl" | "moderators") => {
    const ref = manual.trim();
    if (!ref) return;
    addTo(list, { subject_type: /^cn=/i.test(ref) ? "group" : kind, subject_ref: ref });
    setManual("");
  };

  return (
    <>
      <div className="cols two">
        <div>
          <h4>Кто может заходить</h4>
          <p className="help">Группы и люди из каталога. Если список пуст — заходят только администраторы сервера (и руководители).</p>
          <Chips items={acl} empty="Пока никого — вход только у администраторов." onRemove={(i) => removeFrom("acl", i)} />
        </div>
        <div>
          <h4>Руководители комнаты</h4>
          <p className="help">{leaderHelp ?? "Полностью управляют своей комнатой: настройки, доступ, гости, слово, запись и транскрибация, доска. Руководителей может быть несколько; вход в комнату им разрешён всегда. Администраторы сервера — руководители всех комнат автоматически."}</p>
          <Chips items={moderators} empty={leaderEmpty ?? "Руководители не назначены — комнатой управляют только администраторы сервера."} onRemove={(i) => removeFrom("moderators", i)} />
        </div>
      </div>
      <div className="picker">
        <div className="row">
          <select value={kind} onChange={(e) => setKind(e.target.value as "group" | "user")} aria-label="Что искать"><option value="group">Группа AD</option><option value="user">Пользователь AD</option></select>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск в каталоге (от 2 символов), например: Отдел кадров" onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void run(); } }} />
          <button type="button" className="btn" onClick={() => void run()} disabled={q.trim().length < 2}>Найти</button>
        </div>
        {msg && <div className="muted small">{msg}</div>}
        {hits.map((h) => (
          <div key={h.ref} className="hit"><span>{h.name}{h.sam ? ` (${h.sam})` : ""} <span className="muted small">{h.kind === "group" ? h.ref : h.email}</span></span>
            <span className="row tight"><button type="button" className="btn mini" onClick={() => addTo("acl", hitEntry(h))}>＋ В доступ</button>
              <button type="button" className="btn mini primary" onClick={() => addTo("moderators", hitEntry(h))}>＋ Руководителем</button></span></div>
        ))}
        <div className="row">
          <input value={manual} onChange={(e) => setManual(e.target.value)} placeholder="Или вручную: DN группы (CN=…) либо objectGUID пользователя" aria-label="Добавить вручную" />
          <button type="button" className="btn mini" disabled={!manual.trim()} onClick={() => addManual("acl")}>＋ В доступ</button>
          <button type="button" className="btn mini" disabled={!manual.trim()} onClick={() => addManual("moderators")}>＋ Руководителем</button>
        </div>
        <span className="example">Пример группы: <code>CN=Отдел кадров,OU=Группы,DC=corp,DC=local</code></span>
      </div>
    </>
  );
}
