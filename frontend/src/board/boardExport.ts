// Выгрузка схемы: PNG и SVG делает сам редактор draw.io (офлайн, на странице), .drawio — это XML схемы (его открывают и продолжают править в draw.io),
// PDF собирается здесь же из растрового изображения: серверный экспорт у self-hosted draw.io отсутствует и не нужен.

const enc = new TextEncoder();

export interface PdfPage { jpeg: Uint8Array; width: number; height: number }

/** PDF из JPEG-страниц: каждая страница по размеру своего изображения (px → pt при 96 dpi), без внешних библиотек. */
export function buildPdfPages(pages: PdfPage[]): Uint8Array {
  const MAX = 14400;                                           // предел размера страницы PDF (пунктов)
  const n = pages.length;
  const parts: Uint8Array[] = [];
  const offsets: number[] = [];
  let pos = 0;
  const push = (b: Uint8Array | string) => { const u = typeof b === "string" ? enc.encode(b) : b; parts.push(u); pos += u.length; };
  push("%PDF-1.4\n%\xC7\xEC\x8F\xA2\n");
  const obj = (k: number, body: string, stream?: Uint8Array) => {
    offsets[k] = pos;
    push(`${k} 0 obj\n${body}`);
    if (stream) { push("\nstream\n"); push(stream); push("\nendstream"); }
    push("\nendobj\n");
  };
  obj(1, "<< /Type /Catalog /Pages 2 0 R >>");
  obj(2, `<< /Type /Pages /Kids [${pages.map((_, i) => `${3 + 3 * i} 0 R`).join(" ")}] /Count ${n} >>`);
  pages.forEach(({ jpeg, width, height }, i) => {
    let w = width * 0.75, h = height * 0.75;
    const k = Math.min(1, MAX / Math.max(w, h));
    w = Math.max(1, Math.round(w * k * 100) / 100); h = Math.max(1, Math.round(h * k * 100) / 100);
    const content = `q ${w} 0 0 ${h} 0 0 cm /Im0 Do Q`;
    const base = 3 + 3 * i;
    obj(base, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${w} ${h}] /Resources << /XObject << /Im0 ${base + 1} 0 R >> >> /Contents ${base + 2} 0 R >>`);
    obj(base + 1, `<< /Type /XObject /Subtype /Image /Width ${width} /Height ${height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.length} >>`, jpeg);
    obj(base + 2, `<< /Length ${content.length} >>`, enc.encode(content));
  });
  const total = 3 + 3 * n;
  const xref = pos;
  push(`xref\n0 ${total}\n0000000000 65535 f \n${Array.from({ length: total - 1 }, (_, i) => `${String(offsets[i + 1]).padStart(10, "0")} 00000 n \n`).join("")}`);
  push(`trailer\n<< /Size ${total} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  const out = new Uint8Array(pos);
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}

/** PDF из одного JPEG (прежний вызов). */
export function buildPdf(jpeg: Uint8Array, width: number, height: number): Uint8Array {
  return buildPdfPages([{ jpeg, width, height }]);
}

export function dataUrlBytes(dataUrl: string): { mime: string; bytes: Uint8Array } | null {
  const m = /^data:([^;,]+)(;base64)?,(.*)$/s.exec(dataUrl);
  if (!m) return null;
  try {
    if (m[2]) { const bin = atob(m[3]); const b = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) b[i] = bin.charCodeAt(i); return { mime: m[1], bytes: b }; }
    return { mime: m[1], bytes: enc.encode(decodeURIComponent(m[3])) };
  } catch { return null; }
}

/** PNG (data URL от редактора) → JPEG для страницы PDF. Фон белый: JPEG не хранит прозрачность. */
export async function pngToJpeg(pngDataUrl: string): Promise<PdfPage> {
  const img = new Image();
  await new Promise<void>((ok, bad) => { img.onload = () => ok(); img.onerror = () => bad(new Error("png")); img.src = pngDataUrl; });
  const c = document.createElement("canvas");
  c.width = img.naturalWidth; c.height = img.naturalHeight;
  const g = c.getContext("2d");
  if (!g) throw new Error("canvas");
  g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height); g.drawImage(img, 0, 0);
  const jpeg = await new Promise<Blob | null>((r) => c.toBlob(r, "image/jpeg", 0.92));
  if (!jpeg) throw new Error("jpeg");
  return { jpeg: new Uint8Array(await jpeg.arrayBuffer()), width: c.width, height: c.height };
}

/** Несколько PNG (по странице схемы) → один PDF. */
export async function pngsToPdf(pngDataUrls: string[]): Promise<Blob> {
  const pages: PdfPage[] = [];
  for (const u of pngDataUrls) pages.push(await pngToJpeg(u));
  return new Blob([buildPdfPages(pages) as BlobPart], { type: "application/pdf" });
}

export const pngToPdf = (pngDataUrl: string): Promise<Blob> => pngsToPdf([pngDataUrl]);

const CRC = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  return t;
})();

export function crc32(b: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** ZIP без сжатия (PNG и так сжат, страницы SVG невелики): имена в UTF-8, чтобы русские названия страниц открывались корректно. */
export function zipStore(files: { name: string; data: Uint8Array }[], when: Date = new Date()): Uint8Array {
  const time = (when.getHours() << 11) | (when.getMinutes() << 5) | (when.getSeconds() >> 1);
  const date = (Math.max(0, when.getFullYear() - 1980) << 9) | ((when.getMonth() + 1) << 5) | when.getDate();
  const chunks: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  const u16 = (v: DataView, o: number, x: number) => v.setUint16(o, x, true);
  const u32 = (v: DataView, o: number, x: number) => v.setUint32(o, x >>> 0, true);
  for (const f of files) {
    const name = enc.encode(f.name);
    const crc = crc32(f.data);
    const lh = new Uint8Array(30 + name.length), lv = new DataView(lh.buffer);
    u32(lv, 0, 0x04034b50); u16(lv, 4, 20); u16(lv, 6, 0x0800); u16(lv, 8, 0); u16(lv, 10, time); u16(lv, 12, date);
    u32(lv, 14, crc); u32(lv, 18, f.data.length); u32(lv, 22, f.data.length); u16(lv, 26, name.length); u16(lv, 28, 0);
    lh.set(name, 30);
    const ch = new Uint8Array(46 + name.length), cv = new DataView(ch.buffer);
    u32(cv, 0, 0x02014b50); u16(cv, 4, 20); u16(cv, 6, 20); u16(cv, 8, 0x0800); u16(cv, 10, 0); u16(cv, 12, time); u16(cv, 14, date);
    u32(cv, 16, crc); u32(cv, 20, f.data.length); u32(cv, 24, f.data.length); u16(cv, 28, name.length); u32(cv, 42, offset);
    ch.set(name, 46);
    chunks.push(lh, f.data);
    central.push(ch);
    offset += lh.length + f.data.length;
  }
  const dirSize = central.reduce((n, c) => n + c.length, 0);
  const end = new Uint8Array(22), ev = new DataView(end.buffer);
  u32(ev, 0, 0x06054b50); u16(ev, 8, files.length); u16(ev, 10, files.length); u32(ev, 12, dirSize); u32(ev, 16, offset);
  const all = [...chunks, ...central, end];
  const out = new Uint8Array(all.reduce((n, c) => n + c.length, 0));
  let at = 0;
  for (const c of all) { out.set(c, at); at += c.length; }
  return out;
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
