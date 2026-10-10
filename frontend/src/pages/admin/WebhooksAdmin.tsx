import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type RoomAdmin, type WebhookDeliveryItem, type WebhookEndpointIn, type WebhookEndpointItem, type WebhookIssued } from "../../api";
import { ConfirmDialog, Modal } from "../../components/Dialogs";

const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString("ru-RU") : "—");
const STATUS: Record<WebhookEndpointItem["status"], string> = { active: "работает", degraded: "деградация", disabled: "отключён" };
const DSTATUS: Record<WebhookDeliveryItem["status"], string> = { pending: "ожидает", delivered: "доставлено", failed: "не доставлено" };
const empty = (): WebhookEndpointIn => ({ name: "", url: "https://", enabled: true, events: [], rooms: null });

/** Получатели событий публичного API: адрес, события, область комнат, секрет подписи, проверка, история доставок и ручной повтор. Секрет показывается один раз. */
export default function WebhooksAdmin() {
  const [items, setItems] = useState<WebhookEndpointItem[]>([]);
  const [events, setEvents] = useState<{ name: string; description: string }[]>([]);
  const [rooms, setRooms] = useState<RoomAdmin[]>([]);
  const [edit, setEdit] = useState<{ id?: string; v: WebhookEndpointIn } | null>(null);
  const [issued, setIssued] = useState<WebhookIssued | null>(null);
  const [hist, setHist] = useState<{ ep: WebhookEndpointItem; rows: WebhookDeliveryItem[] } | null>(null);
  const [del, setDel] = useState<WebhookEndpointItem | null>(null);
  const [rot, setRot] = useState<WebhookEndpointItem | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const [w, e, r] = await Promise.all([api.admin.webhooks(), api.admin.webhookEvents(), api.admin.rooms()]);
      setItems(w); setEvents(e); setRooms(r);
    } catch (x) { setErr((x as ApiError).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const guard = async (f: () => Promise<void>) => { setErr(""); try { await f(); } catch (x) { setErr((x as ApiError).message); } };
  const open = (w?: WebhookEndpointItem) => setEdit(w ? { id: w.id, v: { name: w.name, url: w.url, enabled: w.enabled, events: w.events, rooms: w.rooms } } : { v: empty() });
  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!edit) return;
    void guard(async () => {
      if (edit.id) { await api.admin.webhookUpdate(edit.id, edit.v); setEdit(null); }
      else { const r = await api.admin.webhookCreate(edit.v); setEdit(null); setIssued(r); }
      await load();
    });
  };
  const toggleEvent = (n: string) => edit && setEdit({ ...edit, v: { ...edit.v, events: edit.v.events.includes(n) ? edit.v.events.filter((x) => x !== n) : [...edit.v.events, n] } });
  const toggleRoom = (id: string) => edit && setEdit({ ...edit, v: { ...edit.v, rooms: (edit.v.rooms ?? []).includes(id) ? (edit.v.rooms ?? []).filter((x) => x !== id) : [...(edit.v.rooms ?? []), id] } });
  const test = (w: WebhookEndpointItem) => guard(async () => {
    const r = await api.admin.webhookTest(w.id);
    setNote({ ok: r.ok, text: r.ok ? `«${w.name}»: проверочное событие доставлено (HTTP ${r.status})` : `«${w.name}»: не доставлено — ${r.error ?? "ошибка"}` });
    await load();
  });
  const history = (w: WebhookEndpointItem) => guard(async () => setHist({ ep: w, rows: await api.admin.webhookDeliveries(w.id) }));

  return (
    <div className="card form">
      <div className="row"><h2>Подписки на события (webhooks)</h2><div className="spacer" /><button className="btn primary" onClick={() => open()}>Новый получатель</button></div>
      <p className="muted small">Сервер отправляет получателю события (встреча началась/завершилась, документ готов, задача завершена) с подписью HMAC. В событиях только идентификаторы — содержимое получатель запрашивает через API.
        Адрес проверяется политикой безопасности: внутренние адреса запрещены, пока не разрешены в настройках («Разрешённые внутренние узлы»).</p>
      {note && <div className={`alert ${note.ok ? "ok" : "error"}`} role="status">{note.text}</div>}
      {err && <div className="alert error" role="alert">{err}</div>}
      {!items.length && <p className="muted">Получателей пока нет.</p>}
      {items.map((w) => (
        <div className="card" key={w.id}>
          <div className="row">
            <strong>{w.name}</strong><span className={`badge ${w.status === "active" ? "" : "warn"}`}>{STATUS[w.status]}</span><div className="spacer" />
            <button className="btn mini" onClick={() => void test(w)}>Проверить</button>
            <button className="btn mini" onClick={() => void history(w)}>История</button>
            <button className="btn mini" onClick={() => open(w)}>Изменить</button>
            <button className="btn mini" onClick={() => setRot(w)}>Заменить секрет</button>
            <button className="btn mini danger" onClick={() => setDel(w)}>Удалить</button>
          </div>
          <p className="small"><code>{w.url}</code> · События: {w.events.length ? w.events.join(", ") : "все"} · Комнаты: {w.rooms === null ? "все" : `${w.rooms.length} шт.`}</p>
          <p className="small muted">Последняя успешная доставка: {fmt(w.last_success_at)}{w.consecutive_failures ? ` · неудач подряд: ${w.consecutive_failures}` : ""}{w.last_error ? ` · ${w.last_error}` : ""}</p>
          {w.status === "disabled" && (
            <div className="alert error" role="alert">Отключён{w.disabled_reason ? `: ${w.disabled_reason}` : ""}. События ему не отправляются.{" "}
              <button className="btn mini" onClick={() => void guard(async () => { await api.admin.webhookEnable(w.id); await load(); })}>Включить снова</button></div>
          )}
        </div>
      ))}

      {edit && (
        <Modal title={edit.id ? "Изменить получателя" : "Новый получатель"} onClose={() => setEdit(null)}>
          <form className="form" onSubmit={save}>
            <label>Название<input value={edit.v.name} onChange={(e) => setEdit({ ...edit, v: { ...edit.v, name: e.target.value } })} maxLength={120} required autoFocus /></label>
            <label>Адрес (HTTPS)<input value={edit.v.url} onChange={(e) => setEdit({ ...edit, v: { ...edit.v, url: e.target.value } })} maxLength={500} required placeholder="https://hooks.example.com/peregovorka" /></label>
            <label className="check"><input type="checkbox" checked={edit.v.enabled} onChange={(e) => setEdit({ ...edit, v: { ...edit.v, enabled: e.target.checked } })} /> Получатель включён</label>
            <fieldset><legend>События (ничего не отмечено — все)</legend>
              {events.map((ev) => <label className="check" key={ev.name}><input type="checkbox" checked={edit.v.events.includes(ev.name)} onChange={() => toggleEvent(ev.name)} /> <code>{ev.name}</code> — {ev.description}</label>)}
            </fieldset>
            <fieldset><legend>Комнаты</legend>
              <label className="check"><input type="checkbox" checked={edit.v.rooms === null} onChange={(e) => setEdit({ ...edit, v: { ...edit.v, rooms: e.target.checked ? null : [] } })} /> Все комнаты</label>
              {edit.v.rooms !== null && rooms.map((r) => <label className="check" key={r.id}><input type="checkbox" checked={edit.v.rooms!.includes(r.id)} onChange={() => toggleRoom(r.id)} /> {r.name}</label>)}
            </fieldset>
            {err && <div className="alert error" role="alert">{err}</div>}
            <div className="row"><button className="btn primary">Сохранить</button><button type="button" className="btn ghost" onClick={() => setEdit(null)}>Отмена</button></div>
          </form>
        </Modal>
      )}
      {issued && (
        <Modal title="Секрет подписи" onClose={() => setIssued(null)}>
          <p><strong>Сохраните секрет сейчас — позже он не будет показан.</strong> Им получатель проверяет подпись событий.</p>
          <input readOnly value={issued.secret} onFocus={(e) => e.currentTarget.select()} aria-label="Секрет подписи" style={{ fontFamily: "monospace", width: "100%" }} />
          <div className="row"><button className="btn primary" onClick={() => setIssued(null)}>Я сохранил секрет</button></div>
        </Modal>
      )}
      {rot && (
        <ConfirmDialog title="Заменить секрет" confirmLabel="Выпустить новый секрет" danger={false} onClose={() => setRot(null)}
          body={<p>Будет выпущен новый секрет. Ещё 24 часа события подписываются обоими секретами (в заголовке две подписи), чтобы получатель успел перейти; затем действует только новый.</p>}
          onConfirm={async () => { setIssued(await api.admin.webhookRotate(rot.id, 24)); await load(); }} />
      )}
      {del && (
        <ConfirmDialog title="Удалить получателя" confirmLabel="Удалить" typed={del.name} onClose={() => setDel(null)}
          body={<p>Получатель «{del.name}» и история его доставок будут удалены.</p>}
          onConfirm={async () => { await api.admin.webhookDelete(del.id); await load(); }} />
      )}
      {hist && (
        <Modal title={`История доставок: ${hist.ep.name}`} onClose={() => setHist(null)} wide>
          <div className="table-scroll"><table className="table">
            <thead><tr><th>Создано</th><th>Событие</th><th>Статус</th><th>Попыток</th><th>Ответ</th><th>Следующая</th><th /></tr></thead>
            <tbody>
              {hist.rows.map((d) => (
                <tr key={d.id}>
                  <td>{fmt(d.created_at)}</td><td><code>{d.event_type}</code></td><td>{DSTATUS[d.status]}</td><td>{d.attempts}{d.manual_retries ? ` (+${d.manual_retries} вручную)` : ""}</td>
                  <td>{d.last_status ?? "—"} {d.last_error ?? ""}</td><td>{fmt(d.next_attempt_at)}</td>
                  <td>{d.status !== "pending" && <button className="btn mini" onClick={() => void guard(async () => { await api.admin.webhookRetry(d.id); setHist({ ep: hist.ep, rows: await api.admin.webhookDeliveries(hist.ep.id) }); })}>Повторить</button>}</td>
                </tr>
              ))}
              {!hist.rows.length && <tr><td colSpan={7} className="muted">Доставок пока не было</td></tr>}
            </tbody>
          </table></div>
        </Modal>
      )}
    </div>
  );
}
