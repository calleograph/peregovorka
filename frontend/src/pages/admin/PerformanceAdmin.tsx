import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError, type PerfData } from "../../api";
import { LEVEL_TEXT, NO_DATA, asrQueueText, barTone, gib, mbps, num, pct, tracksText } from "../../performanceMath";

const POLL_MS = 5000;

function Bar({ value }: { value: number | null }) {
  const v = value == null ? 0 : Math.max(0, Math.min(100, Math.round(value)));
  return <div className={`bar ${barTone(value)}`} aria-hidden><i style={{ width: `${v}%` }} /></div>;
}

function Row({ k, v, hint }: { k: string; v: React.ReactNode; hint?: string }) {
  return <tr><td title={hint}>{k}</td><td>{v}</td></tr>;
}

/**
 * «Администрирование → Состояние → Производительность»: обновляется само раз в 5 секунд, пока страница открыта и видна;
 * один лёгкий запрос (чтение /proc, счётчики из базы и кэш звонкового сервера). Чего получить нельзя — «Нет данных».
 */
export default function PerformanceAdmin() {
  const [d, setD] = useState<PerfData | null>(null);
  const [err, setErr] = useState("");
  const [paused, setPaused] = useState(false);
  const busy = useRef(false);

  const load = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try { setD(await api.admin.performance()); setErr(""); }
    catch (e) { setErr((e as ApiError).message || "Не удалось получить показатели"); }
    finally { busy.current = false; }
  }, []);
  useEffect(() => {
    void load();
    if (paused) return;
    const t = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, POLL_MS);
    return () => window.clearInterval(t);
  }, [load, paused]);

  if (!d) return <section className="card"><h2>Производительность</h2>{err ? <div className="alert error" role="alert">{err}</div> : <p className="muted">Загрузка…</p>}</section>;
  const lvl = LEVEL_TEXT[d.assessment.level];
  const h = d.host, lk = d.livekit, g = d.jobs.gate;
  return (
    <div className="perf">
      <section className="card">
        <div className="row">
          <h2 style={{ margin: 0 }}>Производительность</h2>
          <span className={`badge ${lvl.tone}`} title={lvl.hint}>{lvl.text}</span>
          <div className="spacer" />
          <label className="check"><input type="checkbox" checked={paused} onChange={(e) => setPaused(e.target.checked)} /><span className="check-body">Остановить обновление</span></label>
          <button className="btn mini" onClick={() => void load()}>Обновить</button>
        </div>
        <p className="muted small">Обновляется само каждые 5 секунд. Показатель, который нельзя получить, отмечен «{NO_DATA}». Ничего тяжёлого для сбора не запускается.</p>
        {err && <div className="alert error small" role="alert">{err} — показаны последние полученные данные.</div>}
        {d.assessment.reasons.length > 0 && <ul className="small perf-reasons">{d.assessment.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
      </section>

      <div className="grid-2">
        <section className="card">
          <h3>Сервер</h3>
          <div className="row"><span>Процессор</span><b>{pct(h.cpu_pct)}</b><span className="muted small">{h.cpus} vCPU</span></div>
          <Bar value={h.cpu_pct} />
          <div className="row" style={{ marginTop: 8 }}><span>Память занята</span><b>{pct(h.mem_used_pct)}</b><span className="muted small">{h.mem_total ? `всего ${gib(h.mem_total)}, доступно ${gib(h.mem_available)}` : NO_DATA}</span></div>
          <Bar value={h.mem_used_pct} />
          <table className="table compact" style={{ marginTop: 8 }}><tbody>
            <Row k="Нагрузка (1 / 5 / 15 мин)" v={h.load1 == null ? NO_DATA : `${num(h.load1, "", 2)} / ${num(h.load5, "", 2)} / ${num(h.load15, "", 2)}`} hint="Очередь на процессор; около числа ядер — предел" />
            <Row k="Этот контейнер (backend): процессор" v={h.container.cpu_pct == null ? NO_DATA : `${num(h.container.cpu_pct, "%")} одного ядра`} />
            <Row k="Этот контейнер (backend): память" v={`${gib(h.container.mem_bytes)}${h.container.mem_limit ? ` из ${gib(h.container.mem_limit)}` : ""}`} />
          </tbody></table>
          <p className="muted small">{d.containers.note}</p>
        </section>

        <section className="card">
          <h3>Встречи и зрители</h3>
          <table className="table compact"><tbody>
            <Row k="Идущих встреч" v={d.rooms.active_meetings} />
            <Row k="Участников в комнатах" v={d.rooms.participants} />
            <Row k="Презентаций идёт" v={d.rooms.presentations} />
            <Row k="Людей в презентациях" v={d.rooms.audience_in_presentations} />
          </tbody></table>
          {d.rooms.top.length > 0 && (
            <table className="table compact" style={{ marginTop: 6 }}><thead><tr><th>Самые людные комнаты</th><th>Людей</th></tr></thead><tbody>
              {d.rooms.top.map((r) => <tr key={r.name}><td>{r.name}{r.type === "presentation" ? " · презентация" : ""}</td><td>{r.participants}</td></tr>)}
            </tbody></table>
          )}
        </section>

        <section className="card">
          <h3>Сервер звонков (LiveKit)</h3>
          {!lk.available && <div className="alert warn small" role="status">Сервер звонков не отвечает на запрос статистики{lk.error ? ` (${lk.error})` : ""}.</div>}
          <table className="table compact"><tbody>
            <Row k="Комнат" v={num(lk.rooms)} />
            <Row k="Участников" v={num(lk.participants)} />
            <Row k="Публикующих" v={num(lk.publishers)} hint="Участники, у которых есть опубликованные дорожки; зрители презентации сюда не входят" />
            <Row k="Потоков" v={tracksText(lk.audio_tracks, lk.video_tracks)} hint="Из метрик LiveKit; нужны включённые метрики (prometheus_port, по умолчанию в compose включены)" />
            <Row k="Трафик входящий" v={mbps(lk.in_kbps)} />
            <Row k="Трафик исходящий" v={mbps(lk.out_kbps)} />
            <Row k="Потеряно пакетов на сервере" v={lk.dropped_pps == null ? NO_DATA : `${num(lk.dropped_pps, "пак/с", 1)}`} />
          </tbody></table>
          {!lk.metrics && <p className="muted small">Метрики LiveKit недоступны — трафик и число потоков показать нельзя. Они включаются строкой prometheus_port в настройках LiveKit (см. docs/PERFORMANCE.md).</p>}
        </section>

        <section className="card">
          <h3>Показатели WebRTC у участников</h3>
          <table className="table compact"><tbody>
            <Row k="Отчётов за 5 минут" v={d.webrtc.samples} />
            <Row k="Задержка (RTT)" v={num(d.webrtc.rtt_ms, "мс", 0)} />
            <Row k="Потери пакетов" v={num(d.webrtc.packet_loss_pct, "%", 1)} />
            <Row k="Джиттер" v={num(d.webrtc.jitter_ms, "мс", 1)} />
            <Row k="Входящий битрейт (у клиента)" v={mbps(d.webrtc.bitrate_in_kbps)} />
            <Row k="Исходящий битрейт (у клиента)" v={mbps(d.webrtc.bitrate_out_kbps)} />
          </tbody></table>
          <p className="muted small">{d.webrtc.note}</p>
        </section>

        <section className="card">
          <h3>Распознавание речи (GigaAM)</h3>
          {!d.asr.ok && <div className="alert warn small" role="status">Служба распознавания не сообщает о готовности.</div>}
          <table className="table compact"><tbody>
            <Row k="Очередь реплик" v={asrQueueText(d.asr.queue_depth)} />
            <Row k="Встреч в обработке" v={num(d.asr.active_meetings)} />
            <Row k="Среднее время распознавания" v={num(d.asr.avg_infer_ms, "мс")} />
            <Row k="Среднее ожидание в очереди" v={num(d.asr.avg_queue_ms, "мс")} />
            <Row k="RTF (доля времени на секунду речи)" v={num(d.asr.rtf, "", 2)} hint="Меньше 1 — распознавание быстрее речи" />
            <Row k="Потоков torch (intra / interop)" v={d.asr.torch_threads == null ? NO_DATA : `${d.asr.torch_threads} / ${d.asr.torch_interop_threads ?? "—"}`} hint="Если вместе с LLM и LiveKit потоков больше, чем ядер, они мешают друг другу" />
            <Row k="Потеряно реплик / ошибок" v={`${num(d.asr.dropped)} / ${num(d.asr.errors)}`} />
            <Row k="Очередь записи аудио" v={d.asr.recorder_queue == null ? NO_DATA : `${d.asr.recorder_queue} КБ, потеряно ${num(d.asr.recorder_dropped)}`} />
          </tbody></table>
        </section>

        <section className="card">
          <h3>Фоновые задачи</h3>
          <table className="table compact"><tbody>
            <Row k="Тяжёлых задач идёт / ждёт" v={`${g.running} / ${g.waiting} (одновременно не больше ${g.concurrency})`} hint="Сведение общей записи, волновая форма, протокол, карта разговора" />
            <Row k="Отложено из-за нагрузки" v={`${g.deferred} раз, всего ${num(g.deferred_seconds, "с")}`} hint="Пока идёт встреча и сервер занят, такие задачи ждут" />
            <Row k="Запущено после предельного ожидания" v={g.forced} hint="Ожидание ограничено: работа не «голодает»" />
            <Row k="Протоколов в очереди" v={d.jobs.protocols_pending} />
            <Row k="Карт разговора в работе" v={d.jobs.maps_pending} />
            <Row k="Записей собирается" v={d.jobs.recordings_processing} />
            <Row k="Переносов хранилища" v={d.jobs.storage_transfers_active} />
          </tbody></table>
        </section>

        <section className="card">
          <h3>События встреч (WebSocket)</h3>
          <table className="table compact"><tbody>
            <Row k="Подписанных соединений" v={d.events.subscribers} />
            <Row k="Каналов встреч" v={d.events.channels} />
            <Row k="Сообщений принято / доставлено" v={`${num(d.events.messages)} / ${num(d.events.delivered)}`} />
            <Row k="В очередях сейчас" v={d.events.queued} />
            <Row k="Отключено медленных получателей" v={d.events.slow_dropped} hint="Получатель не успевал за потоком; клиент переподключился и догрузил состояние" />
          </tbody></table>
        </section>

        <section className="card">
          <h3>Сеть сервера (для LiveKit)</h3>
          <table className="table compact"><tbody>
            {Object.entries(d.kernel.params).map(([k, v]) => <Row key={k} k={k} v={<>{v.value == null ? NO_DATA : v.value.toLocaleString("ru-RU")} <span className={v.ok === false ? "bad-text" : "muted"}>(рекомендовано от {v.recommended.toLocaleString("ru-RU")})</span></>} />)}
          </tbody></table>
          {d.kernel.note && <p className="muted small">{d.kernel.note}</p>}
        </section>
      </div>
    </div>
  );
}
