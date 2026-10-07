import { describe, expect, it } from "vitest";
import { linkify, safeHref } from "./linkify";

const links = (t: string) => linkify(t).filter((c) => c.type === "link").map((c) => (c.type === "link" ? c.href : ""));

describe("linkify", () => {
  it("находит http(s) и www, сохраняя точный адрес", () => {
    expect(links("см. https://wiki.corp.test/x?a=1&b=2#top и www.example.com/path")).toEqual([
      "https://wiki.corp.test/x?a=1&b=2#top", "https://www.example.com/path"]);
  });

  it("знаки препинания после адреса в ссылку не входят, скобки внутри адреса — входят", () => {
    expect(links("(смотрите https://a.test/b).")).toEqual(["https://a.test/b"]);
    expect(links("https://ru.wikipedia.org/wiki/Foo_(bar), далее")).toEqual(["https://ru.wikipedia.org/wiki/Foo_(bar)"]);
    expect(links("Ссылка: https://a.test/x!")).toEqual(["https://a.test/x"]);
  });

  it("IP, имена серверов, номера задач и файлы ссылками не становятся", () => {
    expect(links("srv-db-07 10.20.30.40:5432 JIRA-4521 config.yml user@host.test")).toEqual([]);
  });

  it("опасные схемы не распознаются; текст сохраняется целиком", () => {
    expect(links("javascript:alert(1) data:text/html,<b>x</b> ftp://a.test")).toEqual([]);
    expect(safeHref("javascript:alert(1)")).toBeNull();
    const t = "до https://a.test/x после\nвторая строка";
    expect(linkify(t).map((c) => c.text).join("")).toBe(t);
  });

  it("несколько ссылок и пустая строка", () => {
    expect(links("https://a.test https://b.test")).toEqual(["https://a.test/", "https://b.test/"]);
    expect(linkify("")).toEqual([]);
  });
});
