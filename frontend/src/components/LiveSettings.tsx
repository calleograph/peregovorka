import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type LivePatchBody, type LiveRoomSettings } from "../api";
import { copyText } from "../util";
import { Icon } from "./Icons";
import LiveDestinations from "./LiveDestinations";
import "../live.css";

/**
 * «Настройки комнаты → Трансляция»: всё, что нужно владельцу, на одном экране — ссылки (копировать, отозвать, выпустить заново), пароли выступающих и зрителей,
 * режим запуска, качество, запись. Сложное (качество, собственный просмотр) — в раскрывающемся «Дополнительно».
 * Пароль выступающего — это пароль комнаты; он задаётся здесь же и хранится так же (argon2id), пароль зрителя — отдельный.
 */
const origin = () => window.location.origin;

export default function LiveSettings({ roomId, roomSlug, isBroadcastType, hasSpeakerPassword, onSpeakerPassword }: {
  roomId: string; roomSlug: string; isBroadcastType: boolean; hasSpeakerPassword: boolean; onSpeakerPassword: (pw: string | null) => Promise<void>;
}) {
  const [cfg, setCfg] = useState<LiveRoomSettings | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [viewerPw, setViewerPw] = useState("");
  const [speakerPw, setSpeakerPw] = useState("");
  const [title, setTitle] = useState("");
  const [startsAt, setStartsAt] = useState("");

  const apply = (c: LiveRoomSettings) => { setCfg(c); setTitle(c.title); setStartsAt(c.starts_at ? toLocal(c.starts_at) : ""); };
  const load = useCallback(() => { api.liveRoom(roomId).then(apply).catch((e) => setErr((e as ApiError).message)); }, [roomId]);
  useEffect(() => { load(); }, [load]);
  const flash = (t: string) => { setMsg(t); window.setTimeout(() => setMsg(""), 3500); };
  const run = async (fn: () => Promise<LiveRoomSettings | void>, ok?: string) => {
    setBusy(true); setErr("");
    try { const r = await fn(); if (r) apply(r); if (ok) flash(ok); }
    catch (e) { setErr((e as ApiError).message || "Не удалось сохранить."); }
    finally { setBusy(false); }
  };
  const patch = (b: LivePatchBody, ok?: string) => run(() => api.patchLive(roomId, b), ok);
  const copy = async (path: string | null | undefined, what: string) => {
    if (!path) return;
    flash((await copyText(path.startsWith("http") ? path : `${origin()}${path}`)) ? `${what} скопирована` : "Не удалось скопировать — выделите ссылку вручную");
  };
  const speakerUrl = `${origin()}/rooms/${roomSlug}?as=speaker`;

  if (!cfg) return <div role="tabpanel">{err ? <div className="alert error" role="alert">{err}</div> : <p className="muted">Загрузка…</p>}</div>;
  const live = cfg.state.live;
  const setViewer = (e: FormEvent) => { e.preventDefault(); if (viewerPw) void run(async () => { const r = await api.setLivePassword(roomId, viewerPw); setViewerPw(""); return r; }, "Пароль зрителя сохранён: прежние ссылки на видео перестали действовать"); };
  const setSpeaker = (e: FormEvent) => { e.preventDefault(); if (speakerPw) void run(async () => { await onSpeakerPassword(speakerPw); setSpeakerPw(""); load(); }, "Пароль выступающего сохранён"); };

  return (
    <div role="tabpanel" className="lv-panel">
      {!cfg.global.enabled && <div className="alert warn">Трансляции пока отключены администратором сервера (Администрирование → Трансляции). Настройки можно подготовить заранее.</div>}
      <label className="check"><input type="checkbox" checked={cfg.enabled} disabled={busy || (live && cfg.enabled)} onChange={(e) => void patch({ live_enabled: e.target.checked }, e.target.checked ? "Трансляция в комнате включена" : "Трансляция в комнате выключена")} />
        <span className="check-body">Разрешить трансляции в этой комнате<span className="help">{isBroadcastType ? "Комната создана как трансляционная: выступающие общаются в конференции, зрители смотрят эфир по ссылке." : "Эфир можно вести и в обычной или презентационной комнате: зрители смотрят по ссылке, не подключаясь к конференции."}</span></span></label>
      {cfg.enabled && (<>
        {cfg.state.live && <p className="lv-note"><span className="live-tag on"><i aria-hidden />Идёт эфир</span> зрителей: {cfg.state.viewers}. Пароли и ссылки можно менять сразу — конференция и запись не прерываются.</p>}

        <fieldset className="group"><legend>Участники и доступ</legend>
          <h4 style={{ margin: "2px 0" }}>Выступающие</h4>
          <div className="lv-link">
            <div><b>Ссылка выступающих</b><code>{speakerUrl}</code></div>
            <button type="button" className="btn" onClick={() => void copy(speakerUrl, "Ссылка выступающих")}><Icon name="copy" size={16} /> Скопировать</button>
          </div>
          <form className="row" onSubmit={setSpeaker} aria-label="Пароль выступающего">
            <label style={{ flex: "1 1 220px" }}>Пароль выступающего {(hasSpeakerPassword || cfg.speaker_password_set) && <span className="muted small">(задан; новый заменит прежний)</span>}
              <input type="password" autoComplete="new-password" value={speakerPw} onChange={(e) => setSpeakerPw(e.target.value)} maxLength={128} placeholder="не короче 4 знаков" /></label>
            <button className="btn" disabled={busy || speakerPw.length < 4}>{cfg.speaker_password_set ? "Изменить пароль" : "Задать пароль"}</button>
            {cfg.speaker_password_set && <button type="button" className="btn ghost" disabled={busy} onClick={() => void run(async () => { await onSpeakerPassword(null); load(); }, "Пароль выступающего сброшен")}>Сбросить</button>}
          </form>
          <p className="lv-note">Пароль нужен тем, кто входит как выступающий. Он не даёт прав руководителя: запись, участники и эфир управляются по вашим ролям. Владелец, руководители и администраторы входят без пароля.</p>

          <h4 style={{ margin: "10px 0 2px" }}>Зрители</h4>
          <div className="lv-link">
            <div><b>Ссылка зрителя</b><code>{cfg.view_path ? `${origin()}${cfg.view_path}` : "—"}</code></div>
            <button type="button" className="btn" onClick={() => void copy(cfg.view_path, "Ссылка зрителя")}><Icon name="copy" size={16} /> Копировать ссылку зрителя</button>
          </div>
          <div className="lv-link">
            <div><b>Ссылка на поток (HLS)</b><code>{cfg.stream_path ? "для внешних плееров и интеграций" : "собственный просмотр выключен"}</code></div>
            <button type="button" className="btn" disabled={!cfg.stream_path || !cfg.own_hls} onClick={() => void copy(cfg.stream_path, "Ссылка на поток")}><Icon name="link" size={16} /> Копировать ссылку на поток</button>
          </div>
          <div className="row">
            <button type="button" className="btn ghost" disabled={busy} onClick={() => { if (window.confirm("Выпустить новую ссылку зрителя? Прежняя перестанет открываться сразу, а смотрящие потеряют доступ.")) void run(() => api.revokeLive(roomId, "view"), "Выпущена новая ссылка зрителя"); }}>Отозвать и создать новую ссылку зрителя</button>
            <button type="button" className="btn ghost" disabled={busy} onClick={() => { if (window.confirm("Отозвать ссылку на поток? Внешние плееры перестанут её открывать.")) void run(() => api.revokeLive(roomId, "stream"), "Ссылка на поток отозвана — скопируйте новую"); }}>Отозвать ссылку на поток</button>
          </div>

          <label className="check"><input type="checkbox" checked={cfg.password_enabled} disabled={busy || (!cfg.password_enabled && !cfg.has_password)} onChange={(e) => void patch({ live_password_enabled: e.target.checked }, e.target.checked ? "Просмотр защищён паролем" : "Пароль зрителя выключен: просмотр по ссылке без пароля")} />
            <span className="check-body">Защитить трансляцию паролем<span className="help">По умолчанию выключено: любой с действующей ссылкой смотрит без пароля. Пароль защищает и страницу, и сам поток. {cfg.has_password ? "" : "Сначала задайте пароль ниже."}</span></span></label>
          <form className="row" onSubmit={setViewer} aria-label="Пароль зрителя">
            <label style={{ flex: "1 1 220px" }}>Пароль зрителя {cfg.has_password && <span className="muted small">(задан; новый заменит прежний)</span>}
              <input type="password" autoComplete="new-password" value={viewerPw} onChange={(e) => setViewerPw(e.target.value)} maxLength={128} placeholder="не короче 6 знаков, не как у выступающих" /></label>
            <button className="btn" disabled={busy || viewerPw.length < 6}>{cfg.has_password ? "Изменить пароль" : "Задать пароль"}</button>
            {cfg.has_password && <button type="button" className="btn ghost" disabled={busy} onClick={() => void run(() => api.setLivePassword(roomId, null), "Пароль зрителя удалён")}>Удалить</button>}
          </form>
          <p className="lv-note">Пароль зрителя ничего не открывает, кроме просмотра этого эфира. При смене пароля или отзыве ссылки смотрящие теряют доступ в течение нескольких секунд (плеер получает отказ при следующем обращении к потоку). Пароль Peregovorka не защищает ссылки на внешних площадках: там действуют их настройки приватности.</p>
        </fieldset>

        <fieldset className="group"><legend>Эфир</legend>
          <label>Название мероприятия <span className="muted small">(показывается зрителям)</span>
            <input value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} onBlur={() => { if (title !== cfg.title) void patch({ live_title: title }, "Название сохранено"); }} placeholder="Например: Квартальный отчёт" /></label>
          <label>Начало <span className="muted small">(страница ожидания покажет время и отсчёт)</span>
            <input type="datetime-local" value={startsAt} onChange={(e) => setStartsAt(e.target.value)} onBlur={() => { const iso = startsAt ? new Date(startsAt).toISOString() : null; const cur = cfg.starts_at ? new Date(cfg.starts_at).toISOString() : null; if (iso !== cur) void patch(iso ? { live_starts_at: iso } : { clear_starts_at: true }, "Время начала сохранено"); }} /></label>
          <label>Запуск эфира
            <select value={cfg.start} disabled={busy} onChange={(e) => void patch({ live_start: e.target.value as "manual" | "auto" }, "Режим запуска сохранён")}>
              <option value="manual">Вручную: руководитель нажимает «Начать эфир»</option>
              <option value="auto">Автоматически: эфир начинается, когда подключился первый выступающий</option>
            </select></label>
          <label className="check"><input type="checkbox" checked={cfg.record} disabled={busy || live} onChange={(e) => void patch({ live_record: e.target.checked }, "Сохранено")} />
            <span className="check-body">Сохранять запись эфира в историю (MP4)<span className="help">Запись идёт тем же процессом, что и эфир: лишней нагрузки нет. Файл появится в «Истории» после завершения.</span></span></label>
        </fieldset>

        <LiveDestinations roomId={roomId} cfg={cfg} onChange={apply} />

        <details className="lv-sect"><summary>Дополнительно</summary>
          <label>Качество
            <select value={cfg.quality} disabled={busy || live} onChange={(e) => void patch({ live_quality: e.target.value as "720p" | "1080p" }, "Качество сохранено")}>
              <option value="720p">720p — экономичное (≈ 0,6 ГБ/час записи)</option>
              <option value="1080p">1080p — для презентаций с мелким текстом (≈ 0,8 ГБ/час записи, больше нагрузка)</option>
            </select><span className="help">Меняется до начала эфира. Дорожка одна: автоподстройки под канал зрителя нет. Внешним площадкам уходит тот же поток.</span></label>
          <label className="check"><input type="checkbox" checked={cfg.own_hls} disabled={busy || live} onChange={(e) => void patch({ live_own_hls: e.target.checked }, "Сохранено")} />
            <span className="check-body">Собственный просмотр в Peregovorka (страница зрителя и ссылка на поток)<span className="help">Выключите, если эфир нужен только внешним площадкам: сервер не будет хранить и раздавать сегменты.</span></span></label>
        </details>
      </>)}
      {msg && <div className="alert ok" role="status">{msg}</div>}
      {err && <div className="alert error" role="alert">{err}</div>}
    </div>
  );
}

/** ISO (UTC) → значение поля datetime-local в часовом поясе браузера. */
function toLocal(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
