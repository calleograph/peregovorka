import { describe, expect, it } from "vitest";
import { buildPdf, dataUrlBytes } from "./boardExport";

const text = (b: Uint8Array) => new TextDecoder("latin1").decode(b);

describe("buildPdf", () => {
  const jpeg = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4, 0xff, 0xd9]);
  const pdf = buildPdf(jpeg, 800, 600);
  const s = text(pdf);

  it("валидная структура: заголовок, конец файла, размер страницы из размера изображения (96 dpi → pt)", () => {
    expect(s.startsWith("%PDF-1.4")).toBe(true);
    expect(s.trimEnd().endsWith("%%EOF")).toBe(true);
    expect(s).toContain("/MediaBox [0 0 600 450]");
    expect(s).toContain("/Filter /DCTDecode");
  });

  it("таблица xref указывает точно на объекты, startxref — на таблицу", () => {
    const start = Number(/startxref\n(\d+)\n/.exec(s)![1]);
    expect(s.slice(start, start + 4)).toBe("xref");
    const entries = [...s.slice(start).matchAll(/(\d{10}) 00000 n /g)].map((m) => Number(m[1]));
    expect(entries).toHaveLength(5);
    entries.forEach((off, i) => expect(s.slice(off, off + 7)).toBe(`${i + 1} 0 obj`));
  });

  it("JPEG целиком внутри потока с верной длиной", () => {
    expect(s).toContain(`/Length ${jpeg.length} >>`);
    const at = s.indexOf("stream\n", s.indexOf("/DCTDecode")) + 7;
    expect([...pdf.slice(at, at + jpeg.length)]).toEqual([...jpeg]);
  });

  it("очень большая схема уменьшается до допустимого размера страницы", () => {
    const big = text(buildPdf(jpeg, 40000, 10000));
    const [, , , w] = /MediaBox \[0 0 ([\d.]+) ([\d.]+)\]/.exec(big)!.map(Number).concat([0]) as number[];
    expect(Math.max(Number(/MediaBox \[0 0 ([\d.]+)/.exec(big)![1]), w)).toBeLessThanOrEqual(14400);
  });
});

describe("dataUrlBytes", () => {
  it("base64 и url-кодированный текст", () => {
    expect(dataUrlBytes("data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=")).toMatchObject({ mime: "image/svg+xml" });
    expect(new TextDecoder().decode(dataUrlBytes("data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=")!.bytes)).toBe("<svg></svg>");
    expect(new TextDecoder().decode(dataUrlBytes("data:text/plain,%D0%BF%D1%80")!.bytes)).toBe("пр");
    expect(dataUrlBytes("не data url")).toBeNull();
  });
});
