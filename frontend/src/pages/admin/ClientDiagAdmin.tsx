import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type ClientEventRow, type ClientMetricRow, type MicByClientRow } from "../../api";
import InfoTip from "../../components/InfoTip";

const EVENT_LABEL: Record<string, string> = {
  join_ok: "Вход выполнен", join_failed: "Вход не удался", disconnected: "Отключение от LiveKit", reconnecting: "Переподключение", reconnected: "Соединение восстановлено",
  rejoin_started: "Повторный вход начат", rejoin_failed: "Повторный вход не удался",
  screen_share_started: "Показ экрана начат", screen_share_restarted: "Показ экрана начат повторно", screen_share_stopped: "Показ экрана остановлен", screen_share_failed: "Показ экрана не удался",
  screen_share_ended_by_browser: "Показ остановлен кнопкой браузера", screen_track_published: "Трек экрана опубликован", screen_track_unpublished: "Трек экрана снят", screen_track_ended: "Трек экрана завершён",
  device_error: "Ошибка устройства", mic_failed: "Микрофон: ошибка", camera_failed: "Камера: ошибка", publish_failed: "Публикация не удалась", autoplay_blocked: "Звук заблокирован браузером",
  backend_ws_connected: "Канал событий подключён", backend_ws_reconnecting: "Канал событий: переподключение",
  mic_busy: "Микрофон занят", mic_lost: "Микрофон пропал во время встречи", mic_recovered: "Микрофон вернулся сам", mic_recovery_failed: "Микрофон не вернулся",
  mic_silent: "Звук с микрофона не поступает", mic_permission_denied: "Микрофон: доступ запрещён", join_without_mic: "Вход без микрофона",
};
const MIC_COLS: [string, string][] = [["mic_busy", "занят"], ["mic_failed", "не открылся"], ["mic_lost", "пропал"], ["mic_recovered", "вернулся сам"], ["mic_recovery_failed", "не вернулся"], ["mic_silent", "тишина"], ["mic_retry_recovered", "открылся со 2–4 попытки"], ["mic_retry_failed", "повтор не помог"]];

/** Сбои микрофона по видам клиентов (Edge, Chrome, приложение для Windows) за 30 дней: сравнение по реальной работе пользователей. */
function MicByClient() {
  const [rows, setRows] = useState<MicByClientRow[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.admin.micByClient(30).then((d) => setRows(d.rows), (e) => setErr((e as ApiError).message)); }, []);
  return (
    <>
      <h3>Микрофон по клиентам, 30 дней</h3>
      <p className="muted small">Сколько входов во встречи и сколько сбоев микрофона было у каждого вида клиента — из журнала событий, без ручных проверок.<InfoTip>Если при тех же пользователях у приложения для Windows сбоев на 100 входов заметно больше, чем у Edge, разница подтверждена. «Открылся со 2–4 попытки» — устройство ещё не было освобождено и через долю секунды открылось (гонка освобождения, а не чужая блокировка); «не открылся» — отказ без признаков занятости (часто защитная программа или драйвер). Данные хранятся столько, сколько журнал событий.</InfoTip></p>
      {err && <div className="alert error">{err}</div>}
      <div style={{ overflowX: "auto" }}>
        <table className="table compact"><thead><tr><th>Клиент</th><th>Входов</th><th>Пользователей</th>{MIC_COLS.map(([, l]) => <th key={l}>{l}</th>)}<th>Сбоев на 100 входов</th></tr></thead><tbody>
          {rows && rows.length === 0 && <tr><td colSpan={MIC_COLS.length + 4} className="muted">За 30 дней входов не было.</td></tr>}
          {(rows ?? []).map((r) => <tr key={r.client}><td>{r.client}</td><td>{r.joins}</td><td>{r.users}</td>{MIC_COLS.map(([k]) => <td key={k}>{String(r[k] ?? 0)}</td>)}<td>{r.problems_per_100_joins ?? "—"}</td></tr>)}
        </tbody></table>
      </div>
    </>
  );
}

const t = (ts: number) => new Date(ts * 1000).toLocaleTimeString("ru-RU");
const n = (v: unknown, unit = "") => (typeof v === "number" ? `${Math.round(v * 10) / 10}${unit}` : "—");

