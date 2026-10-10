import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError, type MapState, type MapTopicEdit } from "../api";
import { buildStandaloneHtml } from "../mapExport";
import { downloadText, fileBase } from "../util";

interface Props {
  meetingId: string;
  roomName: string;
  startedAt: string;
  /** Есть ли что-то для построения карты (реплики). */
  hasMaterials: boolean;
  /** Перейти к реплике стенограммы (секунды от начала встречи; id записи, если известен). */
  onGoto: (sec: number, segmentId: number | null) => void;
  onToast?: (text: string, kind?: "ok" | "error") => void;
}

/** «4 мин 36 с»: время формирования показывается целиком, а не округляется до минут. */
export function humanSeconds(s: number): string {
  const t = Math.max(0, Math.round(s));
  if (t < 60) return `${t} с`;
  const m = Math.floor(t / 60), r = t % 60;
  return m < 60 ? `${m} мин${r ? ` ${r} с` : ""}` : `${Math.floor(m / 60)} ч ${m % 60} мин`;
}

const STAGE: Record<string, string> = { pending: "В очереди", running: "Обрабатывается", ready: "Готово", failed: "Ошибка", none: "Не сформирована" };

/** Вкладка «Карта разговора»: формирование в фоне, просмотр (окно /mapview без доступа к API), правки тем, скачивание HTML. */
export default function MapTab({ meetingId, roomName, startedAt, hasMaterials, onGoto, onToast }: Props) {
  const [st, setSt] = useState<MapState | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [height, setHeight] = useState(900);
  const frame = useRef<HTMLIFrameElement>(null);
  const ready = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const stRef = useRef<MapState | null>(null);
  stRef.current = st;

  const load = useCallback(async () => {
    try {
      const s = await api.conversationMap(meetingId);
      setSt(s);
      window.clearTimeout(timer.current);
      if (s.status === "pending" || s.status === "running") timer.current = window.setTimeout(load, 3000);
    } catch (e) { setError((e as ApiError).message); }
  }, [meetingId]);

  useEffect(() => { void load(); return () => window.clearTimeout(timer.current); }, [load]);

  const send = useCallback(() => {
    const s = stRef.current, w = frame.current?.contentWindow;
    if (!s || s.status !== "ready" || !s.data || !w || !ready.current) return;
    w.postMessage({ pg: "map", type: "data", payload: { data: s.data, meta: s.meta ?? null, canEdit: s.can_edit, categories: s.categories } }, "*");
  }, []);
  useEffect(send, [st, send]);

  useEffect(() => {
    const onMsg = async (ev: MessageEvent) => {
      if (ev.source !== frame.current?.contentWindow) return;      // слушаем только своё окно карты
      const m = ev.data as { pg?: string; type?: string; sec?: number; segmentId?: number | null; height?: number; topicId?: string; patch?: MapTopicEdit };
      if (!m || m.pg !== "map") return;
      if (m.type === "ready") { ready.current = true; send(); }
      else if (m.type === "height" && typeof m.height === "number") setHeight(Math.min(Math.max(m.height, 480), 6000));
      else if (m.type === "goto" && typeof m.sec === "number") onGoto(m.sec, m.segmentId ?? null);
      else if (m.type === "edit" && m.topicId && m.patch) {
        try { setSt(await api.editMapTopic(meetingId, m.topicId, m.patch)); onToast?.("Правка сохранена. Ответ модели не изменён — его можно вернуть."); }
        catch (e) { onToast?.((e as ApiError).message, "error"); }
      }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [meetingId, onGoto, onToast, send]);

  const generate = async () => {
    setBusy(true); setError("");
    try { await api.createMap(meetingId); await load(); } catch (e) { setError((e as ApiError).message); } finally { setBusy(false); }
  };

  const download = async () => {
    if (!st?.data) return;
    try {
      const [css, js] = await Promise.all(["viewer.css", "viewer.js"].map((f) => fetch(`/mapview/${f}`).then((r) => { if (!r.ok) throw new Error(f); return r.text(); })));
      downloadText(buildStandaloneHtml(`Карта разговора — ${roomName}`, css, js, { data: st.data, meta: st.meta ?? null }), `${fileBase(roomName, startedAt)}_карта.html`, "text/html;charset=utf-8");
      void api.logMapExport(meetingId).catch(() => undefined);
    } catch { onToast?.("Не удалось собрать файл: не загрузились файлы просмотра", "error"); }
  };

  if (!st) return <div className="card muted">{error || "Загрузка…"}</div>;
  const working = st.status === "pending" || st.status === "running";
  const meta = st.meta ?? {};
  const secs = (k: string) => (typeof meta[k] === "number" ? (meta[k] as number) : null);

  return (
    <div className="map-tab">
      {error && <div className="alert error" role="alert">{error}</div>}
      <div className="card map-bar">
        <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
          <span className={`badge ${st.status === "ready" ? "ok" : st.status === "failed" ? "err" : ""}`}>{STAGE[st.status]}</span>
          {st.status !== "none" && st.updated_at && <span className="muted small">{new Date(st.updated_at).toLocaleString("ru-RU")}</span>}
          <span style={{ flex: 1 }} />
          {st.status === "ready" && <button className="btn" onClick={download} title="Один файл: открывается без сервера, внутри данные карты, стили и скрипт">Скачать HTML</button>}
          {(st.can_generate ?? true) && <button className="btn primary" disabled={busy || working || !st.finished || !hasMaterials || !st.plan.ready} onClick={generate}
                  title={!st.finished ? "Карта формируется после завершения встречи" : !hasMaterials ? "В стенограмме нет реплик" : !st.plan.ready ? st.plan.reason ?? "" : ""}>
            {working ? "Формируется…" : st.status === "ready" || st.status === "failed" ? "Пересоздать" : "Сформировать карту"}</button>}
        </div>
        <p className="muted small" style={{ margin: "6px 0 0" }}>
          Темы встречи, их порядок и длительность, участники и ссылки на реплики. Карту строит {st.plan.model ? <b>{st.plan.model}</b> : "языковая модель"}
          {st.plan.local ? " (локально, текст не покидает сервер)" : ""}; на сервере без видеокарты это занимает несколько минут. Пользовательские правки хранятся отдельно и при пересоздании не теряются.
        </p>
        {!st.plan.ready && <div className="alert info small" style={{ marginTop: 8 }}>{st.plan.reason}</div>}
        {st.status === "failed" && <div className="alert error small" style={{ marginTop: 8 }}>{st.error || "Не удалось построить карту."}</div>}
        {working && <div className="alert info small" style={{ marginTop: 8 }}>
          {st.status === "pending" ? "Карта в очереди: тяжёлые задачи выполняются по одной." : "Модель разбирает стенограмму по частям."} Можно уйти со страницы — карта сформируется в фоне.
          {secs("requested_at") === null && typeof meta.requested_at === "string" && <> Запрошено в {new Date(meta.requested_at).toLocaleTimeString("ru-RU")}.</>}
        </div>}
        {st.status === "ready" && typeof meta.started_at === "string" && typeof meta.finished_at === "string" && (
          <p className="muted small" style={{ margin: "6px 0 0" }}>
            Начато {new Date(meta.started_at).toLocaleTimeString("ru-RU")} · готово {new Date(meta.finished_at).toLocaleTimeString("ru-RU")} · время {secs("duration_s") !== null ? humanSeconds(secs("duration_s") as number) : "—"}
            · фрагментов {String(meta.chunks ?? "—")}, повторов {String(meta.retries ?? 0)}{meta.completion_tokens ? `, ответ ${String(meta.completion_tokens)} токенов` : ""}{meta.tokens_per_s ? ` (${String(meta.tokens_per_s)}/с)` : ""}</p>
        )}
      </div>
      {st.status === "ready" && !!st.data && (
        <iframe ref={frame} title="Карта разговора" src="/mapview/index.html" sandbox="allow-scripts" className="map-frame" style={{ height }} />
      )}
      {st.status === "none" && !working && (
        <div className="card muted">Карта ещё не сформирована. Нажмите «Сформировать карту» — по умолчанию карты не строятся сами, чтобы не загружать сервер
          (автоматическое формирование включается в настройках переговорки или в «Администрирование → Протоколы»).</div>
      )}
    </div>
  );
}
