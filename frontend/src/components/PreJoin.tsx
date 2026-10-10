import { useState, type CSSProperties, type ReactNode } from "react";
import type { Room } from "../api";
import { initials, magnet, tint } from "../fx";
import { Icon } from "./Icons";
import PreJoinCheck from "./PreJoinCheck";
import type { PreJoin as PreJoinHw } from "../prejoin";

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
}

/** Экран перед входом в комнату: что за комната, кто уже там и что произойдёт после входа (микрофон, стенограмма, запись). */
export default function PreJoin({ room, needPassword, password, onPassword, error, busy, onJoin, onBack, progress, onHw }: Props) {
  const [previewHost, setPreviewHost] = useState<HTMLDivElement | null>(null);       // область крупного предпросмотра камеры слева
  const [micDenied, setMicDenied] = useState(false);          // микрофон заблокирован в браузере: входить можно, но без возможности говорить
  const name = room?.name ?? "Комната";
  const t = tint(name);
  const live = room?.active_meeting;
  // «Перед входом»: обычные сведения одним коротким списком; запись звука — отдельным предупреждением (это единственное, что может удивить)
  const facts: [Parameters<typeof Icon>[0]["name"], string, string][] = [
    ["mic", "Микрофон включится сразу", "Выключить его можно кнопкой в комнате."],
  ];
  if (room?.transcription_enabled !== false) facts.push(["transcript", "Ведётся стенограмма", "Реплики попадут в протокол встречи."]);
  if (room?.lifetime === "temporary") facts.push(["sparkle", "Временная комната", "Закроется после встречи; материалы останутся в «Истории»."]);
  const rec = room?.auto_record ? "Запись звука начнётся сразу. Отметка «Идёт запись» видна всем участникам." : room?.record_audio ? "Руководитель может включить запись звука — об этом появится заметная отметка." : "";

  return (
    <section className="prejoin-pro" style={{ "--ha": t.a, "--hb": t.b } as CSSProperties}>
      <div className={`pj-card${onHw && !busy ? "" : " pj-single"}`}>
        <div className="pj-hero">
          <span className="pj-mark" aria-hidden>{initials(name)}</span>
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
            {rec && <div className="alert warn pj-rec" role="note"><Icon name="record" size={15} /> {rec}</div>}
          </div>
          {onHw && !busy && <div className="pj-col pj-hw"><PreJoinCheck cameraAllowed={room?.camera_allowed !== false} previewHost={previewHost} onChange={(p) => { setMicDenied(p.micDenied); onHw(p); }} /></div>}
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
