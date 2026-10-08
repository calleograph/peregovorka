import { useCallback, useEffect, useState } from "react";
import { version as lkClientVersion } from "livekit-client";
import { api, type ApiError, type JournalStats, type SystemStatus } from "../../api";
import { bytes, downloadText, versionLabel } from "../../util";
import ComponentsTable from "./ComponentsTable";
import RepairsPanel from "./RepairsPanel";

const TIMING_LABEL: Record<string, [string, string]> = {
  join_backend_ms: ["Обработка входа на сервере", "Время работы backend над запросом «Войти» (БД и выдача пропуска) без сети и прокси."],
  join_api_ms: ["Запрос входа (браузер → backend)", "Полный круг запроса «Войти» с сетью и прокси. Большая разница с предыдущей строкой — задержка в сети или на прокси."],
  signaling_connect_ms: ["Подключение к серверу звонков", "Установка сигналинга LiveKit (WebSocket). Долго — проблемы с прокси/WebSocket или медленная сеть."],
  ice_connect_ms: ["Установка медиасоединения (ICE)", "Согласование пути для звука/видео. Долго — закрыты UDP/TCP-порты медиа, VPN, сложная сеть."],
  participant_active_ms: ["От нажатия «Войти» до комнаты", "Главный показатель: ориентир 1–2 секунды."],
  microphone_publish_ms: ["Публикация микрофона", "От запроса микрофона до публикации. Включает время, пока пользователь отвечает на запрос браузера."],
  room_create_ms: ["Создание Room", "От ответа /join до созданного объекта Room в браузере: должно быть единицы миллисекунд."],
  livekit_connect_ms: ["Room.connect (всего)", "Сигналинг + ICE. Главный кандидат на «лишние секунды»: 404 на /rtc/v1 с откатом на /rtc, WebSocket на прокси, закрытые порты медиа."],
  get_user_media_ms: ["getUserMedia (микрофон)", "Идёт ПАРАЛЛЕЛЬНО подключению; включает время ответа пользователя на запрос браузера."],
  backend_ws_connect_ms: ["Канал событий (WebSocket)", "Открытие /api/v1/ws; независим от LiveKit и на вход в комнату не влияет."],
  total_join_ms: ["Всего: «Войти» → в комнате", "Итоговый показатель; цель 1–2 с в LAN/VPN."],
  asr_join_ms: ["Вход ASR в комнату", "От старта встречи до входа сервиса распознавания в комнату (на вход пользователя не влияет)."],
  asr_first_segment_ms: ["Первая реплика встречи", "От конца первой фразы до её публикации: включает «холодный» старт распознавания."],
};

const pct = (n: number) => Math.max(0, Math.min(100, Math.round(n)));
function Bar({ value, warn = 70, bad = 90 }: { value: number; warn?: number; bad?: number }) {
  return <div className={`bar ${value >= bad ? "bad" : value >= warn ? "warn" : ""}`}><i style={{ width: `${pct(value)}%` }} /></div>;
}
const ms = (v?: number | null) => (v === undefined || v === null ? "—" : `${v} мс`);

