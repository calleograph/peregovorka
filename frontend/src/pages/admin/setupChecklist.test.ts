import { describe, expect, it } from "vitest";
import type { SetupCheck } from "../../api";
import { problemChecks, summaryLine } from "./SetupChecklistCard";

const c = (id: string, status: SetupCheck["status"]): SetupCheck => ({ id, title: id, status, detail: "d", page: "system" });

describe("чек-лист настройки сервера", () => {
  it("итог одной строкой: готово из всех, требуют внимания", () => {
    expect(summaryLine({ summary: { ok: 9, warn: 2, fail: 1, todo: 2, total: 14, attention: 5 }, last_check_at: null })).toBe("Готово 9 из 14 · требуют внимания: 5");
  });

  it("проблемы показываются от тяжёлых к лёгким, исправные скрыты, число ограничено", () => {
    const list = problemChecks([c("a", "ok"), c("b", "todo"), c("c", "warn"), c("d", "fail"), c("e", "ok"), c("f", "warn")]);
    expect(list.map((x) => x.id)).toEqual(["d", "c", "f", "b"]);
    expect(problemChecks(Array.from({ length: 10 }, (_, i) => c(String(i), "fail")), 6)).toHaveLength(6);
    expect(problemChecks([c("a", "ok")])).toEqual([]);
  });
});
