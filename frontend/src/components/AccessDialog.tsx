import { useEffect, useState } from "react";
import { api, type AccessState, type ApiError, type DirHit } from "../api";
import { Modal } from "./Dialogs";
import { fmt } from "../util";

type Hit = DirHit & { has_access?: boolean };

/**
 * «Управление доступом» к одной завершённой встрече (руководитель комнаты, администратор). Выдаётся только просмотр материалов ЭТОЙ встречи: получатель не становится
 * руководителем, не видит другие встречи комнаты и не может выдавать доступ дальше. Отзыв действует сразу. Выдача группе — только после отдельного подтверждения с числом людей.
 */
export default function AccessDialog({ meetingId, onClose }: { meetingId: string; onClose: () => void }) {
  const [state, setState] = useState<AccessState | null>(null);
  const [kind, setKind] = useState<"user" | "group">("user");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[]>([]);
  const [hint, setHint] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [group, setGroup] = useState<{ hit: Hit; count: number; tooMany: boolean; max: number; sample: string[] } | null>(null);
  const [ok, setOk] = useState("");
  const load = () => api.access.get(meetingId).then(setState).catch((e) => setErr((e as ApiError).message));
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true); setErr(""); setOk("");
    try { await fn(); await load(); if (done) setOk(done); } catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  };
  const search = async () => {
    setHint(""); setGroup(null); setErr("");
    try { const r = await api.access.search(meetingId, kind, q.trim()); setHits(r); if (!r.length) setHint("Ничего не найдено"); } catch (e) { setHits([]); setErr((e as ApiError).message); }
  };
  const pickGroup = async (h: Hit) => {
    setErr("");
    try { const r = await api.access.groupMembers(meetingId, h.ref); setGroup({ hit: h, count: r.count, tooMany: r.too_many, max: r.max, sample: r.sample }); } catch (e) { setErr((e as ApiError).message); }
  };
  return (
    <Modal title="Управление доступом к встрече" onClose={onClose} wide>
      {err && <div className="alert error" role="alert">{err}</div>}
      {ok && <div className="alert ok" role="status">{ok}</div>}
      {state && <p className="muted small">{state.note}</p>}

      <fieldset className="group"><legend>Выдать доступ</legend>
        <div className="row">
          <select value={kind} onChange={(e) => { setKind(e.target.value as "user" | "group"); setHits([]); setGroup(null); setHint(""); }} aria-label="Что искать">
            <option value="user">Пользователь (ФИО, логин, почта)</option><option value="group">Группа каталога</option></select>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="От 2 символов" aria-label="Поиск в каталоге" onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void search(); } }} />
          <button type="button" className="btn" disabled={q.trim().length < 2 || busy} onClick={() => void search()}>Найти</button>
        </div>
        {hint && <div className="muted small">{hint}</div>}
        {hits.map((h) => (
          <div key={h.ref} className="hit"><span>{h.name}{h.sam ? ` (${h.sam})` : ""} <span className="muted small">{h.email || h.description || ""}</span></span>
            {kind === "user"
              ? (h.has_access ? <span className="badge ok">доступ есть</span>
                : <button type="button" className="btn mini primary" disabled={busy} onClick={() => void run(() => api.access.grant(meetingId, h), `Доступ выдан: ${h.name}`)}>Выдать доступ</button>)
              : <button type="button" className="btn mini" disabled={busy} onClick={() => void pickGroup(h)}>Состав группы…</button>}
          </div>))}
        {group && (
          <div className="alert warn" role="alert">
            <b>Группа «{group.hit.name}»: {group.count} чел.</b>{group.sample.length > 0 && <> Например: {group.sample.join(", ")}{group.count > group.sample.length ? "…" : ""}.</>}
            {group.tooMany
              ? <p>В группе больше {group.max} человек — выдайте доступ по одному или обратитесь к администратору.</p>
              : <><p>Все они получат право просмотра материалов этой встречи. Это нельзя сделать случайно: подтвердите явно.</p>
                <button type="button" className="btn primary" disabled={busy || group.count === 0}
                  onClick={() => void run(async () => { await api.access.grantGroup(meetingId, group.hit.ref, group.count); setGroup(null); setHits([]); }, `Доступ выдан группе «${group.hit.name}»`)}>
                  Подтверждаю: выдать доступ {group.count} чел.</button></>}
          </div>)}
      </fieldset>

      {state && (
        <fieldset className="group"><legend>Кому выдан доступ ({state.grants.length})</legend>
          {state.grants.length === 0 && <div className="muted small">Отдельных разрешений нет.</div>}
          {state.grants.map((g) => (
            <div key={g.user_id} className="hit"><span>{g.display_name} ({g.sam_account_name}) <span className="muted small">выдал {g.granted_by ?? "—"}, {fmt(g.created_at)}</span></span>
              <button type="button" className="btn mini danger" disabled={busy}
                onClick={() => { if (window.confirm(`Отозвать доступ у «${g.display_name}»? Он сразу перестанет видеть материалы этой встречи, в том числе по ранее полученным ссылкам.`)) void run(() => api.access.revoke(meetingId, g.user_id), `Доступ отозван: ${g.display_name}`); }}>Отозвать</button></div>))}
          <div className="muted small">Участники встречи ({state.participants.length}) видят материалы по правилу комнаты{state.history_access === "participants" ? " «участники — всегда»" : ""}; руководители: {state.leaders.join(", ") || "—"}.</div>
        </fieldset>)}
    </Modal>
  );
}