/** События и метрики, присланные браузерами участников: причины остановки показа экрана, ошибки устройств, качество связи. */
export default function ClientDiagAdmin({ meetingId }: { meetingId?: string } = {}) {
  const [events, setEvents] = useState<ClientEventRow[]>([]);
  const [metrics, setMetrics] = useState<ClientMetricRow[]>([]);
  const [lifecycle, setLifecycle] = useState<ClientEventRow[]>([]);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const load = useCallback(() => (meetingId ? api.admin.meetingDiagnostics(meetingId) : api.admin.clientDiagnostics()).then((d) => { setEvents(d.events); setMetrics(d.metrics); setLifecycle(d.lifecycle ?? []); setNote((d as { retention_note?: string }).retention_note ?? ""); setErr(""); }).catch((e) => setErr((e as ApiError).message)), []);
  useEffect(() => { void load(); const i = window.setInterval(load, 10000); return () => window.clearInterval(i); }, [load]);
  return (
    <section>
      <div className="row">{meetingId ? <h3 style={{ margin: 0 }}>Подключение, сигнализация, ICE/WebRTC, задержки и ошибки</h3> : <h2>Диагностика клиентов</h2>}<div className="spacer" /><button className="btn mini" onClick={load}>Обновить</button></div>
      {meetingId && <p className="muted small">{note || "Данные браузеров участников этой встречи."}{events.length + metrics.length + lifecycle.length === 0 ? " Сведений об этой встрече нет: участники ещё не присылали данные, либо они уже удалены по сроку хранения." : ""}</p>}
      <p className="muted small">События и замеры качества из браузеров участников (последние 200, хранятся сутки).<InfoTip>Содержимого разговоров и секретов здесь нет. Помогает отличить проблемы сети и браузера от проблем сервера: например, причины остановки показа экрана, потери пакетов и FPS.</InfoTip></p>
      {err && <div className="alert error">{err}</div>}
      {!meetingId && <MicByClient />}
      <h3>События</h3>
      <table className="table compact"><thead><tr><th>Время</th><th>Пользователь</th><th>Событие</th><th>Причина</th><th>Подробности</th></tr></thead><tbody>
        {events.length === 0 && <tr><td colSpan={5} className="muted">Событий пока нет.</td></tr>}
        {events.slice(0, 100).map((e, i) => <tr key={i}><td>{t(e.ts)}</td><td>{e.user}</td><td>{EVENT_LABEL[e.event] ?? e.event}</td><td><code>{e.reason ?? ""}</code></td><td className="small">{e.detail ?? ""}</td></tr>)}
      </tbody></table>
      <h3>Жизненный цикл Room и показа экрана</h3>
      <p className="muted small">Фазы ROOM_CREATE → CONNECT_START → SIGNALING_CONNECTED → ICE_CONNECTED → CONNECT_OK → RECONNECTING/RECONNECTED → DISCONNECTED → ROOM_DISPOSE и SCREEN_*. Одинаковый <code>instance</code> у всех фаз — один и тот же объект (нормальное переподключение); новый <code>instance</code> с <code>reason=rejoin</code> — создан новый Room.</p>
      <table className="table compact"><thead><tr><th>Время</th><th>Пользователь</th><th>Фаза</th><th>Подробности</th></tr></thead><tbody>
        {lifecycle.length === 0 && <tr><td colSpan={4} className="muted">Фаз пока нет.</td></tr>}
        {lifecycle.slice(0, 120).map((e, i) => <tr key={i}><td>{t(e.ts)}</td><td>{e.user}</td><td><code>{e.reason ?? ""}</code></td><td className="small">{e.detail ?? ""}</td></tr>)}
      </tbody></table>
      <h3>Качество связи</h3>
      <div style={{ overflowX: "auto" }}>
        <table className="table compact"><thead><tr><th>Время</th><th>Пользователь</th><th>Вход, мс</th><th>RTT</th><th>Потери</th><th>Путь медиа</th><th>Исх./вх., кбит/с</th><th>Экран: FPS · битрейт · ограничение</th></tr></thead><tbody>
          {metrics.length === 0 && <tr><td colSpan={8} className="muted">Замеров пока нет.</td></tr>}
          {metrics.slice(0, 100).map((m, i) => {
            const sc = (m.screen ?? {}) as Record<string, unknown>;
            return <tr key={i}><td>{t(m.ts)}</td><td>{m.user}</td><td>{n(m.participant_active_ms)}</td><td>{n(m.rtt_ms, " мс")}</td><td>{n(m.packet_loss_pct, " %")}</td><td>{String(m.candidate ?? "—")}</td>
              <td>{n(m.bitrate_out_kbps)} / {n(m.bitrate_in_kbps)}</td><td>{sc.fps !== undefined && sc.fps !== null ? `${n(sc.fps)} · ${n(sc.bitrate_kbps)} · ${String(sc.limit_reason ?? "нет")}` : "—"}</td></tr>;
          })}
        </tbody></table>
      </div>
    </section>
  );
}
