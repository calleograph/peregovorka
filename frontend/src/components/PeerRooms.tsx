import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type ApiError, type PeerPolicy, type PeerRoom } from "../api";
import { Modal } from "./Dialogs";
import { copyText, fmt } from "../util";

const linkOf = (r: PeerRoom) => `${window.location.origin}${r.link}`;

/** «Равноправная комната»: название → ссылка. Все участники равны, ведущего нет; закрывается после встречи. */
export function PeerCreateDialog({ policy, onClose }: { policy: PeerPolicy; onClose: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [room, setRoom] = useState<PeerRoom | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);
  const create = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { setRoom(await api.peer.create(name.trim())); } catch (x) { setErr((x as ApiError).message); }
    setBusy(false);
  };
  return (
    <Modal title="Равноправная комната" onClose={onClose}>
      {!room ? (
        <form className="form" onSubmit={(e) => void create(e)}>
          <p className="small muted">Комната на одну встречу без ведущего: у всех участников равные права — микрофон, камера, экран, доска. Войти может любой сотрудник, у которого есть ссылка. Комната закроется сама после встречи{policy.ttl_hours ? `, а если в неё никто не войдёт — через ${policy.ttl_hours} ч` : ""}.</p>
          <label>Название встречи (необязательно)<input autoFocus value={name} maxLength={200} onChange={(e) => setName(e.target.value)} placeholder="Например: Быстрое согласование" /></label>
          {err && <div className="alert error" role="alert">{err}</div>}
          <div className="row"><button className="btn primary" disabled={busy}>{busy ? "Создание…" : "Создать"}</button><button type="button" className="btn ghost" onClick={onClose}>Отмена</button></div>
        </form>
      ) : (
        <div className="form">
          <p>Комната создана. Отправьте ссылку участникам:</p>
          <input readOnly value={linkOf(room)} onFocus={(e) => e.currentTarget.select()} aria-label="Ссылка на комнату" />
          <div className="row">
            <button className="btn" onClick={async () => setCopied(await copyText(linkOf(room)))}>{copied ? "Скопировано ✓" : "Копировать ссылку"}</button>
            <button className="btn primary" onClick={() => { onClose(); navigate(`/rooms/${room.slug}`); }}>Войти</button>
          </div>
          <p className="small muted">Ссылку можно перевыпустить, продлить срок или ограничить доступ в «Управлении ссылкой» (меню комнаты в списке).</p>
        </div>
      )}
    </Modal>
  );
}

/** Технические права владельца: ссылка, срок, доступ, приглашённые, закрытие. */
export function PeerManageDialog({ roomId, onClose, onChanged }: { roomId: string; onClose: () => void; onChanged: () => void }) {
  const [room, setRoom] = useState<PeerRoom | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ id: string; name: string; login: string; department: string | null }[]>([]);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.peer.get(roomId).then(setRoom).catch((e) => setErr((e as ApiError).message)); }, [roomId]);
  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    const t = window.setTimeout(() => { api.peer.users(q).then(setHits).catch(() => setHits([])); }, 250);
    return () => window.clearTimeout(t);
  }, [q]);
  const act = async (fn: () => Promise<PeerRoom | void>) => {
    setBusy(true); setErr("");
    try { const r = await fn(); if (r) setRoom(r); onChanged(); } catch (e) { setErr((e as ApiError).message); }
    setBusy(false);
  };
  return (
    <Modal title="Управление равноправной комнатой" onClose={onClose}>
      {err && <div className="alert error" role="alert">{err}</div>}
      {room && (
        <div className="form">
          <label>Ссылка<input readOnly value={linkOf(room)} onFocus={(e) => e.currentTarget.select()} /></label>
          <div className="row">
            <button className="btn" onClick={async () => setCopied(await copyText(linkOf(room)))}>{copied ? "Скопировано ✓" : "Копировать"}</button>
            <button className="btn" disabled={busy} onClick={() => { if (window.confirm("Прежняя ссылка перестанет работать. Вошедшие участники останутся в комнате. Перевыпустить?")) void act(() => api.peer.rotate(roomId)); }}>Перевыпустить ссылку</button>
          </div>
          <p className="small muted">Закроется {room.expires_at ? fmt(room.expires_at) : "после встречи"} (если встреча к тому времени не идёт). <button className="btn mini" disabled={busy} onClick={() => void act(() => api.peer.update(roomId, { extend_hours: 4 }))}>Продлить на 4 ч</button></p>
          <fieldset className="mic-prefs">
            <legend>Кто может войти</legend>
            <label className="check"><input type="radio" name="peer-access" checked={room.access === "link"} disabled={busy} onChange={() => void act(() => api.peer.update(roomId, { access: "link" }))} /><span className="check-body">Любой сотрудник со ссылкой</span></label>
            <label className="check"><input type="radio" name="peer-access" checked={room.access === "invited"} disabled={busy} onChange={() => void act(() => api.peer.update(roomId, { access: "invited" }))} /><span className="check-body">Только приглашённые<span className="help">Войти смогут вы, администратор и добавленные ниже.</span></span></label>
          </fieldset>
          {room.access === "invited" && (
            <div>
              <label>Добавить приглашённого<input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Фамилия или логин (от 2 букв)" /></label>
              {hits.length > 0 && <ul className="peer-hits">{hits.map((h) => <li key={h.id}><button type="button" className="btn mini" disabled={busy} onClick={() => { setQ(""); void act(() => api.peer.update(roomId, { invited_add: [h.id] })); }}>+ {h.name}{h.department ? ` · ${h.department}` : ""}</button></li>)}</ul>}
              <ul className="peer-invited">{room.invited.length === 0 ? <li className="muted small">Пока никого не приглашено</li> : room.invited.map((i) => <li key={i.ref}>{i.name || i.ref} <button type="button" className="btn mini ghost" disabled={busy} onClick={() => void act(() => api.peer.update(roomId, { invited_remove: [i.ref] }))} aria-label={`Убрать ${i.name || i.ref}`}>✕</button></li>)}</ul>
            </div>
          )}
          <div className="row"><button className="btn ghost danger" disabled={busy} onClick={() => { if (window.confirm("Закрыть комнату? История и материалы встречи сохранятся.")) void act(async () => { await api.peer.close(roomId); onClose(); }); }}>Закрыть комнату</button></div>
        </div>
      )}
    </Modal>
  );
}
