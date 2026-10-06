import { version as lkClientVersion } from "livekit-client";
import type { Snapshot, ScreenStats } from "../../diagnostics";
import type { SocketStatus } from "../../liveSocket";

interface Props {
  snapshot: Snapshot | null;
  join: Record<string, number | undefined>;
  connection: string;
  socket: SocketStatus;
  asrReady: boolean;
  log: string[];
}

const v = (x: number | undefined, unit = "", digits = 0) => (x === undefined ? "—" : `${Number(x.toFixed(digits))}${unit}`);

function ScreenRows({ title, s }: { title: string; s?: ScreenStats }) {
  if (!s) return null;
  return (
    <>
      <tr><th colSpan={2}>{title}</th></tr>
      <tr><td>Разрешение</td><td>{s.width && s.height ? `${s.width}×${s.height}` : "—"}</td></tr>
      <tr><td>Кадров/с (FPS)</td><td>{v(s.fps, "", 1)}</td></tr>
      <tr><td>Битрейт</td><td>{v(s.bitrateKbps, " кбит/с")}</td></tr>
      <tr><td>Потеряно пакетов</td><td>{v(s.packetsLost)}</td></tr>
      {s.framesDropped !== undefined && <tr><td>Потеряно кадров</td><td>{v(s.framesDropped)}</td></tr>}
      <tr><td>Jitter</td><td>{v(s.jitterMs, " мс", 1)}</td></tr>
      <tr><td>RTT</td><td>{v(s.rttMs, " мс")}</td></tr>
      {s.limitReason && <tr><td>Ограничение качества</td><td>{s.limitReason === "bandwidth" ? "полоса канала" : s.limitReason === "cpu" ? "процессор отправителя" : s.limitReason}</td></tr>}
    </>
  );
}

/** Режим отладки комнаты: тайминги входа, ICE-кандидат, RTT/потери/битрейт, статистика трансляции экрана, журнал событий. */
export default function DebugPanel({ snapshot, join, connection, socket, asrReady, log }: Props) {
  return (
    <section className="debug card" aria-label="Диагностика комнаты">
      <h2>Диагностика комнаты</h2>
      <div className="debug-grid">
        <table className="table compact"><tbody>
          <tr><th colSpan={2}>Вход</th></tr>
          <tr><td>Запрос /join (API)</td><td>{v(join.join_api_ms, " мс")}</td></tr>
          <tr><td>Сигналинг LiveKit</td><td>{v(join.signaling_connect_ms, " мс")}</td></tr>
          <tr><td>ICE / медиасоединение</td><td>{v(join.ice_connect_ms, " мс")}</td></tr>
          <tr><td>Всего до «подключено»</td><td>{v(join.participant_active_ms, " мс")}</td></tr>
          <tr><td>Публикация микрофона</td><td>{v(join.microphone_publish_ms, " мс")}</td></tr>
          <tr><th colSpan={2}>Соединение</th></tr>
          <tr><td>Состояние LiveKit</td><td>{connection}</td></tr>
          <tr><td>Путь медиа (ICE)</td><td>{snapshot?.candidate ?? "—"}</td></tr>
          <tr><td>RTT</td><td>{v(snapshot?.rttMs, " мс")}</td></tr>
          <tr><td>Потери пакетов</td><td>{v(snapshot?.lossPct, " %", 1)}</td></tr>
          <tr><td>Исходящий / входящий поток</td><td>{v(snapshot?.outKbps, " кбит/с")} / {v(snapshot?.inKbps, " кбит/с")}</td></tr>
          <tr><td>События (WebSocket)</td><td>{socket.state}{socket.attempt ? `, попытка ${socket.attempt}` : ""}</td></tr>
          <tr><td>Транскрибация (ASR)</td><td>{asrReady ? "готова" : "запускается"}</td></tr>
          <tr><td>livekit-client</td><td>{lkClientVersion}</td></tr>
        </tbody></table>
        <table className="table compact"><tbody>
          <ScreenRows title="Мой экран (отправка)" s={snapshot?.screenOut} />
          <ScreenRows title="Экран участника (приём)" s={snapshot?.screenIn} />
          {!snapshot?.screenOut && !snapshot?.screenIn && <tr><td colSpan={2} className="muted">Показ экрана не идёт — статистика появится при его запуске.</td></tr>}
        </tbody></table>
      </div>
      <h3>Журнал</h3>
      <pre className="debug-log">{log.length ? log.join("\n") : "Событий пока нет."}</pre>
    </section>
  );
}
