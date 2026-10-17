import { useEffect, useState } from "react";
import { api, type ApiError, type CryptoStatus } from "../../api";
import { fmt } from "../../util";

/** «Шифрование хранилища»: ключи, их смена и шифрование уже записанных чатов и стенограмм (включение — в настройках комнаты). */
export default function CryptoAdmin() {
  const [st, setSt] = useState<CryptoStatus | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [pw, setPw] = useState("");
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const load = () => api.crypto.status().then(setSt).catch((e) => setErr((e as ApiError).message));
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (st?.job.state !== "running") return;
    const t = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(t);
  }, [st?.job.state]);
  const act = async (fn: () => Promise<unknown>, ok: string) => { setBusy(true); setErr(""); setNote(""); try { await fn(); setNote(ok); } catch (e) { setErr((e as ApiError).message); } await load(); setBusy(false); };
  if (!st) return <p className="muted">{err || "Загрузка…"}</p>;
  const c = st.counts;
  return (
    <section aria-label="Шифрование хранилища">
      <h2>Шифрование хранилища</h2>
      <p className="muted">Чат и стенограммы комнат, у которых включено «Шифровать чат и стенограммы при хранении», записываются в базу в зашифрованном виде ({st.algorithm}). Ключ данных лежит в базе только в зашифрованном виде и расшифровывается мастер-ключом сервера (<code>APP_MASTER_KEY</code>). Это защита хранилища и его копий; звук и видео этим режимом не шифруются.</p>
      {!st.master_key_set && <div className="alert warn">Мастер-ключ <code>APP_MASTER_KEY</code> не задан: шифрование хранилища недоступно.</div>}
      {err && <div className="alert error" role="alert">{err}</div>}
      {note && <div className="alert ok">{note}</div>}
      <div className="cards">
        <div className="card"><div className="v">{c.rooms_encrypted}</div><div className="l">комнат с шифрованием</div></div>
        <div className="card"><div className="v">{c.meeting_chat_messages.sealed} / {c.meeting_chat_messages.total}</div><div className="l">сообщений чата зашифровано</div></div>
        <div className="card"><div className="v">{c.transcript_segments.sealed} / {c.transcript_segments.total}</div><div className="l">реплик стенограмм зашифровано</div></div>
      </div>
      <h3>Ключи</h3>
      <div className="table-scroll"><table className="table"><thead><tr><th>№</th><th>Создан</th><th>Состояние</th></tr></thead>
        <tbody>{st.keys.map((k) => <tr key={k.id}><td>{k.id}</td><td>{fmt(k.created_at)}</td><td>{k.active ? <span className="badge ok">активный: им шифруются новые записи</span> : <span className="muted">прежний: нужен, чтобы читать старые записи</span>}</td></tr>)}</tbody></table></div>
      <p className="small muted">Ключи нельзя удалять, пока в базе есть записи, зашифрованные ими. <b>Храните копию APP_MASTER_KEY отдельно от базы и её резервных копий:</b> без него зашифрованные записи не прочитать, а база без него бесполезна для злоумышленника.</p>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <button className="btn" disabled={busy || !st.ready} onClick={() => setAsk(true)}>Сменить активный ключ</button>
        <button className="btn" disabled={busy || !st.ready || st.job.state === "running"} onClick={() => void act(() => api.crypto.reseal({}), "Шифрование запущено")}>Зашифровать существующие записи (комнаты с режимом)</button>
        <button className="btn" disabled={busy || !st.ready || st.job.state === "running"} onClick={() => void act(() => api.crypto.reseal({ rotate_all: true }), "Перезапись запущена")}>Перезаписать всё под активным ключом</button>
      </div>
      {ask && (
        <form className="form" style={{ marginTop: 12 }} onSubmit={(e) => { e.preventDefault(); void act(async () => { await api.crypto.rotate(pw); setAsk(false); setPw(""); }, "Создан новый активный ключ"); }}>
          <p className="small">Новые записи будут шифроваться новым ключом, старые остаются читаемыми. Подтвердите паролем администратора.</p>
          <label>Ваш пароль<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" autoFocus required /></label>
          <div className="row"><button className="btn primary" disabled={!pw || busy}>Сменить ключ</button><button type="button" className="btn" onClick={() => setAsk(false)}>Отмена</button></div>
        </form>)}
      {st.job.state && (
        <div className={`alert ${st.job.state === "failed" ? "error" : st.job.state === "done" ? "ok" : ""}`} role="status" style={{ marginTop: 12 }}>
          {st.job.state === "running" && `Выполняется: обработано ${st.job.done ?? 0}, пропущено ${st.job.skipped ?? 0}…`}
          {st.job.state === "done" && `Готово: зашифровано или перезаписано ${st.job.sealed ?? 0}, пропущено (уже в порядке) ${st.job.skipped ?? 0}.`}
          {st.job.state === "failed" && `Задание не выполнено (${st.job.error ?? "ошибка"}). Повторите; уже обработанные записи не затрагиваются.`}
        </div>)}
    </section>
  );
}
