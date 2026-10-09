import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type ApiError } from "../api";
import type { LiveBus } from "../liveSocket";
import BoardExportMenu from "./BoardExportMenu";
import DrawioFrame, { type FrameHandle } from "./DrawioFrame";
import { reportEvent } from "../diagnostics";
import { BoardSync, type BoardStatus, type BoardTimings } from "./whiteboardSync";

const SAVED_TEXT = { saved: "Сохранено", saving: "Сохраняем…", dirty: "Есть несохранённые правки" } as const;

interface Props {
  meetingId: string;
  bus: LiveBus;
  /** Доска видна (иначе она работает в фоне — правки участников продолжают приходить, а открытие мгновенно). */
  open: boolean;
  readOnly?: boolean;
  fileBase: string;
  onClose: () => void;
  /** Кто-то изменил схему, пока доска была закрыта. */
  onRemoteChange?: (by: string) => void;
}

/** Общая доска: один холст draw.io на всех участников встречи; правки синхронизируются, схема сохраняется вместе со встречей. */
export default function Whiteboard({ meetingId, bus, open, readOnly = false, fileBase, onClose, onRemoteChange }: Props) {
  const frame = useRef<FrameHandle>(null);
  const stage = useRef<HTMLElement>(null);
  const [full, setFull] = useState(false);
  const [timings, setTimings] = useState<BoardTimings | null>(null);     // сколько заняла первая загрузка: редактор · схема · отрисовка       // доска развёрнута на весь экран (редактор draw.io при этом не перезагружается)
  const clientId = useMemo(() => `b-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`, []);
  const [status, setStatus] = useState<BoardStatus>({ phase: "loading", saved: "saved" });
  const syncRef = useRef<BoardSync | null>(null);
  const openRef = useRef(open);
  openRef.current = open;
  const remote = useRef(onRemoteChange);
  remote.current = onRemoteChange;

  useEffect(() => {
    const sync = new BoardSync({
      clientId, readOnly, prefetch: true,
      fetchState: () => api.whiteboard(meetingId),
      sendPatch: (b) => api.whiteboardPatch(meetingId, b),
      saveSnapshot: (xml, seq) => api.whiteboardSave(meetingId, xml, seq),
      post: (m) => frame.current?.post(m),
      onStatus: setStatus,
    });
    syncRef.current = sync;
    const off = bus.on((e) => {
      if (e.type === "whiteboard_patch" || e.type === "whiteboard_saved") {
        sync.handleLive(e);
        if (e.type === "whiteboard_patch" && e.from !== clientId && !openRef.current) remote.current?.(e.by);
      }
    });
    const offSync = bus.onResync(() => sync.resyncSoon());
    return () => { off(); offSync(); sync.dispose(); syncRef.current = null; };
  }, [meetingId, bus, clientId, readOnly]);

  // Выход из полноэкранного режима браузера (Esc) сворачивает и нашу «полную» доску; без поддержки Fullscreen API работает режим поверх страницы и Esc
  useEffect(() => {
    if (!full) return;
    const onFs = () => { if (!document.fullscreenElement) setFull(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.fullscreenElement) setFull(false); };
    document.addEventListener("fullscreenchange", onFs);
    window.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("fullscreenchange", onFs); window.removeEventListener("keydown", onKey); };
  }, [full]);
  useEffect(() => { if (!open && full) setFull(false); }, [open, full]);
  useEffect(() => { if (!full && document.fullscreenElement && document.fullscreenElement === stage.current) void document.exitFullscreen().catch(() => undefined); }, [full]);
  const toggleFull = () => {
    if (full) { setFull(false); return; }
    setFull(true);
    void stage.current?.requestFullscreen?.().catch(() => undefined);      // не вышло (запрет браузера) — остаётся режим поверх страницы
  };

  useEffect(() => {
    if (status.phase !== "ready" || timings) return;
    const t = syncRef.current?.timings();
    if (!t) return;
    setTimings(t);
    reportEvent("board_ready", { meetingId, data: { ...t, hidden_preload: !openRef.current } });     // в журнале видно, где теряется время при открытии доски
  }, [status.phase, timings, meetingId]);

  const onMessage = useCallback((m: Parameters<BoardSync["handleFrame"]>[0]) => syncRef.current?.handleFrame(m), []);
  const request = useCallback((f: Parameters<BoardSync["requestExport"]>[0], extra?: Record<string, unknown>) => syncRef.current?.requestExport(f, extra) ?? Promise.resolve(null), []);

  return (
    <section ref={stage} className={`board-stage card ${open ? "" : "off"} ${full ? "full" : ""}`} aria-label="Общая доска" aria-hidden={!open}>
      <div className="board-head row">
        <h2>Общая доска</h2>
        <span className={`badge ${status.saved === "saved" ? "ok" : "warn"}`} title="Схема хранится вместе со встречей">{SAVED_TEXT[status.saved]}</span>
        {status.phase === "loading" && <span className="muted small">Загрузка редактора…</span>}
        {timings && timings.total_ms !== undefined && (
          <span className="muted small" title="Первая загрузка: окно редактора · получение схемы (параллельно) · отрисовка схемы в редакторе">
            загружена за {(timings.total_ms / 1000).toFixed(1)} с (редактор {((timings.editor_ms ?? 0) / 1000).toFixed(1)} · схема {((timings.diagram_ms ?? 0) / 1000).toFixed(1)})</span>
        )}
        <div className="spacer" />
        {(status.phase === "desync" || status.phase === "error") && (
          <>
            <span className="field-err" role="alert">{status.message}</span>
            <button className="btn mini primary" onClick={() => void syncRef.current?.resync()}>Обновить схему</button>
          </>
        )}
        <BoardExportMenu request={request} fileBase={fileBase} />
        <button className="btn mini" onClick={toggleFull} aria-pressed={full} title={full ? "Вернуть доску в окно (Esc)" : "Развернуть доску на весь экран"}>{full ? "⤡ Свернуть" : "⤢ На весь экран"}</button>
        <button className="btn mini" onClick={onClose} title="Скрыть доску — вы остаётесь в звонке, правки продолжат приходить">Скрыть доску</button>
      </div>
      <p className="muted small board-hint">Фигуры — слева, перетащите на холст. Связь: наведите на фигуру и потяните за стрелку к другой фигуре. Цвета и стили — кнопка «Формат» вверху справа.</p>
      <DrawioFrame ref={frame} onMessage={onMessage} className="drawio-frame" title="Общая доска (draw.io)" />
    </section>
  );
}

export type { ApiError };
