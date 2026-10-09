import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type AclEntry, type ApiError, type DirHit, type LocalAdminInfo } from "../../api";
import { Chips } from "../../components/AclPicker";
import { fmtDate } from "./common";

const asEntries = (dns: string[]): AclEntry[] => dns.map((d) => ({ subject_type: "group", subject_ref: d }));

/** Список групп каталога с поиском: добавляются найденные группы или вручную введённый DN. */
export function GroupList({ title, help, empty, value, onChange }: { title: string; help: string; empty: string; value: string[]; onChange: (v: string[]) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<DirHit[]>([]);
  const [msg, setMsg] = useState("");
  const [manual, setManual] = useState("");
  const add = (dn: string) => { const v = dn.trim(); if (v && !value.some((x) => x.toLowerCase() === v.toLowerCase())) onChange([...value, v]); };
  const search = async () => {
    setMsg("");
    try { const r = await api.admin.search("group", q); setHits(r); if (!r.length) setMsg("Ничего не найдено"); } catch (e) { setHits([]); setMsg((e as ApiError).message); }
  };
  return (
    <fieldset className="group"><legend>{title}</legend>
      <p className="help" style={{ marginTop: 0 }}>{help}</p>
      <Chips items={asEntries(value)} empty={empty} onRemove={(i) => onChange(value.filter((_, k) => k !== i))} />
      <div className="picker">
        <div className="row">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти группу в каталоге (от 2 символов), например: Admins" aria-label="Поиск группы"
                 onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void search(); } }} />
          <button type="button" className="btn" onClick={() => void search()} disabled={q.trim().length < 2}>Найти</button>
        </div>
        {msg && <div className="muted small">{msg}</div>}
        {hits.map((h) => (
          <div key={h.ref} className="hit"><span>{h.name} <span className="muted small">{h.ref}</span></span>
            <button type="button" className="btn mini primary" onClick={() => add(h.ref)}>＋ Добавить</button></div>
        ))}
        <div className="row">
          <input value={manual} onChange={(e) => setManual(e.target.value)} placeholder="Или DN группы вручную: CN=Admins,OU=Groups,DC=example,DC=local" aria-label="DN группы" spellCheck={false} />
          <button type="button" className="btn mini" disabled={!manual.trim()} onClick={() => { add(manual); setManual(""); }}>＋ Добавить</button>
        </div>
      </div>
    </fieldset>
  );
}

function LocalAdminCard({ info, onChanged }: { info: LocalAdminInfo | null; onChanged: () => void }) {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const change = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setMsg(null);
    try { await api.changePassword(cur, next); setMsg({ ok: true, text: "Пароль изменён. Остальные сеансы этого администратора завершены." }); setCur(""); setNext(""); onChanged(); }
    catch (err) { setMsg({ ok: false, text: (err as ApiError).message }); }
    setBusy(false);
  };
  if (!info) return null;
  return (
    <div className="card form">
      <h3 style={{ margin: 0 }}>Локальный (аварийный) администратор</h3>
      {info.exists ? (
        <p className="muted" style={{ margin: 0 }}>Логин <code>{info.username}</code> · последний вход: {fmtDate(info.last_login_at)} · пароль изменён: {fmtDate(info.password_changed_at)}.
          Не зависит от каталога: если каталог недоступен или группы настроены неверно, вход остаётся.</p>
      ) : <div className="alert info">Локальный администратор не создан. На сервере: <code>{info.recovery}</code></div>}
      <div className="alert info small">Потеряли пароль? На сервере, от root: <code>./scripts/admin-reset.sh</code> — выдаст новый пароль, завершит старые сеансы и запишет событие в аудит. Работает без LDAP и без правки базы данных.</div>
      {info.exists && (
        <form onSubmit={(e) => void change(e)} className="form" style={{ padding: 0, boxShadow: "none", border: 0 }}>
          <div className="cols">
            <label>Текущий пароль<input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} /></label>
            <label>Новый пароль<input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /><span className="help">Не короче 12 символов, не менее двух видов символов, без имени пользователя.</span></label>
          </div>
          {msg && <div className={`alert ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
          <div className="row"><button className="btn" disabled={busy || !cur || !next}>Сменить пароль</button></div>
        </form>
      )}
    </div>
  );
}

/** «Администраторы»: AD-группы администраторов (отдельно от настройки самого LDAP), необязательные группы допуска и локальный администратор. */
export default function AccessAdmin() {
  const [admins, setAdmins] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [info, setInfo] = useState<LocalAdminInfo | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    void api.admin.settings("access").then((v) => { setAdmins((v.admin_groups as unknown as string[]) ?? []); setLoaded(true); }).catch((e) => setMsg({ ok: false, text: (e as ApiError).message }));
    void api.admin.localAdmin().then(setInfo).catch(() => undefined);
  }, []);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setBusy(true); setMsg(null);
    try { await api.admin.saveSettings("access", { admin_groups: admins } as never); setMsg({ ok: true, text: "Сохранено. Новые права применяются при следующем входе пользователя." }); }
    catch (e) { setMsg({ ok: false, text: (e as ApiError).message }); }
    setBusy(false);
  };

  return (
    <section>
      <div className="row"><h2>Администраторы</h2></div>
      <p className="muted">Кто из доменных пользователей получает права администратора. Кто вообще может входить в систему — отдельно, в разделе «Кто может входить». Подключение к каталогу настраивается отдельно — в разделе «Подключения LDAP».</p>
      {!loaded ? <div className="muted">Загрузка…</div> : (
        <div className="card form">
          <GroupList title="Группы администраторов" value={admins} onChange={setAdmins}
                     help="Члены этих групп (включая вложенные) входят с правами администратора системы. Можно указать несколько групп."
                     empty="Групп нет — администраторами работают только локальный администратор и группа из файла установки (если задана)." />
          {msg && <div className={`alert ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
          <div className="row form-actions"><button className="btn primary" onClick={() => void save()} disabled={busy}>{busy ? "Сохранение…" : "Сохранить"}</button></div>
        </div>
      )}
      <LocalAdminCard info={info} onChanged={load} />
    </section>
  );
}
