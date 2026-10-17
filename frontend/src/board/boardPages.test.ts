import { describe, expect, it } from "vitest";
import { buildPdfPages, crc32, zipStore } from "./boardExport";
import { safeFileName, splitPages } from "./boardPages";

const FILE = '<mxfile host="x" version="1"><diagram id="p1" name="Схема &amp; план"><mxGraphModel><root><mxCell id="0"/></root></mxGraphModel></diagram>'
  + '<diagram id="p2" name="Вторая">eJzLzS0pSk1RyE9TKEgtS8zJTNEBAF1WCP4=</diagram></mxfile>';

describe("страницы схемы", () => {
  it("режет файл на отдельные файлы из одной страницы, сохраняя заголовок и содержимое", () => {
    const pages = splitPages(FILE);
    expect(pages.map((p) => [p.id, p.name])).toEqual([["p1", "Схема & план"], ["p2", "Вторая"]]);
    expect(pages[0].xml).toBe('<mxfile host="x" version="1"><diagram id="p1" name="Схема &amp; план"><mxGraphModel><root><mxCell id="0"/></root></mxGraphModel></diagram></mxfile>');
    expect(pages[1].xml).toContain("eJzLzS0pSk1RyE9TKEgtS8zJTNEBAF1WCP4=");      // сжатая страница остаётся как есть
    expect(pages.every((p) => (p.xml.match(/<diagram/g) ?? []).length === 1)).toBe(true);
  });

  it("одна страница — одна запись; не файл схемы — пусто; без имени — «Страница N»", () => {
    expect(splitPages('<mxfile><diagram id="a"><x/></diagram></mxfile>').map((p) => p.name)).toEqual(["Страница 1"]);
    expect(splitPages("<html/>")).toEqual([]);
    expect(splitPages("")).toEqual([]);
    expect(splitPages('<mxfile><diagram name="Б"><x/></diagram><diagram name=""><y/></diagram></mxfile>').map((p) => p.name)).toEqual(["Б", "Страница 2"]);
  });

  it("имя файла страницы безопасно", () => {
    expect(safeFileName('План: "А/Б"?')).toBe("План_ _А_Б__");
    expect(safeFileName("")).toBe("схема");
    expect(safeFileName("..скрытый")).toBe("скрытый");
    expect(safeFileName("я".repeat(200)).length).toBe(80);
  });
});

const text = (b: Uint8Array) => new TextDecoder("latin1").decode(b);

describe("PDF из нескольких страниц", () => {
  const j = (n: number) => new Uint8Array([0xff, 0xd8, n, 0xff, 0xd9]);
  const pdf = buildPdfPages([{ jpeg: j(1), width: 800, height: 600 }, { jpeg: j(2), width: 400, height: 400 }, { jpeg: j(3), width: 1000, height: 200 }]);
  const s = text(pdf);

  it("число страниц, размеры каждой страницы по своему изображению", () => {
    expect(s).toContain("/Count 3");
    expect(s.match(/\/Type \/Page /g)).toHaveLength(3);
    expect(s).toContain("/MediaBox [0 0 600 450]");
    expect(s).toContain("/MediaBox [0 0 300 300]");
    expect(s).toContain("/MediaBox [0 0 750 150]");
  });

  it("xref указывает точно на объекты (3 страницы × 3 объекта + каталог и список страниц)", () => {
    const start = Number(/startxref\n(\d+)\n/.exec(s)![1]);
    expect(s.slice(start, start + 4)).toBe("xref");
    const entries = [...s.slice(start).matchAll(/(\d{10}) 00000 n /g)].map((m) => Number(m[1]));
    expect(entries).toHaveLength(11);
    entries.forEach((off, i) => expect(s.slice(off, off + String(i + 1).length + 6)).toBe(`${i + 1} 0 obj`));
    expect(s).toContain("/Size 12");
  });
});

describe("ZIP без сжатия", () => {
  it("контрольная сумма CRC-32 совпадает с эталоном", () => {
    expect(crc32(new TextEncoder().encode("123456789"))).toBe(0xcbf43926);
    expect(crc32(new Uint8Array())).toBe(0);
  });

  it("локальные заголовки, каталог и конец каталога согласованы; имена в UTF-8", () => {
    const a = new TextEncoder().encode("первый файл");
    const b = new Uint8Array([1, 2, 3, 4, 5]);
    const zip = zipStore([{ name: "01_Схема 1.png", data: a }, { name: "02_Схема 2.png", data: b }], new Date(2026, 9, 17, 12, 30, 40));
    const v = new DataView(zip.buffer);
    const end = zip.length - 22;
    expect(v.getUint32(end, true)).toBe(0x06054b50);
    expect(v.getUint16(end + 10, true)).toBe(2);
    const dirAt = v.getUint32(end + 16, true);
    expect(v.getUint32(end + 12, true)).toBe(end - dirAt);
    let at = dirAt;
    const dec = new TextDecoder();
    const names: string[] = [];
    for (const data of [a, b]) {
      expect(v.getUint32(at, true)).toBe(0x02014b50);
      expect(v.getUint16(at + 8, true) & 0x0800).toBe(0x0800);                    // UTF-8 в именах
      expect(v.getUint32(at + 16, true)).toBe(crc32(data));
      expect(v.getUint32(at + 20, true)).toBe(data.length);
      const nameLen = v.getUint16(at + 28, true), off = v.getUint32(at + 42, true);
      names.push(dec.decode(zip.slice(at + 46, at + 46 + nameLen)));
      expect(v.getUint32(off, true)).toBe(0x04034b50);                            // локальный заголовок по смещению из каталога
      const dataAt = off + 30 + v.getUint16(off + 26, true);
      expect([...zip.slice(dataAt, dataAt + data.length)]).toEqual([...data]);
      at += 46 + nameLen;
    }
    expect(names).toEqual(["01_Схема 1.png", "02_Схема 2.png"]);
  });
});
