import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type PublicApiClient, type PublicApiClientIn, type PublicApiIssued, type PublicApiKey, type PublicApiLogRow, type RoomAdmin } from "../../api";
import { ConfirmDialog, Modal } from "../../components/Dialogs";
import { publicApiFields } from "./fields";
import SettingsForm from "./SettingsForm";

const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString("ru-RU") : "—");
const STATE: Record<PublicApiKey["state"], string> = { active: "действует", expired: "срок истёк", revoked: "отозван" };
const empty = (): PublicApiClientIn => ({ name: "", description: null, enabled: true, scopes: [], rooms: null, ip_allowlist: null });

/** Публичный API: настройки, интеграции (сервисные учётные записи) с ключами, журнал обращений. Секрет ключа показывается один раз. */
export default function PublicApiAdmin() {
  const [clients, setClients] = useState<PublicApiClient[]>([]);
  const [scopes, setScopes] = useState<{ name: string; description: string }[]>([]);
  const [rooms, setRooms] = useState<RoomAdmin[]>([]);
  const [err, setErr] = useState("");
  const [edit, setEdit] = useState<{ id?: string; v: PublicApiClientIn; ips: string } | null>(null);
  const [issued, setIssued] = useState<PublicApiIssued | null>(null);
  const [del, setDel] = useState<PublicApiClient | null>(null);
  const [revoke, setRevoke] = useState<PublicApiKey | null>(null);
  const [rotate, setRotate] = useState<PublicApiKey | null>(null);
  const [log, setLog] = useState<PublicApiLogRow[] | null>(null);
  const [onlyErrors, setOnlyErrors] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, s, r] = await Promise.all([api.admin.apiClients(), api.admin.apiScopes(), api.admin.rooms()]);
      setClients(c); setScopes(s.scopes); setRooms(r);
    } catch (e) { setErr((e as ApiError).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const loadLog = useCallback(async () => {
    try { setLog(await api.admin.apiLog({ limit: 100, status_from: onlyErrors ? 400 : undefined })); } catch (e) { setErr((e as ApiError).message); }
  }, [onlyErrors]);

  const open = (c?: PublicApiClient) => setEdit(c
    ? { id: c.id, v: { name: c.name, description: c.description, enabled: c.enabled, scopes: c.scopes, rooms: c.rooms, ip_allowlist: c.ip_allowlist }, ips: (c.ip_allowlist ?? []).join("\n") }
    : { v: empty(), ips: "" });

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!edit) return;
    setErr("");
    const ips = edit.ips.split(/[\s,;]+/).filter(Boolean);
    const body = { ...edit.v, ip_allowlist: ips.length ? ips : null };
    try {
      if (edit.id) await api.admin.apiUpdateClient(edit.id, body); else await api.admin.apiCreateClient(body);
      setEdit(null); await load();
    } catch (x) { setErr((x as ApiError).message); }
  };
  const toggle = (name: string) => edit && setEdit({ ...edit, v: { ...edit.v, scopes: edit.v.scopes.includes(name) ? edit.v.scopes.filter((s) => s !== name) : [...edit.v.scopes, name] } });
  const toggleRoom = (id: string) => edit && setEdit({ ...edit, v: { ...edit.v, rooms: (edit.v.rooms ?? []).includes(id) ? (edit.v.rooms ?? []).filter((r) => r !== id) : [...(edit.v.rooms ?? []), id] } });

  return (
    <>
      <SettingsForm key="api" group="api" title="Публичный API" fields={publicApiFields}
        intro="Интеграции вызывают API по ключу: каждой интеграции выдаются только нужные права и, при необходимости, только некоторые комнаты. Описание методов — на странице документации." />
      <div className="card form">
        <div className="row">
          <h2>Интеграции</h2><div className="spacer" />
          <a className="btn ghost" href="/api/public/v1/docs" target="_blank" rel="noopener noreferrer">Документация API</a>
          <button className="btn primary" onClick={() => open()}>Новая интеграция</button>
        </div>
        {err && <div className="alert error" role="alert">{err}</div>}
        {!clients.length && <p className="muted">Интеграций пока нет. Создайте интеграцию и выпустите для неё ключ.</p>}
        {clients.map((c) => (
          <div className="card" key={c.id}>
            <div className="row">
              <strong>{c.name}</strong>{!c.enabled && <span className="badge">отключена</span>}<div className="spacer" />
              <button className="btn mini" onClick={() => open(c)}>Изменить</button>
              <button className="btn mini danger" onClick={() => setDel(c)}>Удалить</button>
            </div>
            {c.description && <p className="muted small">{c.description}</p>}
            <p className="small">Права: {c.scopes.length ? c.scopes.join(", ") : "нет"} · Комнаты: {c.rooms === null ? "все" : c.rooms.length ? `${c.rooms.length} шт.` : "ни одной"} · Адреса: {c.ip_allowlist?.length ? c.ip_allowlist.join(", ") : "любые"}</p>
            <div className="table-scroll"><table className="table">
              <thead><tr><th>Ключ</th><th>Метка</th><th>Состояние</th><th>Действует до</th><th>Последнее использование</th><th /></tr></thead>
              <tbody>
                {c.keys.map((k) => (
                  <tr key={k.id}>
                    <td><code>{k.key}</code></td><td>{k.label ?? "—"}</td><td>{STATE[k.state]}</td><td>{fmt(k.expires_at)}</td>
                    <td>{fmt(k.last_used_at)}{k.last_used_ip ? ` · ${k.last_used_ip}` : ""}</td>
                    <td className="row">
                      {k.state === "active" && <button className="btn mini" onClick={() => setRotate(k)}>Заменить</button>}
                      {k.state === "active" && <button className="btn mini danger" onClick={() => setRevoke(k)}>Отозвать</button>}
                    </td>
                  </tr>
                ))}
                {!c.keys.length && <tr><td colSpan={6} className="muted">Ключей нет</td></tr>}
              </tbody>
            </table></div>
            <div className="row"><button className="btn mini" onClick={async () => { try { setIssued(await api.admin.apiCreateKey(c.id, { label: null })); await load(); } catch (x) { setErr((x as ApiError).message); } }}>Выпустить ключ</button></div>
          </div>
        ))}
      </div>

      <div className="card form">
        <div className="row"><h2>Журнал обращений</h2><div className="spacer" />
          <label className="check"><input type="checkbox" checked={onlyErrors} onChange={(e) => { setOnlyErrors(e.target.checked); setLog(null); }} /> только ошибки</label>
          <button className="btn" onClick={() => void loadLog()}>{log ? "Обновить" : "Показать"}</button>
        </div>
        {log && (
          <div className="table-scroll"><table className="table">
            <thead><tr><th>Время</th><th>Ключ</th><th>Запрос</th><th>Статус</th><th>мс</th><th>Адрес</th><th>Код</th></tr></thead>
            <tbody>
              {log.map((r) => <tr key={r.id}><td>{fmt(r.at)}</td><td><code>{r.key_id ?? "—"}</code></td><td>{r.method} {r.path}</td><td>{r.status}</td><td>{r.ms}</td><td>{r.ip ?? "—"}</td><td>{r.error_code ?? ""}</td></tr>)}
              {!log.length && <tr><td colSpan={7} className="muted">Записей нет</td></tr>}
            </tbody>
          </table></div>
        )}
      </div>

      {edit && (
        <Modal title={edit.id ? "Изменить интеграцию" : "Новая интеграция"} onClose={() => setEdit(null)}>
          <form className="form" onSubmit={save}>
            <label>Название<input value={edit.v.name} onChange={(e) => setEdit({ ...edit, v: { ...edit.v, name: e.target.value } })} maxLength={120} required autoFocus placeholder="например, CRM" /></label>
            <label>Описание<input value={edit.v.description ?? ""} onChange={(e) => setEdit({ ...edit, v: { ...edit.v, description: e.target.value || null } })} maxLength={1000} /></label>
            <label className="check"><input type="checkbox" checked={edit.v.enabled} onChange={(e) => setEdit({ ...edit, v: { ...edit.v, enabled: e.target.checked } })} /> Интеграция включена</label>
            <fieldset><legend>Права</legend>
              {scopes.map((s) => <label className="check" key={s.name}><input type="checkbox" checked={edit.v.scopes.includes(s.name)} onChange={() => toggle(s.name)} /> <code>{s.name}</code> — {s.description}</label>)}
            </fieldset>
            <fieldset><legend>Комнаты</legend>
              <label className="check"><input type="checkbox" checked={edit.v.rooms === null} onChange={(e) => setEdit({ ...edit, v: { ...edit.v, rooms: e.target.checked ? null : [] } })} /> Все комнаты (в том числе созданные позже)</label>
              {edit.v.rooms !== null && rooms.map((r) => <label className="check" key={r.id}><input type="checkbox" checked={edit.v.rooms!.includes(r.id)} onChange={() => toggleRoom(r.id)} /> {r.name}</label>)}
            </fieldset>
            <label>Разрешённые адреса (необязательно)
              <textarea rows={3} value={edit.ips} onChange={(e) => setEdit({ ...edit, ips: e.target.value })} placeholder={"192.0.2.10\n198.51.100.0/24"} />
              <span className="muted small">По одному адресу или сети на строку. Пусто — обращения с любых адресов.</span>
            </label>
            {err && <div className="alert error" role="alert">{err}</div>}
            <div className="row"><button className="btn primary">Сохранить</button><button type="button" className="btn ghost" onClick={() => setEdit(null)}>Отмена</button></div>
          </form>
        </Modal>
      )}
      {issued && (
        <Modal title="Ключ выпущен" onClose={() => setIssued(null)}>
          <p><strong>Сохраните ключ сейчас — позже он не будет показан.</strong></p>
          <input readOnly value={issued.secret} onFocus={(e) => e.currentTarget.select()} aria-label="Ключ API" style={{ fontFamily: "monospace", width: "100%" }} />
          <p className="muted small">Передавайте его в заголовке <code>Authorization: Bearer …</code>. Храните как пароль.</p>
          <div className="row"><button className="btn primary" onClick={() => setIssued(null)}>Я сохранил ключ</button></div>
        </Modal>
      )}
      {rotate && (
        <ConfirmDialog title="Заменить ключ" confirmLabel="Выпустить новый ключ" danger={false} onClose={() => setRotate(null)}
          body={<p>Будет выпущен новый ключ. Прежний ключ <code>{rotate.key}</code> продолжит работать ещё 24 часа, чтобы интеграция успела перейти на новый, затем перестанет действовать.</p>}
          onConfirm={async () => { setIssued(await api.admin.apiRotateKey(rotate.id, { grace_hours: 24 })); await load(); }} />
      )}
      {revoke && (
        <ConfirmDialog title="Отозвать ключ" confirmLabel="Отозвать" onClose={() => setRevoke(null)}
          body={<p>Ключ <code>{revoke.key}</code> перестанет действовать немедленно. Интеграция, которая его использует, получит ошибку авторизации.</p>}
          onConfirm={async () => { await api.admin.apiRevokeKey(revoke.id); await load(); }} />
      )}
      {del && (
        <ConfirmDialog title="Удалить интеграцию" confirmLabel="Удалить" typed={del.name} onClose={() => setDel(null)}
          body={<p>Интеграция «{del.name}» и все её ключи будут удалены. Журнал обращений сохранится до истечения срока хранения.</p>}
          onConfirm={async () => { await api.admin.apiDeleteClient(del.id); await load(); }} />
      )}
    </>
  );
}
