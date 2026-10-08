import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type ApiError, type PhoneState } from "../api";
import { normalizeNumber, numberAllowed } from "../phone";
import { Modal } from "./Dialogs";

/**
 * «Позвонить»: исходящий вызов в идущую встречу через SIP (LiveKit). Абонент появляется среди участников как «Телефон: номер»; его речь попадает в стенограмму и запись.
 * Звонок идёт до ответа абонента (или отказа): пока вызов набирается, окно показывает ход, а ошибка АТС объясняется человеческим языком (занято, нет ответа, запрещено…).
 */
export default function PhoneDialog({ roomId, onClose }: { roomId: string; onClose: () => void }) {
  const [st, setSt] = useState<PhoneState | null>(null);
  const [number, setNumber] = useState("");
  const [calling, setCalling] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(() => api.manage.phone(roomId).then(setSt).catch((e) => setErr((e as ApiError).message)), [roomId]);
  useEffect(() => { void load(); }, [load]);

  const n = normalizeNumber(number);
  const blocked = n !== null && st ? !numberAllowed(st.allowed_prefixes, n) : false;

  const call = async (body: { number?: string; contact?: number }) => {
    setCalling(true); setMsg(null);
    try {
      const r = await api.manage.phoneCall(roomId, body);
      setMsg({ ok: true, text: `${r.display_name} подключён к встрече.` }); setNumber(""); await load();
    } catch (e) { setMsg({ ok: false, text: (e as ApiError).message }); }
    setCalling(false);
  };
  const submit = (e: FormEvent) => { e.preventDefault(); if (n && !blocked && !calling) void call({ number: n }); };
  const hangup = async (id: string) => {
    try { await api.manage.phoneHangup(roomId, id); setMsg({ ok: true, text: "Абонент отключён." }); await load(); } catch (e) { setMsg({ ok: false, text: (e as ApiError).message }); }
  };

  return (
    <Modal title="Позвонить участнику по телефону" onClose={onClose}>
      {!st && !err && <p className="muted">Загрузка…</p>}
      {err && <div className="alert error" role="alert">{err}</div>}
      {st && !st.can_call && <div className="alert info">{st.reason ?? "Звонки сейчас недоступны."}</div>}
      {st && st.can_call && (
        <form className="form" onSubmit={submit} noValidate>
          <label>Номер телефона
            <input value={number} onChange={(e) => setNumber(e.target.value)} inputMode="tel" placeholder="+70001112233" autoFocus disabled={calling} aria-invalid={number.trim() !== "" && (n === null || blocked)} maxLength={48} />
            {number.trim() !== "" && n === null && <span className="field-err" role="alert">Только цифры и символы + * # (пробелы, скобки и дефисы допустимы).</span>}
            {blocked && <span className="field-err" role="alert">Этот номер не входит в список допустимых для профиля «{st.profile}»{st.allowed_prefixes.length ? `: ${st.allowed_prefixes.join(", ")}` : ""}.</span>}
          </label>
          {st.contacts.length > 0 && (
            <div><span className="small muted">Сохранённые номера:</span>
              <div className="row tight" style={{ flexWrap: "wrap" }}>{st.contacts.map((c, i) => (
                <button key={`${c.number}-${i}`} type="button" className="btn mini" disabled={calling} onClick={() => void call({ contact: i })} title={c.number}>{c.name || c.number}</button>))}</div></div>)}
          <div className="row">
            <button className="btn primary" disabled={!n || blocked || calling}>{calling ? "Вызываем… (ждём ответа абонента)" : "Позвонить"}</button>
            <span className="muted small">Профиль: {st.profile}. Ответа ждём до окончания времени вызова профиля.</span>
          </div>
        </form>
      )}
      {msg && <div className={`alert ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
      {st && st.phones.length > 0 && (
        <div><h3 style={{ margin: "12px 0 6px" }}>Сейчас на связи по телефону</h3>
          <ul className="chips-list">{st.phones.map((p) => (
            <li key={p.guest_id}>{p.display_name}<button type="button" className="btn mini ghost danger" onClick={() => void hangup(p.guest_id)}>Отключить</button></li>))}</ul></div>)}
      {st?.allow_inbound && st.extension && <p className="help">Входящие: звонок на внутренний номер <b>{st.extension}</b> попадает в эту встречу.</p>}
      <div className="row form-actions"><button type="button" className="btn ghost" onClick={onClose}>Закрыть</button></div>
    </Modal>
  );
}
