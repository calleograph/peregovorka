import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError, type LiveStartOptions, type LiveState } from "../api";
import InfoTip from "./InfoTip";
import { copyText } from "../util";
import { Icon } from "./Icons";
import { Modal } from "./Dialogs";
import LiveDestinations, { LiveDestToggles } from "./LiveDestinations";
import LiveSettings, { QualitySelect } from "./LiveSettings";
import "../live.css";

/** Состояние прямого эфира встречи: читается при входе, по событию `live_changed` и раз в 20 с (событие могло потеряться). */
export function useLive(meetingId: string | null | undefined) {
  const [state, setState] = useState<LiveState | null>(null);
  const [startOpts, setStartOpts] = useState<LiveStartOptions | null>(null);      // блок «Этот эфир»: общий для окна «Трансляция» и красной кнопки; null — значения комнаты
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
  const start = useCallback(async () => {
    await api.setLive(meetingId as string, true, startOpts ?? undefined);
    setStartOpts(null);
    refresh();
  }, [meetingId, startOpts, refresh]);
  return { state, refresh, start, startOpts, setStartOpts };
}

/** Панель вопросов в комнате: всем в трансляционной комнате; в обычной и презентационной — ведущим, пока идёт эфир и после него (зрители спрашивают там же, а участников могут быть сотни). */
export const showLiveQuestions = (o: { broadcastRoom: boolean; canControl: boolean; wasLive: boolean }): boolean => o.broadcastRoom || (o.canControl && o.wasLive);

