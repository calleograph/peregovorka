import { useEffect, useRef } from "react";
import { Icon } from "./Icons";
import "../live.css";

/** Вход в трансляционную комнату: две карточки — «Выступающий» (обычное участие в конференции) и «Зритель» (лёгкий плеер HLS без подключения к LiveKit). */
export default function BroadcastChooser({ name, live, viewers, watch, hasPassword, onSpeaker, onViewer }: {
  name: string; live: boolean; viewers: number; watch: string | null; hasPassword: boolean; onSpeaker: () => void; onViewer: () => void;
}) {
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => { first.current?.focus(); }, []);
  return (
    <section className="bc-choose" aria-labelledby="bc-h">
      <h1 id="bc-h">{name}</h1>
      <p className="live-sub">Как вы хотите присоединиться? {live && <span className="live-tag on"><i aria-hidden />Идёт эфир{viewers ? ` · ${viewers} смотрят` : ""}</span>}</p>
      <div className="bc-cards">
        <button ref={first} type="button" className="bc-card" onClick={onSpeaker}>
          <span className="bc-ico"><Icon name="mic" size={24} /></span>
          <strong>Выступающий</strong>
          <span>Участвовать в конференции: микрофон, камера, демонстрация экрана, общение с другими выступающими.{hasPassword ? " Понадобится пароль выступающего." : ""}</span>
        </button>
        <button type="button" className="bc-card" onClick={onViewer} disabled={!watch}>
          <span className="bc-ico"><Icon name="eye" size={24} /></span>
          <strong>Зритель</strong>
          <span>{watch ? "Смотреть трансляцию: только видео и звук, без микрофона, камеры и подключения к конференции." : "Просмотр для этой комнаты не включён. Выступать можно."}</span>
        </button>
      </div>
    </section>
  );
}
