import { describe, expect, it } from "vitest";
import { archivePasswordComplete, backupFileName, canApply, groupPassword, groupWarnings, normalizeArchivePassword, statusTitle, statusTone } from "./configBackupMath";

describe("резервная копия конфигурации: чистая логика", () => {
  it("пароль архива: пробелы и дефисы не мешают, длина считается по значащим символам", () => {
    expect(normalizeArchivePassword(" ab cd-ef–gh\n")).toBe("abcdefgh");
    expect(archivePasswordComplete("abcd efgh jkmn pqrs tuvw")).toBe(true);
    expect(archivePasswordComplete("abcd-efgh-jkmn-pqrs-tuv")).toBe(false);
  });
  it("пароль показывается группами по 4 символа и без хвостового пробела", () => {
    expect(groupPassword("abcdefghjkmnpqrstuvw")).toBe("abcd efgh jkmn pqrs tuvw");
    expect(groupPassword("abc")).toBe("abc");
  });
  it("имя файла содержит дату и время", () => {
    expect(backupFileName(new Date(2026, 9, 11, 7, 5))).toBe("peregovorka-config-20261011-0705.pgcfg");
  });
  it("предупреждения группируются по виду привязки к серверу", () => {
    const g = groupWarnings([
      { kind: "host", title: "имя или адрес сервера", where: "LDAP «A»", value: "dc1", note: "" },
      { kind: "url", title: "адрес (URL)", where: "LLM", value: "https://x", note: "" },
      { kind: "host", title: "имя или адрес сервера", where: "SMTP", value: "smtp", note: "" },
    ]);
    expect(g.map((x) => [x.kind, x.items.length])).toEqual([["host", 2], ["url", 1]]);
  });
  it("статусы проверок: понятные названия и цвета", () => {
    expect(statusTitle("needs_attention")).toBe("требует внимания");
    expect(statusTone("restored")).toBe("ok");
    expect(statusTone("failed")).toBe("bad");
  });
  it("применить можно, только когда введён пароль администратора и подтверждены предупреждения", () => {
    expect(canApply({ needs_ack: true, conflicts: [] }, false, "pw")).toBe(false);
    expect(canApply({ needs_ack: true, conflicts: [] }, true, "")).toBe(false);
    expect(canApply({ needs_ack: true, conflicts: [] }, true, "pw")).toBe(true);
    expect(canApply({ needs_ack: false, conflicts: [] }, false, "pw")).toBe(true);
  });
});
