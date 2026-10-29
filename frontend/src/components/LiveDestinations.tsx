import { type FormEvent, useState } from "react";
import { api, type ApiError, type LiveDestination, type LiveDestStatus, type LiveRoomSettings } from "../api";
import { Icon } from "./Icons";
import "../live.css";

/** Подсказки по площадкам: адреса и ключи берутся из кабинета самой площадки — в приложении они не зашиты, здесь только порядок действий. */
const HINTS: Record<string, { title: string; steps: string; server: string }> = {
  rutube: { title: "RUTUBE", steps: "В студии RUTUBE создайте трансляцию (для закрытого показа — доступ по прямой ссылке) и скопируйте «Сервер» и «Ключ потока». Ключ действует до 24 часов с начала трансляции.", server: "Сервер из студии RUTUBE" },
  vk: { title: "VK Видео", steps: "В VK Видео нажмите «Начать трансляцию» и в «Настройке программы для трансляции» скопируйте URL и ключ. Ключ никому не передавайте.", server: "URL из настроек трансляции VK" },
  youtube: { title: "YouTube", steps: "В YouTube Studio откройте настройки потока: URL трансляции (значок замка даёт rtmps://) и ключ трансляции. Прямые эфиры на канале должны быть активированы.", server: "URL трансляции из YouTube Studio" },
  twitch: { title: "Twitch", steps: "В панели автора Twitch скопируйте ключ потока и адрес ближайшего сервера приёма вида rtmp://<сервер>/app.", server: "rtmp://<сервер приёма>/app" },
  custom: { title: "Другой RTMP/RTMPS-сервер", steps: "Адрес сервера вещания (rtmp:// или rtmps://) и ключ потока выдаёт администратор этого сервера.", server: "rtmps://сервер/приложение" },
};
export const STATUS_LABEL: Record<LiveDestStatus, string> = { idle: "не подключена", connecting: "подключается…", live: "идёт", failed: "ошибка", finished: "завершена" };
export const statusClass = (s: LiveDestStatus) => (s === "live" ? "ok" : s === "failed" ? "warn" : "");

function StatusBadge({ d }: { d: LiveDestination }) {
  return <span className={`badge ${statusClass(d.status)}`} title={d.last_error ?? undefined}>{!d.enabled ? "выключена" : STATUS_LABEL[d.status]}</span>;
}

