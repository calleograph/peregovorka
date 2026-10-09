import type { CSSProperties, ReactNode } from "react";
import type { Room } from "../api";
import { initials, magnet, tint } from "../fx";
import { Icon } from "./Icons";

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
}

/** Экран перед входом в комнату: что за комната, кто уже там и что произойдёт после входа (микрофон, стенограмма, запись). */
export default function PreJoin({ room, needPassword, password, onPassword, error, busy, onJoin, onBack, progress }: Props) {
  const name = room?.name ?? "Комната";
  const t = tint(name);
  const live = room?.active_meeting;
  const facts: [Parameters<typeof Icon>[0]["name"], string, string, boolean][] = [
    ["mic", "Микрофон включится сразу", "Его можно выключить кнопкой в комнате. В презентационной комнате участники слушают, пока им не дадут слово.", true],
  ];
  if (room?.transcription_enabled !== false) facts.push(["transcript", "Идёт стенограмма", "Реплики участников записываются в текст и потом попадают в протокол встречи.", true]);
  if (room?.auto_record) facts.push(["record", "Запись звука начнётся сразу", "Аудио сохраняется вместе со встречей. Отметка «Идёт запись» видна всем участникам.", false]);
  else if (room?.record_audio) facts.push(["record", "Руководитель может включить запись", "Если запись включат, об этом появится заметная отметка.", false]);
  if (room?.lifetime === "temporary") facts.push(["sparkle", "Временная переговорка", "Комната закроется сама вскоре после выхода всех. Материалы останутся в «Истории».", true]);

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
          <ul className="pj-facts" aria-label="Что произойдёт после входа">
            {facts.map(([icon, title, text, ok], i) => (
              <li key={title} style={{ "--i": i } as CSSProperties} className={ok ? "" : "warn"}>
                <span className="fi" aria-hidden><Icon name={icon} size={17} /></span>
                <div><b>{title}</b><p>{text}</p></div>
              </li>
            ))}
          </ul>
          {needPassword && (
            <label>Пароль комнаты
              <input type="password" value={password} onChange={(e) => onPassword(e.target.value)} autoFocus autoComplete="off"
                     onKeyDown={(e) => { if (e.key === "Enter" && password && !busy) onJoin(); }} />
            </label>
          )}
          {error && <div className="alert error" role="alert">{error}</div>}
          <div className="row pj-actions">
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
