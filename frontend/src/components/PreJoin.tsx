import type { CSSProperties, ReactNode } from "react";
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
      <div className="pj-card">
        <div className="pj-hero">
          <span className="pj-mark" aria-hidden>{initials(name)}</span>
          <div>
            <h1>{name}</h1>
            <span className={`pill ${live ? "live" : "free"}`}><i className="pulse" aria-hidden />
              {live ? `Идёт встреча · ${live.participants} уч.` : "Пока никого нет — вы начнёте встречу"}</span>
          </div>
        </div>
        <div className="pj-body">
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
          {onHw && !busy && <PreJoinCheck cameraAllowed={room?.camera_allowed !== false} onChange={onHw} />}
          {needPassword && (
            <label>Пароль комнаты
              <input type="password" value={password} onChange={(e) => onPassword(e.target.value)} autoFocus autoComplete="off"
                     onKeyDown={(e) => { if (e.key === "Enter" && password && !busy) onJoin(); }} />
            </label>
          )}
          {error && <div className="alert error" role="alert">{error}</div>}
          <div className="row pj-actions" role="group" aria-label="Вход в комнату">
            <button className="btn primary cta" {...magnet} disabled={busy || (needPassword && !password)} onClick={onJoin}>
              {busy ? <><i className="spin" aria-hidden /> Вход…</> : <>{live ? "Присоединиться" : "Войти в комнату"} <Icon name="arrowR" size={17} /></>}
            </button>
            <button className="btn ghost" onClick={onBack}>Назад к списку</button>
          </div>
          {progress}
        </div>
      </div>
    </section>
  );
}
