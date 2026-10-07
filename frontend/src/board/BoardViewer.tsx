import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type ApiError } from "../api";
import BoardExportMenu from "./BoardExportMenu";
import DrawioFrame, { type FrameHandle } from "./DrawioFrame";
import { FrameRpc, type FrameMsg } from "./frameRpc";

/** Сохранённая схема встречи в истории: открывается в том же редакторе draw.io; скачивается как PNG/SVG/PDF и как .drawio (для дальнейшего редактирования). */
export default function BoardViewer({ meetingId, fileBase }: { meetingId: string; fileBase: string }) {
  const frame = useRef<FrameHandle>(null);
  const rpc = useMemo(() => new FrameRpc((m) => frame.current?.post(m)), []);
  const xmlRef = useRef<string | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "empty" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api.whiteboard(meetingId).then((s) => {
      if (cancelled) return;
      xmlRef.current = s.xml;
      setState(s.xml ? "ready" : "empty");
    }).catch((e) => { if (!cancelled) { setError((e as ApiError).message || "Не удалось загрузить схему"); setState("error"); } });
    return () => { cancelled = true; rpc.dispose(); };
  }, [meetingId, rpc]);

  const onMessage = useCallback((m: FrameMsg) => {
    if (m.event === "init" && xmlRef.current) frame.current?.post({ action: "load", xml: xmlRef.current, autosave: 0 });
    else rpc.handle(m);
  }, [rpc]);

  if (state === "loading") return <div className="muted">Загрузка схемы…</div>;
  if (state === "error") return <div className="alert error" role="alert">{error}</div>;
  if (state === "empty") return <div className="card muted">В этой встрече общая доска не использовалась.</div>;
  return (
    <div className="card board-view">
      <div className="row">
        <BoardExportMenu request={(f, extra) => rpc.request(f, extra)} fileBase={fileBase} />
        <a className="btn mini" href={api.whiteboardFileUrl(meetingId)} download>Скачать .drawio (исходный файл)</a>
        <span className="muted small">Правки здесь не сохраняются в встречу — чтобы продолжить работу над схемой, скачайте файл .drawio и откройте его в draw.io.</span>
      </div>
      <DrawioFrame ref={frame} onMessage={onMessage} className="drawio-frame view" title="Схема встречи (draw.io)" />
    </div>
  );
}
