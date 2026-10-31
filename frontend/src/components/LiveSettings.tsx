import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type LiveBroadcast, type LivePatchBody, type LiveRoomSettings } from "../api";
import { copyText } from "../util";
import { Icon } from "./Icons";
import LiveDestinations from "./LiveDestinations";
import "../live.css";
import InfoTip from "./InfoTip";

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
  const [days, setDays] = useState(5);

  const apply = (c: LiveRoomSettings) => { setCfg(c); setTitle(c.title); setStartsAt(c.starts_at ? toLocal(c.starts_at) : ""); setDays(c.link_days); };
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
        <span className="check-body">Разрешить трансляции в этой комнате<InfoTip>{isBroadcastType ? "Комната создана как трансляционная: выступающие общаются в конференции, зрители смотрят эфир по ссылке." : "Эфир можно вести и в обычной или презентационной комнате: зрители смотрят по ссылке, не подключаясь к конференции."}</InfoTip></span></label>
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
            <span className="check-body">Защитить трансляцию паролем<InfoTip>По умолчанию выключено: любой с действующей ссылкой смотрит без пароля. Пароль защищает и страницу, и сам поток. {cfg.has_password ? "" : "Сначала задайте пароль ниже."}</InfoTip></span></label>
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
          <div className="lv-rec">
            <span className="field-title">Запись эфира<InfoTip>Каждый эфир записывается автоматически (MP4 — общая сцена встречи: выступающие, показ экрана, доска, звук) и хранится по общей политике хранения видеозаписей. Если запись начать нельзя (нет места, служба недоступна), эфир не начнётся — ведущий увидит причину. Параметры ниже действуют для новых эфиров: у уже прошедшего эфира свои значения, их меняют в списке «Эфиры».</InfoTip></span>
            <label className="check"><input type="checkbox" checked={cfg.replay} disabled={busy} onChange={(e) => void patch({ live_replay: e.target.checked }, e.target.checked ? "Новые эфиры: запись будет доступна по ссылке зрителя" : "Новые эфиры: запись зрителям не показывается")} />
              <span className="check-body">Показывать запись после завершения эфира<InfoTip>Та же ссылка зрителя после окончания открывает запись этого эфира — когда она готова. Действует пароль зрителя. Выключено — запись остаётся во внутреннем хранилище, зрителям не выдаётся.</InfoTip></span></label>
            <label className="check"><input type="checkbox" checked={cfg.download} disabled={busy || !cfg.replay} onChange={(e) => void patch({ live_download: e.target.checked }, "Сохранено")} />
              <span className="check-body">Разрешить скачивание записи<InfoTip>На странице зрителя появится «Скачать запись» (MP4). Без этого — только просмотр; полностью исключить копирование воспроизводимого видео технически нельзя.</InfoTip></span></label>
            <label className="lv-days">Ссылка на запись действует
              <input type="number" min={1} max={cfg.link_days_max} value={days} disabled={busy || !cfg.replay} onChange={(e) => setDays(Number(e.target.value))}
                     onBlur={() => { if (days !== cfg.link_days && days >= 1 && days <= cfg.link_days_max) void patch({ live_link_days: days }, "Срок сохранён"); }} />
              <span className="muted">дн. после окончания<InfoTip>{`Срок отсчитывается от фактического окончания эфира. Не больше ${cfg.link_days_max} дн. — предел администратора сервера. Истечение срока закрывает доступ по ссылке, но не удаляет запись: её хранение — по политике хранения видеозаписей.`}</InfoTip></span></label>
          </div>
        </fieldset>

        <LiveDestinations roomId={roomId} cfg={cfg} onChange={apply} />

        {cfg.broadcasts.length > 0 && <LiveBroadcasts roomId={roomId} cfg={cfg} busy={busy} run={run} />}

        <details className="lv-sect"><summary>Дополнительно</summary>
          <label><span className="field-title">Качество<InfoTip>Меняется до начала эфира. Дорожка одна: автоподстройки под канал зрителя нет. Внешним площадкам уходит тот же поток.</InfoTip></span><select value={cfg.quality} disabled={busy || live} onChange={(e) => void patch({ live_quality: e.target.value as "720p" | "1080p" }, "Качество сохранено")}>
              <option value="720p">720p — экономичное (≈ 0,6 ГБ/час записи)</option>
              <option value="1080p">1080p — для презентаций с мелким текстом (≈ 0,8 ГБ/час записи, больше нагрузка)</option>
            </select></label>
          <label className="check"><input type="checkbox" checked={cfg.cdn} disabled={busy || !cfg.cdn_available} onChange={(e) => void patch({ live_cdn: e.target.checked }, e.target.checked ? "Эфир этой комнаты раздаётся через CDN" : "Раздача через CDN выключена")} />
            <span className="check-body">Раздавать через CDN<InfoTip>{cfg.cdn_available ? "Плеер зрителя и ссылка на поток берут видео прямо с CDN администратора, а не с этого сервера — так можно показывать эфир сотням и тысячам зрителей." : "CDN не подключён администратором сервера (Администрирование → Трансляции). Без CDN каждый зритель получает отдельный поток с основного сервера."}</InfoTip></span></label>
          <label className="check"><input type="checkbox" checked={cfg.own_hls} disabled={busy || live} onChange={(e) => void patch({ live_own_hls: e.target.checked }, "Сохранено")} />
            <span className="check-body">Собственный просмотр в Peregovorka (страница зрителя и ссылка на поток)<InfoTip>Выключите, если эфир нужен только внешним площадкам: сервер не будет хранить и раздавать сегменты.</InfoTip></span></label>
        </details>
      </>)}
      {msg && <div className="alert ok" role="status">{msg}</div>}
      {err && <div className="alert error" role="alert">{err}</div>}
    </div>
  );
}

