import { useEffect, useRef, useState } from "react";
import { Modal } from "./Dialogs";

const BOX = 280;          // размер окна обрезки на экране

export interface CropperProps {
  file: File; onCancel: () => void; onDone: (blob: Blob) => Promise<void>;
  title?: string; out?: number; maxBytes?: number; shape?: "circle" | "rounded";
  hint?: string;
}

/**
 * Окно квадратного кадрирования: перетащите картинку и подберите масштаб — получится квадрат `out`×`out` (WebP). Исходный файл дальше этого окна не уходит:
 * на сервер отправляется только результат, а сервер проверяет и перекодирует его ещё раз. Используется для аватарки (круг) и значка комнаты (скруглённый квадрат).
 */
export default function ImageCropper({ file, onCancel, onDone, title = "Кадрирование", out: OUT = 256, maxBytes: MAX_BYTES = 600 * 1024, shape = "circle", hint }: CropperProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const preview = useRef<HTMLCanvasElement>(null);
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  const [err, setErr] = useState("");
  const [zoom, setZoom] = useState(1);
  const [pos, setPos] = useState({ x: 0, y: 0 });       // сдвиг центра картинки относительно центра окна, px
  const [busy, setBusy] = useState(false);
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);

  useEffect(() => {
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { setErr("Допустимы только JPEG, PNG и WebP."); return; }
    const url = URL.createObjectURL(file);
    let alive = true;                                    // повторный запуск эффекта (StrictMode, смена файла) не должен показывать ошибку от уже отменённой загрузки
    const im = new Image();
    im.onload = () => { if (alive) setImg(im); };
    im.onerror = () => { if (alive) setErr("Не удалось открыть картинку."); };
    im.src = url;
    return () => { alive = false; URL.revokeObjectURL(url); };
  }, [file]);

  const base = img ? BOX / Math.min(img.width, img.height) : 1;          // при zoom=1 меньшая сторона картинки закрывает окно
  const clamp = (p: { x: number; y: number }, z: number) => {
    if (!img) return p;
    const w = img.width * base * z, h = img.height * base * z;
    const mx = Math.max(0, (w - BOX) / 2), my = Math.max(0, (h - BOX) / 2);
    return { x: Math.max(-mx, Math.min(mx, p.x)), y: Math.max(-my, Math.min(my, p.y)) };
  };

  useEffect(() => {
    const c = canvas.current;
    if (!c || !img) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, BOX, BOX);
    const w = img.width * base * zoom, h = img.height * base * zoom;
    ctx.drawImage(img, BOX / 2 + pos.x - w / 2, BOX / 2 + pos.y - h / 2, w, h);
    const p = preview.current?.getContext("2d");
    if (p) { p.clearRect(0, 0, 96, 96); p.drawImage(c, 0, 0, BOX, BOX, 0, 0, 96, 96); }
  }, [img, zoom, pos, base]);

  const save = async () => {
    if (!img) return;
    setBusy(true); setErr("");
    try {
      const out = document.createElement("canvas");
      out.width = out.height = OUT;
      const ctx = out.getContext("2d")!;
      ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, OUT, OUT);
      const k = OUT / BOX, w = img.width * base * zoom * k, h = img.height * base * zoom * k;
      ctx.drawImage(img, OUT / 2 + pos.x * k - w / 2, OUT / 2 + pos.y * k - h / 2, w, h);
      let blob = await new Promise<Blob | null>((r) => out.toBlob(r, "image/webp", 0.9));
      if (!blob || blob.type !== "image/webp") blob = await new Promise<Blob | null>((r) => out.toBlob(r, "image/jpeg", 0.9));       // браузер без WebP
      if (!blob) throw new Error("кодирование");
      if (blob.size > MAX_BYTES) throw new Error("размер");
      await onDone(blob);
    } catch (e) {
      setErr((e as Error).message === "размер" ? "Картинка получилась слишком большой." : (e as { message?: string }).message || "Не удалось сохранить.");
      setBusy(false);
    }
  };

  return (
    <Modal title={title} onClose={onCancel}>
      <div className="cropper">
        {err && <div className="alert error" role="alert">{err}</div>}
        {img && (
          <>
            <div className="crop-box" style={{ width: BOX, height: BOX }}>
              <canvas ref={canvas} width={BOX} height={BOX} aria-label="Область обрезки: перетащите картинку"
                      onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); drag.current = { x: e.clientX, y: e.clientY, px: pos.x, py: pos.y }; }}
                      onPointerMove={(e) => { const d = drag.current; if (d) setPos(clamp({ x: d.px + e.clientX - d.x, y: d.py + e.clientY - d.y }, zoom)); }}
                      onPointerUp={() => { drag.current = null; }} />
              <span className={`crop-ring ${shape === "rounded" ? "rounded" : ""}`} aria-hidden />
            </div>
            <label className="crop-zoom">Масштаб
              <input type="range" min={1} max={3} step={0.02} value={zoom} onChange={(e) => { const z = Number(e.target.value); setZoom(z); setPos((p) => clamp(p, z)); }} />
            </label>
            <div className="crop-preview" aria-label="Предпросмотр результата"><canvas ref={preview} width={96} height={96} className={shape === "rounded" ? "rounded" : "circle"} /><span className="muted small">{hint ?? "Перетащите картинку, чтобы выбрать нужную часть. Сохраняется квадрат."}</span></div>
          </>
        )}
        <div className="row">
          <button type="button" className="btn primary" onClick={() => void save()} disabled={!img || busy}>{busy ? "Сохранение…" : "Сохранить"}</button>
          <button type="button" className="btn ghost" onClick={onCancel} disabled={busy}>Отмена</button>
        </div>
      </div>
    </Modal>
  );
}
