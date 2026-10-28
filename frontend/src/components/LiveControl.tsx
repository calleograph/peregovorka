import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError, type LiveState } from "../api";
import { copyText } from "../util";
import { Icon } from "./Icons";
import { Modal } from "./Dialogs";
import "../live.css";

/** Состояние прямого эфира встречи: читается при входе, по событию `live_changed` и раз в 20 с (событие могло потеряться). */
export function useLive(meetingId: string | null | undefined) {
  const [state, setState] = useState<LiveState | null>(null);
  const alive = useRef(true);
  const refresh = useCallback(() => {
    if (!meetingId) return;
    api.liveState(meetingId).then((s) => { if (alive.current) setState(s); }).catch(() => undefined);
  }, [meetingId]);
  useEffect(() => {
    alive.current = true;
    refresh();
    const t = window.setInterval(refresh, 20_000);
    return () => { alive.current = false; window.clearInterval(t); };
  }, [refresh]);
  return { state, refresh };
}

const origin = () => window.location.origin;
const clockSince = (iso: string | null) => {
  if (!iso) return "";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h} ч ${m} мин` : `${m} мин`;
};
const STATUS: Record<string, string> = { idle: "Эфир не идёт", starting: "Эфир запускается…", live: "Идёт прямой эфир", ending: "Эфир заканчивается…", ended: "Эфир завершён", failed: "Эфир прерван" };

/** Окно «Трансляция» в меню «Ещё»: запуск и остановка эфира, две ссылки, сведения. Настройки (пароль, качество, площадки) — в «Настройках комнаты → Трансляция». */
export function LiveDialog({ meetingId, state, onChanged, onClose, onSettings }: { meetingId: string; state: LiveState; onChanged: () => void; onClose: () => void; onSettings?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState("");
  const links = state.links;
  const toggle = async () => {
    setBusy(true); setErr("");
    try { await api.setLive(meetingId, !state.live); onChanged(); }
    catch (e) { setErr((e as ApiError).message || "Не удалось переключить эфир."); }
    finally { setBusy(false); }
  };
  const copy = async (path: string | null | undefined, what: string) => {
    if (!path) return;
    const ok = await copyText(path.startsWith("http") ? path : `${origin()}${path}`);
    setDone(ok ? `${what} скопирована` : "Не удалось скопировать — выделите ссылку вручную");
    window.setTimeout(() => setDone(""), 3000);
  };
  const warnBig = links && state.viewers >= links.global.direct_viewers_warn;
  return (
    <Modal title="Трансляция" onClose={onClose}>
      <div className="lv-panel">
        <div className="lv-state">
          {state.live ? <span className="live-tag on big"><i aria-hidden />Прямой эфир</span> : <span className="badge">{STATUS[state.status] ?? state.status}</span>}
          {state.live && <span className="muted">идёт {clockSince(state.since)} · зрителей: {state.viewers}</span>}
        </div>
        {state.status === "failed" && state.error && <div className="alert error" role="alert">{state.error}</div>}
        {err && <div className="alert error" role="alert">{err}</div>}
        {!state.available && <div className="alert warn">Трансляции отключены администратором сервера.</div>}
        {state.can_control && state.available && (
          <div className="lv-big">
            <button className={`btn ${state.live ? "danger" : "primary"}`} onClick={() => void toggle()} disabled={busy || state.status === "ending" || (!state.live && !state.can_start)}>
              <Icon name={state.live ? "stop" : "broadcast"} size={18} /> {busy ? "Подождите…" : state.live ? "Остановить эфир" : "Начать эфир"}
            </button>
          </div>)}
        {!state.live && state.can_control && <p className="lv-note">Эфир начинается с текущего момента. {state.record ? "Запись MP4 сохранится в «Истории». " : "Запись выключена. "}Конференцию выступающих это не прерывает. Качество — {state.quality}.</p>}
        {state.live && !state.hls_ready && state.own_hls && <p className="lv-note">Поток готовится — зрители увидят эфир через несколько секунд.</p>}
        {warnBig && <p className="lv-note warn">Зрителей уже {state.viewers}: прямая раздача с этого сервера рассчитана на небольшую аудиторию. Для больших мероприятий подключите CDN (администратору: Администрирование → Трансляции).</p>}
        {links && (
          <div className="lv-links">
            <div className="lv-link">
              <div><b>Ссылка зрителя</b><code>{links.view_path ? `${origin()}${links.view_path}` : "не создана"}</code></div>
              <button className="btn" onClick={() => void copy(links.view_path, "Ссылка зрителя")} disabled={!links.view_path}><Icon name="copy" size={16} /> Копировать ссылку зрителя</button>
            </div>
            <div className="lv-link">
              <div><b>Ссылка на поток (HLS)</b><code>{links.stream_path ? "для внешних плееров; действует по подписи" : "собственный просмотр выключен"}</code></div>
              <button className="btn" onClick={() => void copy(links.stream_path, "Ссылка на поток")} disabled={!links.stream_path || !links.own_hls}><Icon name="link" size={16} /> Копировать ссылку на поток</button>
            </div>
            {links.password_enabled && <p className="lv-note">Просмотр защищён паролем зрителя. Ссылка на поток пароль не спрашивает: делитесь ею осторожно, её можно отозвать в настройках комнаты.</p>}
          </div>)}
        {done && <div className="alert ok" role="status">{done}</div>}
        {links && onSettings && <div className="row"><button className="btn ghost" onClick={onSettings}>Настройки трансляции…</button></div>}
      </div>
    </Modal>
  );
}