const BCAST_STATUS: Record<LiveBroadcast["status"], string> = { live: "идёт", ending: "завершается", ended: "завершён", failed: "прерван" };
const ACCESS: Record<LiveBroadcast["access"], string> = { off: "запись зрителям не показывается", open: "запись доступна по ссылке", expired: "срок ссылки истёк", revoked: "доступ закрыт" };

/** Эфиры комнаты: у каждого своя ссылка, параметры доступа к записи и срок. Организатор и администратор меняют их здесь; запись — в материалах встречи. */
function LiveBroadcasts({ roomId, cfg, busy, run }: { roomId: string; cfg: LiveRoomSettings; busy: boolean; run: (fn: () => Promise<LiveRoomSettings | void>, ok?: string) => Promise<void> }) {
  const [copied, setCopied] = useState("");
  const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");
  const upd = (b: LiveBroadcast, body: Parameters<typeof api.patchBroadcast>[2], ok: string) => run(() => api.patchBroadcast(roomId, b.id, body), ok);
  return (
    <fieldset className="group lv-bcasts"><legend>Эфиры<InfoTip>Для каждого эфира создаётся своя ссылка зрителя: пока эфир идёт — прямой эфир, после окончания — его запись (если показ разрешён) до конца срока. Повторный эфир из этой комнаты — новая ссылка; старые ссылки остаются привязаны к своим записям.</InfoTip></legend>
      <ul className="lv-bcast-list">
        {cfg.broadcasts.map((b) => {
          const rec = b.recording;
          const recText = !rec ? "записи нет" : rec.file_state === "missing" ? "запись удалена" : rec.status === "ready" ? "запись готова" : rec.status === "failed" ? "запись не сохранилась" : "запись готовится";
          return (
            <li key={b.id} className="lv-bcast">
              <div className="lv-bcast-head">
                <b>{b.title || cfg.title || "Эфир"}</b>
                <span className={`badge ${b.status === "live" ? "live" : b.status === "failed" ? "warn" : ""}`}>{BCAST_STATUS[b.status]}</span>
                <span className="muted small">{fmt(b.started_at)}{b.ended_at ? ` — ${fmt(b.ended_at)}` : ""}{b.password ? " · с паролем" : ""}</span>
              </div>
              <div className="lv-bcast-meta muted small">
                {recText} · {ACCESS[b.access]}{b.replay && b.access_until && b.access === "open" ? ` до ${fmt(b.access_until)}` : ""}{b.download && b.replay ? " · скачивание разрешено" : ""}
              </div>
              <div className="lv-bcast-acts row">
                {b.view_path && <button type="button" className="btn mini" onClick={async () => { const ok = await copyText(`${origin()}${b.view_path}`); setCopied(ok ? b.id : ""); window.setTimeout(() => setCopied(""), 2500); }}>{copied === b.id ? "Скопировано" : "Копировать ссылку"}</button>}
                <a className="btn mini ghost" href={`/history/${b.meeting_id}`}>Запись и материалы</a>
                <label className="check small"><input type="checkbox" checked={b.replay} disabled={busy} onChange={(e) => void upd(b, { replay: e.target.checked }, "Сохранено")} /> показывать запись</label>
                <label className="check small"><input type="checkbox" checked={b.download} disabled={busy || !b.replay} onChange={(e) => void upd(b, { download: e.target.checked }, "Сохранено")} /> скачивание</label>
                {b.status !== "live" && b.replay && (
                  <label className="small">срок <select value={b.link_days} disabled={busy} onChange={(e) => void upd(b, { link_days: Number(e.target.value) }, "Срок изменён")}>
                    {[...new Set([1, 3, 5, 7, 14, 30, 90, 365, b.link_days])].filter((d) => d <= cfg.link_days_max || d === b.link_days).sort((x, y) => x - y).map((d) => <option key={d} value={d}>{d} дн.</option>)}
                  </select></label>)}
                {b.replay && b.access !== "revoked" && b.status !== "live" && <button type="button" className="btn mini danger" disabled={busy} onClick={() => void upd(b, { revoke: true }, "Доступ к записи закрыт")}>Закрыть доступ</button>}
                {b.access === "revoked" && <button type="button" className="btn mini" disabled={busy} onClick={() => void upd(b, { revoke: false }, "Доступ открыт снова")}>Открыть снова</button>}
              </div>
            </li>);
        })}
      </ul>
    </fieldset>
  );
}

/** ISO (UTC) → значение поля datetime-local в часовом поясе браузера. */
function toLocal(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
