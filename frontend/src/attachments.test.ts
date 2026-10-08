import { describe, expect, it } from "vitest";
import { fileTypeLabel, formatSize, pastedName } from "./attachments";

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
