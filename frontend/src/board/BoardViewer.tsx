import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type ApiError } from "../api";
import BoardExportMenu from "./BoardExportMenu";
import DrawioFrame, { type FrameHandle } from "./DrawioFrame";
import { FrameRpc, type FrameMsg } from "./frameRpc";

/** Сохранённая схема встречи в истории: открывается в том же редакторе draw.io; скачивается как PNG/SVG/PDF и как .drawio (для дальнейшего редактирования). */
/** Моменты загрузки схемы в истории (мс от начала открытия): сначала берём схему, одновременно поднимается редактор, затем «схема передана» и «отрисована». */
export interface HistoryBoardTimings { schema_ms?: number; editor_ms?: number; sent_ms?: number; rendered_ms?: number; total_ms?: number; warm?: boolean }

export default function BoardViewer({ meetingId, fileBase, warm = false }: { meetingId: string; fileBase: string; warm?: boolean }) {
  const frame = useRef<FrameHandle>(null);
  const rpc = useMemo(() => new FrameRpc((m) => frame.current?.post(m)), []);
  const xmlRef = useRef<string | null>(null);
  const t0 = useRef(performance.now());
  const marks = useRef<HistoryBoardTimings>({});
  const sentRef = useRef(false);
  const warmRef = useRef(warm);
  warmRef.current = warm;
  const [state, setState] = useState<"loading" | "ready" | "empty" | "error">("loading");
  const [error, setError] = useState("");
  const [total, setTotal] = useState<number | null>(null);
  const now = () => Math.round(performance.now() - t0.current);

  // схема и редактор загружаются ОДНОВРЕМЕННО (раньше iframe создавался только после ответа сервера — время складывалось)
  const trySend = useCallback(() => {
    if (sentRef.current || xmlRef.current === null || marks.current.editor_ms === undefined) return;
    sentRef.current = true;
    marks.current.sent_ms = now();
    frame.current?.post({ action: "load", xml: xmlRef.current, autosave: 0 });
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.whiteboard(meetingId).then((s) => {
      if (cancelled) return;
      xmlRef.current = s.xml ?? "";
      marks.current.schema_ms = now();
      setState(s.xml ? "ready" : "empty");
      trySend();
    }).catch((e) => { if (!cancelled) { setError((e as ApiError).message || "Не удалось загрузить схему"); setState("error"); } });
    return () => { cancelled = true; rpc.dispose(); };
  }, [meetingId, rpc, trySend]);

  const onMessage = useCallback((m: FrameMsg) => {
    if (m.event === "init") { marks.current.editor_ms = now(); trySend(); }
    else if (m.event === "load" && marks.current.rendered_ms === undefined) {
      marks.current.rendered_ms = now();
      marks.current.total_ms = marks.current.rendered_ms;
      setTotal(marks.current.total_ms);
      api.clientEvent({ event: "board_history_ready", meeting_id: meetingId, data: { ...marks.current, warm: warmRef.current } });   // только числа; diagnostics.ts не импортируем — он тянет livekit-client в основной бандл     // в журнале видно, где теряется время при первом открытии
    } else rpc.handle(m);
  }, [rpc, trySend]);

  if (state === "error") return <div className="alert error" role="alert">{error}</div>;
  if (state === "empty") return <div className="card muted">В этой встрече общая доска не использовалась.</div>;
  return (
    <div className="card board-view">
      <div className="row">
        <BoardExportMenu request={(f, extra) => rpc.request(f, extra)} fileBase={fileBase} />
        <a className="btn mini" href={api.whiteboardFileUrl(meetingId)} download>Скачать .drawio (исходный файл)</a>
        {state === "loading" || total === null ? <span className="muted small">Загрузка схемы…</span> : <span className="muted small">загружена за {(total / 1000).toFixed(1)} с</span>}
        <span className="muted small">Правки здесь не сохраняются в встречу — чтобы продолжить работу над схемой, скачайте файл .drawio и откройте его в draw.io.</span>
      </div>
      <DrawioFrame ref={frame} onMessage={onMessage} className="drawio-frame view" title="Схема встречи (draw.io)" />
    </div>
  );
}
