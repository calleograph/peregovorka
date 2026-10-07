import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import type { FrameMsg } from "./frameRpc";

/**
 * Редактор схем — draw.io (jgraph/drawio, Apache-2.0), развёрнутый у нас как статика в /drawio/ и встроенный iframe'ом. Свой редактор не пишется.
 * Фрейм работает офлайн (offline=1) и в «песочнице» БЕЗ allow-same-origin: у него нет ни cookie, ни доступа к API приложения, ни к localStorage;
 * общаемся только сообщениями. Источник сообщений проверяется по окну фрейма (origin у песочницы — «null», по нему проверять нечего).
 */
export const DRAWIO_SRC = "/drawio/index.html?embed=1&proto=json&spin=1&noSaveBtn=1&noExitBtn=1&offline=1&lang=ru&libraries=1&ui=kennedy&dark=0&configure=1";

/**
 * Настройка редактора при запуске (протокол embed, событие `configure`): панель фигур открыта сразу (по умолчанию в окне встраивания она свёрнута);
 * автосохранение чаще — чужие правки приходят быстрее. Проверено в Edge на draw.io v32.3.0.
 */
export const DRAWIO_CONFIG = { sidebarWidth: 232, autosaveDelay: 800 };

export interface FrameHandle { post(msg: Record<string, unknown>): void }

const DrawioFrame = forwardRef<FrameHandle, { onMessage: (m: FrameMsg) => void; className?: string; title?: string }>(function DrawioFrame({ onMessage, className, title }, ref) {
  const frame = useRef<HTMLIFrameElement>(null);
  const cb = useRef(onMessage);
  cb.current = onMessage;
  useImperativeHandle(ref, () => ({
    post: (msg) => { frame.current?.contentWindow?.postMessage(JSON.stringify(msg), "*"); }, // у песочницы origin «null» — адресуем «*»; фрейм получает только схему
  }), []);
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.source !== frame.current?.contentWindow) return;
      let data: unknown = e.data;
      if (typeof data === "string") { try { data = JSON.parse(data); } catch { return; } }
      if (data && typeof data === "object") {
        if ((data as FrameMsg).event === "configure") frame.current?.contentWindow?.postMessage(JSON.stringify({ action: "configure", config: DRAWIO_CONFIG }), "*");
        cb.current(data as FrameMsg);
      }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);
  return (
    <iframe ref={frame} src={DRAWIO_SRC} title={title ?? "Схема (draw.io)"} className={className ?? "drawio-frame"} referrerPolicy="no-referrer"
            sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox allow-downloads allow-modals" />
  );
});
export default DrawioFrame;
