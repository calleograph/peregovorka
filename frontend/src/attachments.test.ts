import { describe, expect, it } from "vitest";
import { fileTypeLabel, formatSize, pastedFiles, pastedName } from "./attachments";

describe("вложения чата", () => {
  it("размер: килобайты и мегабайты", () => {
    expect(formatSize(10)).toBe("1 КБ");
    expect(formatSize(2048)).toBe("2 КБ");
    expect(formatSize(5 * 1024 * 1024)).toBe("5,0 МБ");
  });
  it("тип файла по расширению", () => {
    expect(fileTypeLabel("Отчёт.final.pdf")).toBe("PDF");
    expect(fileTypeLabel("README")).toBe("файл");
    expect(fileTypeLabel(".hidden")).toBe("файл");
    expect(fileTypeLabel("trailing.")).toBe("файл");
  });
  it("имя скриншота из буфера обмена", () => {
    const t = new Date(2026, 0, 1, 10, 20, 30);
    expect(pastedName("image.png", "image/png", t)).toMatch(/^Скриншот 10-20-30\.png$/);
    expect(pastedName("", "image/jpeg", t, 1, 2)).toMatch(/-2\.jpg$/);
    expect(pastedName("схема.png", "image/png", t)).toBe("схема.png");
  });
});


describe("pastedFiles: вставка из буфера обмена", () => {
  const dt = (o: Partial<{ files: File[]; items: { kind: string; getAsFile: () => File | null }[]; html: string }>) => ({
    files: o.files ?? [], items: o.items ?? [], getData: (f: string) => (f === "text/html" ? o.html ?? "" : ""),
  }) as unknown as DataTransfer;
  const PNG64 = "iVBORw0KGgo=";
  it("файл в списке files — как есть", () => {
    const f = new File([new Uint8Array([1])], "image.png", { type: "image/png" });
    expect(pastedFiles(dt({ files: [f] }))).toEqual([f]);
  });
  it("элемент-файл без списка files", () => {
    const f = new File([new Uint8Array([1])], "x.png", { type: "image/png" });
    expect(pastedFiles(dt({ items: [{ kind: "file", getAsFile: () => f }, { kind: "string", getAsFile: () => null }] }))).toEqual([f]);
  });
  it("картинка внутри HTML (Office, Teams, «копировать изображение»)", async () => {
    const out = pastedFiles(dt({ html: `<p>текст</p><img alt="" src="data:image/png;base64,${PNG64}">` }));
    expect(out).toHaveLength(1);
    expect(out[0].type).toBe("image/png");
    expect(out[0].name).toBe("image.png");
    expect(new Uint8Array(await out[0].arrayBuffer())[0]).toBe(0x89);
  });
  it("обычный текст и повреждённые данные — ничего", () => {
    expect(pastedFiles(dt({ html: "<b>просто текст</b>" }))).toEqual([]);
    expect(pastedFiles(dt({ html: '<img src="data:image/png;base64,@@@@">' }))).toEqual([]);
    expect(pastedFiles(null)).toEqual([]);
  });
});
