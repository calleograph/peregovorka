import { useEffect, useRef, useState } from "react";
import { Modal } from "./Dialogs";

const BOX = 280;          // размер окна обрезки на экране
const OUT = 256;          // размер итоговой картинки
const MAX_BYTES = 600 * 1024;

/** Окно обрезки аватарки: перетащите картинку и подберите масштаб — получится квадрат 256×256 (WebP). Сервер проверяет и перекодирует файл ещё раз. */
export default function AvatarCropper({ file, onCancel, onDone }: { file: File; onCancel: () => void; onDone: (blob: Blob) => Promise<void> }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  const [err, setErr] = useState("");
  const [zoom, setZoom] = useState(1);
  const [pos, setPos] = useState({ x: 0, y: 0 });       // сдвиг центра картинки относительно центра окна, px
  const [busy, setBusy] = useState(false);
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);

  useEffect(() => {
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { setErr("Допустимы только JPEG, PNG и WebP."); return; }
    const url = URL.createObjectURL(file);
    const im = new Image();
    im.onload = () => setImg(im);
    im.onerror = () => setErr("Не удалось открыть картинку.");
    im.src = url;
    return () => URL.revokeObjectURL(url);
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
    <Modal title="Аватарка" onClose={onCancel}>
      <div className="cropper">
        {err && <div className="alert error" role="alert">{err}</div>}
        {img && (
          <>
            <div className="crop-box" style={{ width: BOX, height: BOX }}>
              <canvas ref={canvas} width={BOX} height={BOX} aria-label="Область обрезки: перетащите картинку"
                      onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); drag.current = { x: e.clientX, y: e.clientY, px: pos.x, py: pos.y }; }}
                      onPointerMove={(e) => { const d = drag.current; if (d) setPos(clamp({ x: d.px + e.clientX - d.x, y: d.py + e.clientY - d.y }, zoom)); }}
                      onPointerUp={() => { drag.current = null; }} />
              <span className="crop-ring" aria-hidden />
            </div>
            <label className="crop-zoom">Масштаб
              <input type="range" min={1} max={3} step={0.02} value={zoom} onChange={(e) => { const z = Number(e.target.value); setZoom(z); setPos((p) => clamp(p, z)); }} />
            </label>
            <p className="muted small">Перетащите картинку, чтобы выбрать нужную часть. Сохраняется квадрат; на экране он показывается кругом.</p>
          </>
        )}
        <div className="row">
          <button className="btn primary" onClick={() => void save()} disabled={!img || busy}>{busy ? "Сохранение…" : "Сохранить"}</button>
          <button className="btn ghost" onClick={onCancel} disabled={busy}>Отмена</button>
        </div>
      </div>
    </Modal>
  );
}
