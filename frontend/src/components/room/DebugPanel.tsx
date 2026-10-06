import { version as lkClientVersion } from "livekit-client";
import type { MediaStats, Snapshot } from "../../diagnostics";
import type { SocketStatus } from "../../liveSocket";

interface Props {
  snapshot: Snapshot | null;
  join: Record<string, number | undefined>;
  connection: string;
  socket: SocketStatus;
  asrReady: boolean;
  log: string[];
  /** Идентификатор текущего объекта Room и сколько объектов создано за сессию (больше 1 — это повторный вход, а не «обычный» reconnect). */
  instance: string;
  roomsCreated: number;
}

const v = (x: number | undefined, unit = "", digits = 0) => (x === undefined ? "—" : `${Number(x.toFixed(digits))}${unit}`);

function Media({ title, s }: { title: string; s?: MediaStats }) {
  if (!s || Object.keys(s).length === 0) return null;
  return (
    <>
      <tr><th colSpan={2}>{title}</th></tr>
      <tr><td>Кодек</td><td>{s.codec ?? "—"}</td></tr>
      <tr><td>Разрешение</td><td>{s.width && s.height ? `${s.width}×${s.height}` : "—"}</td></tr>
      <tr><td>Кадров/с (FPS)</td><td>{v(s.fps, "", 1)}</td></tr>
      <tr><td>Битрейт</td><td>{v(s.bitrateKbps, " кбит/с")}</td></tr>
      <tr><td>Пакеты отправлено / получено</td><td>{v(s.packetsSent)} / {v(s.packetsReceived)}</td></tr>
      <tr><td>Потеряно пакетов</td><td>{v(s.packetsLost)}</td></tr>
      <tr><td>Jitter · RTT</td><td>{v(s.jitterMs, " мс", 1)} · {v(s.rttMs, " мс")}</td></tr>
      <tr><td>NACK · PLI · FIR</td><td>{v(s.nack)} · {v(s.pli)} · {v(s.fir)}</td></tr>
      <tr><td>Кадров закодировано / декодировано / потеряно</td><td>{v(s.framesEncoded)} / {v(s.framesDecoded)} / {v(s.framesDropped)}</td></tr>
      {s.limitReason && <tr><td>Ограничение качества</td><td>{s.limitReason === "bandwidth" ? "полоса канала" : s.limitReason === "cpu" ? "процессор отправителя" : s.limitReason}</td></tr>}
    </>
  );
}

/** Режим отладки комнаты: тайминги входа, жизненный цикл Room, путь ICE, статистика WebRTC камеры и показа экрана, журнал событий. */
export default function DebugPanel({ snapshot, join, connection, socket, asrReady, log, instance, roomsCreated }: Props) {
  const path = snapshot?.path;
  return (
    <section className="debug card" aria-label="Диагностика комнаты">
      <h2>Диагностика комнаты</h2>
      <div className="debug-grid">
        <table className="table compact"><tbody>
          <tr><th colSpan={2}>Вход (весь путь)</th></tr>
          <tr><td>Join API</td><td>{v(join.join_api_ms, " мс")}</td></tr>
          <tr><td>Создание Room</td><td>{v(join.room_create_ms, " мс")}</td></tr>
          <tr><td>Подключение LiveKit (всего)</td><td>{v(join.livekit_connect_ms, " мс")}</td></tr>
          <tr><td>— Signaling</td><td>{v(join.signaling_connect_ms, " мс")}</td></tr>
          <tr><td>— ICE / медиасоединение</td><td>{v(join.ice_connect_ms, " мс")}</td></tr>
          <tr><td>getUserMedia (микрофон)</td><td>{v(join.get_user_media_ms, " мс")}</td></tr>
          <tr><td>Публикация микрофона</td><td>{v(join.microphone_publish_ms, " мс")}</td></tr>
          <tr><td>Канал событий (WebSocket)</td><td>{v(join.backend_ws_connect_ms, " мс")}</td></tr>
          <tr><td><b>Всего до «подключено»</b></td><td><b>{v(join.total_join_ms, " мс")}</b></td></tr>
          <tr><th colSpan={2}>Room и соединение</th></tr>
          <tr><td>Экземпляр Room</td><td><code>{instance || "—"}</code> · создано объектов: {roomsCreated}{roomsCreated > 1 ? " (повторный вход)" : ""}</td></tr>
          <tr><td>Состояние LiveKit</td><td>{connection}</td></tr>
          <tr><td>Путь медиа (ICE)</td><td>{path?.candidate ?? "—"}</td></tr>
          <tr><td>RTT · потери</td><td>{v(snapshot?.rttMs, " мс")} · {v(snapshot?.lossPct, " %", 1)}</td></tr>
          <tr><td>Исходящий / входящий поток</td><td>{v(snapshot?.outKbps, " кбит/с")} / {v(snapshot?.inKbps, " кбит/с")}</td></tr>
          <tr><td>Канал событий (WebSocket)</td><td>{socket.state}{socket.attempt ? `, попытка ${socket.attempt}` : ""}</td></tr>
          <tr><td>Транскрибация (ASR)</td><td>{asrReady ? "готова" : "недоступна/запускается"}</td></tr>
          <tr><td>livekit-client</td><td>{lkClientVersion}</td></tr>
        </tbody></table>
        <table className="table compact"><tbody>
          <Media title="Камера (отправка)" s={snapshot?.camera} />
          <Media title="Мой экран (отправка)" s={snapshot?.screenOut} />
          <Media title="Экран участника (приём)" s={snapshot?.screenIn} />
          {snapshot?.screenFrozen && <tr><td colSpan={2} style={{ color: "var(--danger-text)" }}>Изображение экрана участника «зависло»: счётчик декодированных кадров не растёт.</td></tr>}
          {!snapshot?.camera && !snapshot?.screenOut && !snapshot?.screenIn && <tr><td colSpan={2} className="muted">Видео не идёт — статистика появится при включении камеры или показа экрана.</td></tr>}
        </tbody></table>
      </div>
      <h3>Журнал</h3>
      <pre className="debug-log">{log.length ? log.join("\n") : "Событий пока нет."}</pre>
    </section>
  );
}
