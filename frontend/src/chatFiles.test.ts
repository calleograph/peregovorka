import { describe, expect, it } from "vitest";
import { chatFileOk } from "./chatFiles";

const f = (name: string, size: number, type = "") => ({ name, size, type });
const MB = 1024 * 1024;

describe("вложения чатов", () => {
  it("изображения, снимки экрана и документы до 3 МБ", () => {
    for (const n of ["a.png", "b.JPG", "c.pdf", "d.docx", "e.xlsx", "f.txt"]) expect(chatFileOk(f(n, 2 * MB))).toBe(true);
    expect(chatFileOk(f("", 200_000, "image/png"))).toBe(true);        // снимок экрана из буфера без имени
    expect(chatFileOk(f("a.png", 3 * MB))).toBe(true);
  });
  it("больше 3 МБ, видео, звук, архивы, исполняемые — отказ", () => {
    expect(chatFileOk(f("a.png", 3 * MB + 1))).toBe(false);
    for (const n of ["v.mp4", "s.mp3", "a.zip", "r.rar", "x.exe", "p.apk", "noext"]) expect(chatFileOk(f(n, 1000))).toBe(false);
    expect(chatFileOk(f("a.pdf", 0))).toBe(false);
  });
  it("предел и список сервера только сужают", () => {
    expect(chatFileOk(f("a.pdf", 2 * MB), 1)).toBe(false);
    expect(chatFileOk(f("a.zip", 1000), 25, ["zip"])).toBe(false);
  });
});
