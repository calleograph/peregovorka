// Выгрузка схемы: PNG и SVG делает сам редактор draw.io (офлайн, на странице), .drawio — это XML схемы (его открывают и продолжают править в draw.io),
// PDF собирается здесь же из растрового изображения: серверный экспорт у self-hosted draw.io отсутствует и не нужен.

const enc = new TextEncoder();

/** PDF из одного JPEG: страница по размеру изображения (px → pt при 96 dpi), без внешних библиотек. */
export function buildPdf(jpeg: Uint8Array, width: number, height: number): Uint8Array {
  const MAX = 14400;                                           // предел размера страницы PDF (пунктов)
  let w = width * 0.75, h = height * 0.75;
  const k = Math.min(1, MAX / Math.max(w, h));
  w = Math.max(1, Math.round(w * k * 100) / 100); h = Math.max(1, Math.round(h * k * 100) / 100);
  const content = `q ${w} 0 0 ${h} 0 0 cm /Im0 Do Q`;
  const parts: Uint8Array[] = [];
  const offsets: number[] = [];
  let pos = 0;
  const push = (b: Uint8Array | string) => { const u = typeof b === "string" ? enc.encode(b) : b; parts.push(u); pos += u.length; };
  push("%PDF-1.4\n%\xC7\xEC\x8F\xA2\n");
  const obj = (n: number, body: string, stream?: Uint8Array) => {
    offsets[n] = pos;
    push(`${n} 0 obj\n${body}`);
    if (stream) { push("\nstream\n"); push(stream); push("\nendstream"); }
    push("\nendobj\n");
  };
  obj(1, "<< /Type /Catalog /Pages 2 0 R >>");
  obj(2, "<< /Type /Pages /Kids [3 0 R] /Count 1 >>");
  obj(3, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${w} ${h}] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>`);
  obj(4, `<< /Type /XObject /Subtype /Image /Width ${width} /Height ${height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>`, jpeg);
  obj(5, `<< /Length ${content.length} >>`, enc.encode(content));
  const xref = pos;
  push(`xref\n0 6\n0000000000 65535 f \n${[1, 2, 3, 4, 5].map((n) => `${String(offsets[n]).padStart(10, "0")} 00000 n \n`).join("")}`);
  push(`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  const out = new Uint8Array(pos);
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

export function dataUrlBytes(dataUrl: string): { mime: string; bytes: Uint8Array } | null {
  const m = /^data:([^;,]+)(;base64)?,(.*)$/s.exec(dataUrl);
  if (!m) return null;
  try {
    if (m[2]) { const bin = atob(m[3]); const b = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) b[i] = bin.charCodeAt(i); return { mime: m[1], bytes: b }; }
    return { mime: m[1], bytes: enc.encode(decodeURIComponent(m[3])) };
  } catch { return null; }
}

/** PNG (data URL от редактора) → PDF. Фон белый: JPEG не хранит прозрачность. */
export async function pngToPdf(pngDataUrl: string): Promise<Blob> {
  const img = new Image();
  await new Promise<void>((ok, bad) => { img.onload = () => ok(); img.onerror = () => bad(new Error("png")); img.src = pngDataUrl; });
  const c = document.createElement("canvas");
  c.width = img.naturalWidth; c.height = img.naturalHeight;
  const g = c.getContext("2d");
  if (!g) throw new Error("canvas");
  g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height); g.drawImage(img, 0, 0);
  const jpeg = await new Promise<Blob | null>((r) => c.toBlob(r, "image/jpeg", 0.92));
  if (!jpeg) throw new Error("jpeg");
  const bytes = buildPdf(new Uint8Array(await jpeg.arrayBuffer()), c.width, c.height);
  return new Blob([bytes as BlobPart], { type: "application/pdf" });
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function saveDataUrl(dataUrl: string, filename: string): boolean {
  const d = dataUrlBytes(dataUrl);
  if (!d) return false;
  saveBlob(new Blob([d.bytes as BlobPart], { type: d.mime }), filename);
  return true;
}

export function saveXml(xml: string, filename: string): void {
  saveBlob(new Blob([xml], { type: "application/vnd.jgraph.mxfile" }), filename);
}