const origin = () => window.location.origin;
const clockSince = (iso: string | null) => {
  if (!iso) return "";
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h} ч ${m} мин` : `${m} мин`;
};
const STATUS: Record<string, string> = { idle: "Эфир не идёт", starting: "Эфир запускается…", live: "Идёт прямой эфир", ending: "Эфир заканчивается…", ended: "Эфир завершён", failed: "Эфир прерван" };

/** Окно «Трансляция» в меню «Ещё»: всё для эфира в самой комнате — запуск и остановка, качество, площадки с ключами, ссылки, пароли. Те же блоки, что в «Настройках комнаты → Трансляция». */
export function LiveDialog({ meetingId, room, state, startOpts, onStartOpts, onStart, onChanged, onClose, onDiag, onSpeakerPassword }: { meetingId: string; startOpts: LiveStartOptions | null; onStartOpts: (o: LiveStartOptions) => void; onStart: () => Promise<void>; room: { id: string; slug: string; room_type?: string; has_password?: boolean }; state: LiveState; onChanged: () => void; onClose: () => void; onDiag?: () => void; onSpeakerPassword: (pw: string | null) => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [done, setDone] = useState("");
  const links = state.links;
  // параметры именно этого эфира: по умолчанию — из настроек комнаты, ведущий может поменять их перед запуском (после запуска они фиксируются у эфира)
  const opts: LiveStartOptions = startOpts ?? { title: links?.title ?? "", replay: links?.replay ?? false, download: links?.download ?? false, link_days: links?.link_days ?? 5, quality: undefined };
  const setOpts = onStartOpts;
  const current = links?.broadcasts.find((b) => b.status === "live");
  useEffect(() => {          // статусы площадок меняются сами: окно не должно показывать «идёт» у площадки, которая уже отказала
    if (!state.live) return undefined;
    const t = window.setInterval(onChanged, 4000);
    return () => window.clearInterval(t);
  }, [state.live, onChanged]);
  const toggle = async () => {
    setBusy(true); setErr("");
    try {
      if (state.live) { await api.setLive(meetingId, false); onChanged(); } else await onStart();
    }
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
    <Modal title="Трансляция" onClose={onClose} wide>
      <div className="lv-panel">
        <div className="lv-state">
          {state.live ? <span className="live-tag on big"><i aria-hidden />Прямой эфир</span> : <span className="badge">{STATUS[state.status] ?? state.status}</span>}
          {state.live && <span className="muted">идёт {clockSince(state.since)} · зрителей: {state.viewers}{links ? ` · ${current?.quality ?? links.quality}` : ""}</span>}
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
        {!state.live && state.can_control && links && (
          <QualitySelect title="Качество этого эфира" value={opts.quality ?? links.quality} disabled={busy} onChange={(q) => setOpts({ ...opts, quality: q })}
            hint={`Только для этого эфира: запись эфира идёт в том же качестве. В настройках комнаты («Трансляция») сейчас ${links.quality}; их можно изменить там, и это качество подставится само. Качество видеозаписи встреч (720p) к эфиру не относится.`} />)}
        {!state.live && state.can_control && links && opts.quality && opts.quality !== links.quality && <p className="muted small lv-note">Эфир начнётся в {opts.quality}, в настройках комнаты остаётся {links.quality}.</p>}
        {!state.live && state.can_control && links && (
          <fieldset className="group lv-opts"><legend>Этот эфир<InfoTip>Значения подставлены из настроек комнаты. Здесь их можно поменять только для этого эфира: после запуска они сохраняются у эфира и не меняются от последующих изменений настроек комнаты. Кнопка «Выйти в эфир» запускает эфир с этими же значениями. Качество — настройка комнаты, оно задаётся выше.</InfoTip></legend>
            <label>Название для зрителей <input value={opts.title ?? ""} maxLength={200} onChange={(e) => setOpts({ ...opts, title: e.target.value })} placeholder="Название мероприятия" /></label>
            <label className="check"><input type="checkbox" checked={!!opts.replay} onChange={(e) => setOpts({ ...opts, replay: e.target.checked })} /> Показывать запись по ссылке после эфира</label>
            {opts.replay && <div className="row lv-opt-row">
              <label className="check"><input type="checkbox" checked={!!opts.download} onChange={(e) => setOpts({ ...opts, download: e.target.checked })} /> Разрешить скачивание</label>
              <label>Срок ссылки <input type="number" min={1} max={links.link_days_max} value={opts.link_days ?? 5} onChange={(e) => setOpts({ ...opts, link_days: Number(e.target.value) })} /> дн.</label>
            </div>}
          </fieldset>)}
        {current?.view_path && (
          <div className="lv-link">
            <div><b>Ссылка этого эфира</b><code>{`${origin()}${current.view_path}`}</code><span className="muted small">{current.replay ? `после эфира откроет запись (${current.link_days} дн.)` : "после эфира запись по ней не показывается"}</span></div>
            <button className="btn primary" onClick={() => void copy(current.view_path, "Ссылка эфира")}><Icon name="copy" size={16} /> Копировать</button>
          </div>)}
        {!state.live && state.can_control && <p className="lv-note">Эфир начинается с текущего момента. Эфир записывается автоматически (MP4 сохранится в «Истории»). Конференцию выступающих это не прерывает. Качество — {state.quality}.</p>}
        {state.live && !state.hls_ready && state.own_hls && <p className="lv-note">Поток готовится — зрители увидят эфир через несколько секунд.</p>}
        {warnBig && <p className="lv-note warn">Зрителей уже {state.viewers}: прямая раздача с этого сервера рассчитана на небольшую аудиторию. Для больших мероприятий подключите CDN (администратору: Администрирование → Трансляции).</p>}
        {links && (
          <div className="lv-links">
            <div className="lv-link">
              <div><b>Постоянная ссылка комнаты</b><code>{links.view_path ? `${origin()}${links.view_path}` : "не создана"}</code></div>
              <button className="btn" onClick={() => void copy(links.view_path, "Ссылка зрителя")} disabled={!links.view_path}><Icon name="copy" size={16} /> Копировать ссылку зрителя</button>
            </div>
            <div className="lv-link">
              <div><b>Ссылка на поток (HLS)</b><code>{links.stream_path ? "для внешних плееров; действует по подписи" : "собственный просмотр выключен"}</code></div>
              <button className="btn" onClick={() => void copy(links.stream_path, "Ссылка на поток")} disabled={!links.stream_path || !links.own_hls}><Icon name="link" size={16} /> Копировать ссылку на поток</button>
            </div>
            {links.password_enabled && <p className="lv-note">Просмотр защищён паролем зрителя. Ссылка на поток пароль не спрашивает: делитесь ею осторожно, её можно отозвать в настройках комнаты.</p>}
          </div>)}
        {links && state.can_edit
          ? <LiveDestinations roomId={room.id} cfg={{ ...links, destinations: state.destinations ?? links.destinations }} onChange={onChanged} meetingId={state.live ? meetingId : undefined} onRefresh={onChanged} />
          : state.can_control && state.destinations && <LiveDestToggles meetingId={meetingId} items={state.destinations} onChanged={onChanged} />}
        {links && state.can_edit && <details className="lv-sect"><summary>Пароли, ссылки и другие настройки</summary>
          <LiveSettings roomId={room.id} roomSlug={room.slug} isBroadcastType={room.room_type === "broadcast"} hasSpeakerPassword={!!room.has_password} onSpeakerPassword={onSpeakerPassword} embedded />
        </details>}
        {done && <div className="alert ok" role="status">{done}</div>}
        {onDiag && <div className="row"><button className="btn ghost" onClick={onDiag}><Icon name="gauge" size={16} /> Диагностика эфира</button></div>}
      </div>
    </Modal>
  );
}