/** Состояние системы: ресурсы, сервисы, ASR, комнаты и пользователи, хранилища, версии, времена входа; скачивание диагностического отчёта. */
export default function SystemAdmin({ onOpen }: { onOpen?: (page: string) => void } = {}) {
  const [s, setS] = useState<SystemStatus | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [problems, setProblems] = useState<string[] | null>(null);
  const [busy, setBusy] = useState("");
  const [js, setJs] = useState<JournalStats | null>(null);
  const load = useCallback(() => api.admin.system().then((x) => { setS(x); setErr(""); }).catch((e) => setErr((e as ApiError).message)), []);
  useEffect(() => { void load(); const t = window.setInterval(load, 15000); return () => window.clearInterval(t); }, [load]);
  useEffect(() => { const f = () => api.admin.journalStats().then(setJs).catch(() => undefined); void f(); const t = window.setInterval(f, 30000); return () => window.clearInterval(t); }, []);

  const run = async (what: string, fn: () => Promise<void>) => { setBusy(what); setNote(null); try { await fn(); } catch (e) { setNote({ ok: false, text: (e as ApiError).message }); } finally { setBusy(""); } };
  const report = () => run("report", async () => {
    const r = await api.admin.diagnosticsReport();
    const full = { ...r, client: { livekit_client_js: lkClientVersion, user_agent: navigator.userAgent, secure_context: window.isSecureContext, page: location.origin } };
    setProblems(r.verdict);
    downloadText(JSON.stringify(full, null, 2), `peregovorka-diagnostics-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.json`, "application/json");
    setNote({ ok: true, text: "Отчёт сформирован и скачан. Секреты, пароли и токены из него автоматически удалены." });
  });

  if (err && !s) return <div className="alert error">{err}</div>;
  if (!s) return <div className="muted">Загрузка…</div>;
  const names: Record<string, string> = { postgres: "PostgreSQL", redis: "Redis", livekit: "LiveKit", asr: "ASR (транскрибация)", ldap: "Active Directory (LDAPS)", llm_local: "Локальная LLM (Qwen3 0.6B)" };
  const h = s.host ?? {};
  const cpuPct = h.load1 !== undefined && h.cpus ? (h.load1 / h.cpus) * 100 : undefined;
  const memUsed = h.mem_total && h.mem_available !== undefined ? ((h.mem_total - h.mem_available) / h.mem_total) * 100 : undefined;
  const asr = s.checks.asr as Record<string, unknown> | undefined;
  const asrProv = (asr?.provider ?? {}) as { name?: string; device?: string };
  const prov = asrProv.name ? `${asrProv.name} · ${(asrProv as { runtime?: string }).runtime ?? "?"} · ${(asrProv.device ?? "?").toUpperCase()}` : "—";

  return (
    <section>
      <div className="row"><h2>Состояние системы</h2><div className="spacer" />
        <button className="btn primary" onClick={report} disabled={!!busy}>{busy === "report" ? "Формирование…" : "Скачать диагностический отчёт"}</button>
        <button className="btn" onClick={() => run("exp", async () => { const r = await api.admin.retryExports(); setNote({ ok: true, text: `Повторная выгрузка записей: выгружено ${r.exported}, с ошибкой ${r.still_failed}` }); await load(); })} disabled={!!busy}>Повторить выгрузку записей</button>
        <button className="btn" onClick={() => run("ret", async () => { const r = await api.admin.runRetention(); setNote({ ok: true, text: `Очистка по срокам выполнена: ${JSON.stringify(r)}` }); await load(); })} disabled={!!busy}>Запустить очистку по срокам</button></div>
      {note && <div className={`alert ${note.ok ? "ok" : "error"}`} role="status">{note.text}</div>}
      {problems && (problems.length ? <div className="alert error"><b>Найдено в отчёте:</b><ul style={{ margin: "4px 0 0" }}>{problems.map((p) => <li key={p}>{p}</li>)}</ul></div> : <div className="alert ok">Диагностика не нашла проблем.</div>)}
      <RepairsPanel onOpen={onOpen} />
      {!s.master_key_ok && <div className="alert error">APP_MASTER_KEY не задан или некорректен — секретные настройки (пароли, токены, ключи) сохранить нельзя.</div>}
      {s.kernel && !s.kernel.ok && <div className="alert"><b>Параметры ядра ниже рекомендаций WebRTC.</b> {s.kernel.note} На общем сервере применяет администратор сервера.</div>}
      {(s.recording_export?.failed ?? 0) > 0 && <div className="alert error">Записей, не выгруженных во внешнее хранилище: {s.recording_export?.failed}. Файлы сохранены локально — проверьте раздел «Хранилище записей» и нажмите «Повторить выгрузку».</div>}

      <div className="kpi">
        <div className="card"><div className="l">Идёт встреч</div><div className="v">{s.counts.active_meetings}</div></div>
        <div className="card"><div className="l">Пользователей онлайн</div><div className="v">{s.live?.users_online ?? "—"}</div></div>
        <div className="card"><div className="l">Нагрузка CPU (load1 / {h.cpus ?? "?"} ядер)</div><div className="v">{cpuPct !== undefined ? `${Math.round(cpuPct)} %` : "—"}</div>{cpuPct !== undefined && <Bar value={cpuPct} />}</div>
        <div className="card"><div className="l">Память занята</div><div className="v">{memUsed !== undefined ? `${Math.round(memUsed)} %` : "—"}</div>{memUsed !== undefined && <><Bar value={memUsed} warn={80} bad={92} /><div className="l">свободно {bytes(h.mem_available)} из {bytes(h.mem_total)}</div></>}</div>
        <div className="card"><div className="l">Свободно на диске данных</div><div className="v">{bytes(s.disk_free_bytes)}</div></div>
        {js && (
          <div className="card"><div className="l">Журнал событий</div><div className="v">{bytes(js.size_bytes)}</div>
            <div className="l">{js.total.toLocaleString("ru-RU")} записей · хранится {js.retention_days} дн. · ошибок за сутки: {js.errors_24h}</div>
            <div className="row tight" style={{ marginTop: 6 }}>
              {onOpen && <button className="btn mini" onClick={() => onOpen("journal")}>Открыть</button>}
              {onOpen && <button className="btn mini" onClick={() => onOpen("journal_settings")}>Хранение и очистка</button>}</div></div>
        )}
        <div className="card"><div className="l">Версия · commit · сборка</div><div className="v" style={{ fontSize: 15 }}>{versionLabel(s.version, s.commit)}</div><div className="l">{s.built_at ?? ""}</div></div>
      </div>

      <h3>Сервисы</h3>
      <div className="grid">
        {Object.entries(s.checks).map(([k, v]) => (
          <div key={k} className="card"><div className="row"><span className={`dot ${v.ok ? "ok" : "bad"}`} /><b>{names[k] ?? k}</b></div>
            <div className="muted small">{v.ok ? (k === "llm_local" ? (v.configured ? (v.selected ? "работает, выбрана для протоколов" : "модель на месте, не выбрана в настройках") : v.state === "disabled" ? "отключена при установке" : "не загружена (необязательно)") : "работает") : `недоступен${v.error ? ` (${v.error})` : ""}`}</div>
            {k === "asr" && (
              <div className="muted small" style={{ marginTop: 4 }}>
                модель: {prov}<br />
                очередь {String(asr?.queue_depth ?? "—")} · обработано {String(asr?.processed ?? "—")} · отброшено {String(asr?.dropped ?? "—")} · ошибок {String(asr?.errors ?? "—")}<br />
                средняя задержка распознавания: {ms(asr?.avg_infer_ms as number | undefined)} · ожидание в очереди: {ms(asr?.avg_queue_ms as number | undefined)}<br />
                RTF (время / длительность аудио): {asr?.rtf !== undefined && asr?.rtf !== null ? String(asr.rtf) : "—"} · потоки torch: {String(asr?.torch_threads ?? "—")}/{String(asr?.torch_interop_threads ?? "—")}
              </div>)}
          </div>))}
      </div>

      <h3>Время входа в комнату (последние измерения)</h3>
      <p className="muted small">Сравнение строк показывает, где теряется время: backend, прокси, сигналинг, ICE, микрофон или ASR. Данные присылают браузеры участников и ASR; хранятся сутки.</p>
      <table className="table compact"><thead><tr><th>Этап</th><th>Среднее</th><th>p95</th><th>Максимум</th><th>Измерений</th></tr></thead><tbody>
        {Object.entries(TIMING_LABEL).map(([k, [label, hint]]) => {
          const t = s.timings?.[k];
          return <tr key={k} title={hint}><td>{label}<div className="muted small">{hint}</div></td><td>{t ? ms(t.avg) : "—"}</td><td>{t ? ms(t.p95) : "—"}</td><td>{t ? ms(t.max) : "—"}</td><td>{t?.n ?? 0}</td></tr>;
        })}
      </tbody></table>

      {s.realtime && (
        <>
          <h3>Реальное время</h3>
          <div className="kpi">
            <div className="card"><div className="l">Идёт встреч · пользователей онлайн</div><div className="v">{s.realtime.active_meetings} · {s.realtime.users_online}</div></div>
            <div className="card"><div className="l">RTT (среднее по клиентам)</div><div className="v">{s.realtime.client.rtt_ms ?? "—"}{s.realtime.client.rtt_ms != null ? " мс" : ""}</div></div>
            <div className="card"><div className="l">Потери пакетов</div><div className="v">{s.realtime.client.packet_loss_pct ?? "—"}{s.realtime.client.packet_loss_pct != null ? " %" : ""}</div></div>
            <div className="card"><div className="l">Битрейт исх. / вх.</div><div className="v" style={{ fontSize: 16 }}>{s.realtime.client.bitrate_out_kbps ?? "—"} / {s.realtime.client.bitrate_in_kbps ?? "—"} кбит/с</div></div>
            <div className="card"><div className="l">Переподключения за сутки</div><div className="v" style={{ fontSize: 16 }}>{s.realtime.counters.reconnecting ?? 0} · разрывов {s.realtime.counters.disconnected ?? 0} · повторных входов {s.realtime.counters.rejoin_started ?? 0}</div></div>
            <div className="card" title="Room должен создаваться один раз на вход. Значение заметно выше 1 означает, что объект пересоздаётся (повторные входы или ошибка жизненного цикла)."><div className="l">Объектов Room на один вход</div><div className="v">{s.realtime.rooms_per_join}</div></div>
            <div className="card"><div className="l">Запись аудио: очередь · потеряно · записано</div><div className="v" style={{ fontSize: 16 }}>{s.realtime.recording.recorder_queue ?? "—"} КБ · {s.realtime.recording.recorder_dropped ?? "—"} · {s.realtime.recording.recorder_written_mb ?? "—"} МБ</div></div>
            <div className="card"><div className="l">Заморозки показа экрана</div><div className="v">{s.realtime.counters.screen_frozen ?? 0}</div></div>
          </div>
        </>
      )}

      <ComponentsTable compact />
      <div className="row">{onOpen && <button className="btn mini" onClick={() => onOpen("updates")}>Обновления и подробности →</button>}</div>
      <p className="muted small">Совместимость проверяется на практике: в отчёте и в scripts/smoke-test.sh есть проба WebSocket на /rtc/v1 (ответ 404 означает устаревший сервер и медленный вход).</p>

      {s.kernel && (
        <>
          <h3>Параметры ядра для WebRTC</h3>
          <table className="table compact"><thead><tr><th>Параметр</th><th>Сейчас</th><th>Рекомендуется</th><th /></tr></thead><tbody>
            {Object.entries(s.kernel.params).map(([k, v]) => <tr key={k}><td><code>{k}</code></td><td>{v.value ?? "н/д"}</td><td>≥ {v.recommended}</td><td>{v.ok === null ? "—" : v.ok ? "✓" : <span className="badge warn">ниже</span>}</td></tr>)}
          </tbody></table>
        </>
      )}

      <h3>Данные</h3>
      <table className="table compact"><tbody>
        {Object.entries({ "Пользователей": s.counts.users, "Комнат": s.counts.rooms, "Встреч": s.counts.meetings, "Реплик": s.counts.segments, "Записей аудио": s.counts.recordings,
          "Объём записей": bytes(s.counts.recordings_bytes), "Документов (протоколов и резюме)": s.counts.protocols, "Публичный адрес": s.public_url }).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}
      </tbody></table>
    </section>
  );
}