/** «Внешние площадки» в настройках комнаты: список со статусами, добавление (мастер ручного подключения через RTMP-ключ), замена ключа, проверка, удаление. */
export default function LiveDestinations({ roomId, cfg, onChange }: { roomId: string; cfg: LiveRoomSettings; onChange: (c: LiveRoomSettings) => void }) {
  const [edit, setEdit] = useState<{ id: string | null; platform: string } | null>(null);
  const [f, setF] = useState({ name: "", server_url: "", key: "", channel: "", watch_url: "" });
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const flash = (t: string) => { setMsg(t); window.setTimeout(() => setMsg(""), 4000); };
  const run = async (fn: () => Promise<LiveRoomSettings | void>, ok?: string) => {
    setBusy(true); setErr("");
    try { const r = await fn(); if (r) onChange(r); if (ok) flash(ok); return true; }
    catch (e) { setErr((e as ApiError).message || "Не удалось выполнить."); return false; }
    finally { setBusy(false); }
  };
  const open = (platform: string, d?: LiveDestination) => {
    setErr("");
    setEdit({ id: d?.id ?? null, platform });
    setF({ name: d?.name ?? HINTS[platform]?.title ?? "", server_url: d?.server_url ?? "", key: "", channel: d?.channel ?? "", watch_url: d?.watch_url ?? "" });
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!edit) return;
    const body: Record<string, unknown> = { name: f.name, server_url: f.server_url, channel: f.channel, watch_url: f.watch_url };
    if (f.key) body.key = f.key;
    const ok = await run(() => edit.id ? api.patchLiveDestination(roomId, edit.id, body) : api.addLiveDestination(roomId, { ...body, platform: edit.platform, key: f.key }), edit.id ? "Площадка сохранена" : "Площадка добавлена");
    if (ok) setEdit(null);
  };
  const probe = async () => {
    setBusy(true); setErr("");
    try { const r = edit?.id && !f.server_url ? await api.probeLiveDestination(roomId, edit.id) : await api.probeLiveServer(roomId, f.server_url); flash(r.message); if (!r.ok) setErr(r.message); }
    catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  };
  const left = cfg.max_destinations - cfg.destinations.length;
  const hint = edit ? HINTS[edit.platform] : null;
  return (
    <fieldset className="group"><legend>Внешние площадки</legend>
      <p className="lv-note">Тот же эфир параллельно уходит на RUTUBE, VK Видео, YouTube, Twitch или любой RTMP/RTMPS-сервер. Площадки можно включать и выключать во время эфира — он не прерывается; ошибка одной площадки не останавливает остальные. Пароль Peregovorka внешнюю площадку не защищает: используйте приватность самой площадки.</p>
      {cfg.destinations.length === 0 && !edit && <p className="muted">Площадок пока нет.</p>}
      <ul className="lv-dests">
        {cfg.destinations.map((d) => (
          <li key={d.id} className="lv-link">
            <div><b>{d.name}</b> <StatusBadge d={d} /><code>{d.platform_name}{d.channel ? ` · ${d.channel}` : ""} · {d.server_url}</code>
              {d.last_error && d.status === "failed" && <span className="lv-err" role="alert">{d.last_error}</span>}</div>
            <button type="button" className="btn mini" disabled={busy} onClick={() => open(d.platform, d)}>Изменить</button>
            <button type="button" className="btn mini ghost" disabled={busy} onClick={() => { if (window.confirm(`Удалить площадку «${d.name}» вместе с ключом?`)) void run(() => api.deleteLiveDestination(roomId, d.id), "Площадка удалена"); }}>Удалить</button>
          </li>))}
      </ul>
      {!edit && (
        <div className="row" style={{ flexWrap: "wrap" }}>
          {left > 0 ? cfg.platforms.map((p) => <button key={p.id} type="button" className="btn" disabled={busy} onClick={() => open(p.id)}><Icon name="plus" size={16} /> {p.name}</button>)
            : <span className="muted small">Достигнут предел площадок для комнаты ({cfg.max_destinations}).</span>}
        </div>)}
      {edit && hint && (
        <form className="lv-form" onSubmit={(e) => void submit(e)} aria-label="Площадка">
          <h4 style={{ margin: "4px 0" }}>{edit.id ? "Изменение площадки" : `Новая площадка: ${hint.title}`}</h4>
          <p className="lv-note">{hint.steps}</p>
          <label>Название в списке<input value={f.name} maxLength={120} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <label>Адрес сервера вещания<input value={f.server_url} onChange={(e) => setF({ ...f, server_url: e.target.value })} placeholder={hint.server} autoComplete="off" spellCheck={false} required={!edit.id} /></label>
          <label>Ключ потока {edit.id && <span className="muted small">(задан; новый заменит прежний, пусто — не менять)</span>}
            <input type="password" value={f.key} onChange={(e) => setF({ ...f, key: e.target.value })} autoComplete="new-password" required={!edit.id} placeholder="хранится зашифрованно, обратно не показывается" /></label>
          <div className="cols">
            <label>Канал на площадке <span className="muted small">(необязательно)</span><input value={f.channel} maxLength={160} onChange={(e) => setF({ ...f, channel: e.target.value })} /></label>
            <label>Ссылка для зрителей на площадке <span className="muted small">(необязательно)</span><input value={f.watch_url} onChange={(e) => setF({ ...f, watch_url: e.target.value })} placeholder="https://…" /></label>
          </div>
          <div className="row">
            <button className="btn primary" disabled={busy || !f.name.trim()}>{edit.id ? "Сохранить" : "Добавить"}</button>
            <button type="button" className="btn" disabled={busy || (!f.server_url && !edit.id)} onClick={() => void probe()}>Проверить сервер</button>
            <button type="button" className="btn ghost" disabled={busy} onClick={() => setEdit(null)}>Отмена</button>
          </div>
          <p className="lv-note">«Проверить сервер» убеждается, что адрес корректен, разрешён политикой и отвечает по сети. Ключ проверит сама площадка при начале эфира.</p>
        </form>)}
      {msg && <div className="alert ok" role="status">{msg}</div>}
      {err && <div className="alert error" role="alert">{err}</div>}
    </fieldset>
  );
}

/** Компактный список в окне «Трансляция…» во время эфира: включить или выключить площадку на лету. */
export function LiveDestToggles({ meetingId, items, onChanged }: { meetingId: string; items: LiveDestination[]; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState("");
  if (!items.length) return null;
  const toggle = async (d: LiveDestination) => {
    setBusy(d.id); setErr("");
    try { await api.toggleLiveDestination(meetingId, d.id, !d.enabled); onChanged(); }
    catch (e) { setErr((e as ApiError).message || "Не удалось переключить площадку."); }
    finally { setBusy(null); }
  };
  return (
    <div className="lv-links" aria-label="Внешние площадки">
      <b className="small">Внешние площадки</b>
      {items.map((d) => (
        <label key={d.id} className="lv-link check">
          <input type="checkbox" checked={d.enabled} disabled={busy === d.id} onChange={() => void toggle(d)} />
          <span className="check-body"><span>{d.name} <StatusBadge d={d} /></span>
            {d.status === "failed" && d.last_error && <span className="lv-err">{d.last_error}</span>}
            {d.watch_url && <a href={d.watch_url} target="_blank" rel="noopener noreferrer">Открыть на площадке</a>}</span>
        </label>))}
      {err && <div className="alert error" role="alert">{err}</div>}
    </div>
  );
}
