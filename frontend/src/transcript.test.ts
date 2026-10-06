import { describe, expect, it } from "vitest";
import type { Segment } from "./api";
import { mergeSegment, renderProtocol } from "./transcript";

const seg = (uid: string, id: number, at: string, name: string, text: string): Segment => ({
  id, uid, meeting_id: "m", user_id: null, display_name: name, identity: "u-x",
  started_at: at, ended_at: at, text, language: "ru",
});

describe("transcript", () => {
  it("ignores duplicates by uid (WS + загрузка истории)", () => {
    const a = seg("1", 1, "2026-01-01T10:00:00Z", "Алиса", "привет");
    expect(mergeSegment(mergeSegment([], a), a)).toHaveLength(1);
  });

  it("keeps parallel replies of different speakers as separate ordered entries", () => {
    let list: Segment[] = [];
    list = mergeSegment(list, seg("2", 2, "2026-01-01T10:00:02Z", "Боб", "второй"));
    list = mergeSegment(list, seg("1", 1, "2026-01-01T10:00:01Z", "Алиса", "первый"));
    expect(list.map((s) => s.display_name)).toEqual(["Алиса", "Боб"]);
  });

  it("renders protocol with participants header", () => {
    const text = renderProtocol("Переговорка 1", "2026-01-01T10:00:00Z", ["Алиса", "Боб"],
      [seg("1", 1, "2026-01-01T10:00:01Z", "Алиса", "добрый день")]);
    expect(text).toContain("Участвовали: Алиса, Боб");
    expect(text).toContain("Алиса: добрый день");
  });
});
