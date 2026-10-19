import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { Room } from "../api";
import { magnet, tint } from "../fx";
import { useSite } from "../site";
import { Icon } from "./Icons";
import PreJoinCheck from "./PreJoinCheck";
import type { PreJoin as PreJoinHw } from "../prejoin";
import { initialMic, micFacts, type JoinMode } from "../prejoinMode";
import RoomIcon from "./RoomIcon";

interface Props {
  room?: Room | null;
  needPassword: boolean;
  password: string;
  onPassword: (v: string) => void;
  error: string;
  busy: boolean;
  onJoin: () => void;
  onBack: () => void;
  progress?: ReactNode;
  /** Выбор устройств с проверки оборудования; комната подхватит его при входе. */
  onHw?: (p: PreJoinHw & { micOk: boolean }) => void;
  /** Кем пользователь войдёт (см. prejoinMode.ts): от этого зависят подписи, проверка оборудования и выбор состояния микрофона. */
  mode?: JoinMode;
}

/** Экран перед входом в комнату: что за комната, кто уже там и что произойдёт после входа (микрофон, стенограмма, запись). */
export default function PreJoin({ room, needPassword, password, onPassword, error, busy, onJoin, onBack, progress, onHw, mode = "regular" }: Props) {
  const [previewHost, setPreviewHost] = useState<HTMLDivElement | null>(null);       // область крупного предпросмотра камеры слева
  const site = useSite();
  const [micDenied, setMicDenied] = useState(false);          // микрофон заблокирован в браузере: входить можно, но без возможности говорить
  const name = room?.name ?? "Комната";
  const t = tint(name);
  const live = room?.active_meeting;
  // «Перед входом»: обычные сведения одним коротким списком; запись звука — отдельным предупреждением (это единственное, что может удивить)
  // Состояние микрофона при входе выбирает руководитель презентации; зритель входит с выключенными микрофоном и камерой; обычная комната — как раньше.
  const [micOn, setMicOn] = useState(() => initialMic(mode, room?.mute_on_join));
  const lastHw = useRef<(PreJoinHw & { micOk: boolean }) | null>(null);
  useEffect(() => { if (lastHw.current && onHw) onHw({ ...lastHw.current, micOn }); }, [micOn]);       // eslint-disable-line react-hooks/exhaustive-deps
  const facts: [Parameters<typeof Icon>[0]["name"], string, string][] = micFacts(mode, micOn).map((f) => [f.icon, f.title, f.text]);
  if (room?.transcription_enabled !== false) facts.push(["transcript", "Ведётся стенограмма", "Реплики попадут в протокол встречи."]);
  if (room?.lifetime === "temporary") facts.push(["sparkle", "Временная комната", "Закроется после встречи; материалы останутся в «Истории»."]);
  const rec = room?.auto_record ? "Запись звука начнётся сразу. Отметка «Идёт запись» видна всем участникам." : room?.record_audio ? "Руководитель может включить запись звука — об этом появится заметная отметка." : "";

  return (
    <section className="prejoin-pro" style={{ "--ha": t.a, "--hb": t.b } as CSSProperties}>
      <div className={`pj-card${onHw && !busy ? "" : " pj-single"}`}>
        <div className="pj-hero">
          <RoomIcon name={name} icon={room?.icon} size={58} className="pj-mark" />
          <div>
            <h1>{name}</h1>
            <span className={`pill ${live ? "live" : "free"}`}><i className="pulse" aria-hidden />
              {live ? `Идёт встреча · ${live.participants} уч.` : "Пока никого нет — вы начнёте встречу"}</span>
          </div>
        </div>
        <div className="pj-body">
          <div className="pj-col pj-info">
            {onHw && !busy && room?.camera_allowed !== false && <div className="pj-preview" ref={setPreviewHost} />}
            {room?.description && <p className="pj-desc">{room.description}</p>}
            <h2 className="pj-h">Перед входом</h2>
            <ul className="pj-facts" aria-label="Что произойдёт после входа">
              {facts.map(([icon, title, text], i) => (
                <li key={title} style={{ "--i": i } as CSSProperties}>
                  <span className="fi" aria-hidden><Icon name={icon} size={16} /></span>
                  <div><b>{title}</b><p>{text}</p></div>
                </li>
              ))}
            </ul>
            {room?.e2ee_mode && room.e2ee_mode !== "off" && (
              <div className="alert info pj-rec" role="note"><b>Сквозное шифрование звука и видео.</b> {room.e2ee_mode === "full"
                ? "Сервер не имеет ключа: стенограмма и серверная запись звука в этой комнате отключены. Нужен современный браузер."
                : "Стенограмму и запись выполняет доверенный компонент сервера, которому комната выдаёт ключ встречи. Нужен современный браузер."}</div>
            )}
            {rec && <div className="alert warn pj-rec" role="note"><Icon name="record" size={15} /> {rec}</div>}
            {site.recording_text && <div className="alert info pj-rec" role="note" style={{ whiteSpace: "pre-wrap" }}>{site.recording_text}</div>}
          </div>
          {onHw && !busy && (
            <div className="pj-col pj-hw">
              {mode === "leader" && (
                <div className="pc-card pj-micstate" role="radiogroup" aria-label="Микрофон при входе">
                  <div className="pc-h"><b>Микрофон при входе</b><span className="pc-note">ваш выбор</span></div>
                  <div className="pc-btns">
                    <button type="button" role="radio" aria-checked={micOn} className={`btn mini${micOn ? " primary" : ""}`} onClick={() => setMicOn(true)}>Включён</button>
                    <button type="button" role="radio" aria-checked={!micOn} className={`btn mini${!micOn ? " primary" : ""}`} onClick={() => setMicOn(false)}>Выключен</button>
                  </div>
                  <p className="pc-p">{micOn ? "После входа вас сразу будет слышно." : "Вы войдёте с выключенным микрофоном и включите его, когда начнёте говорить."}</p>
                </div>
              )}
              <PreJoinCheck cameraAllowed={room?.camera_allowed !== false} previewHost={previewHost} onChange={(p) => { setMicDenied(p.micDenied); lastHw.current = p; onHw({ ...p, micOn }); }} />
            </div>
          )}
        </div>
        <div className="pj-foot">
          {needPassword && (
            <label className="pj-pass">Пароль комнаты
              <input type="password" value={password} onChange={(e) => onPassword(e.target.value)} autoFocus autoComplete="off"
                     onKeyDown={(e) => { if (e.key === "Enter" && password && !busy) onJoin(); }} />
            </label>
          )}
          {micDenied && <div className="alert warn pj-nomic" role="alert"><b>Вы войдёте без возможности говорить:</b> микрофон заблокирован в браузере.</div>}
          {error && <div className="alert error" role="alert">{error}</div>}
          {progress}
          <div className="row pj-actions" role="group" aria-label="Вход в комнату">
            <button className="btn primary cta" {...magnet} disabled={busy || (needPassword && !password)} onClick={onJoin}>
              {busy ? <><i className="spin" aria-hidden /> Вход…</> : <>{live ? "Присоединиться" : "Войти в переговорку"} <Icon name="arrowR" size={17} /></>}
            </button>
            <button className="btn ghost" onClick={onBack}>Назад к списку</button>
          </div>
        </div>
      </div>
    </section>
  );
}
