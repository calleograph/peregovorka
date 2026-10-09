import { describe, expect, it } from "vitest";
import type { ProtocolItem } from "./api";
import { docState, generationLine } from "./components/GenerationInfo";
import { headersFromValues, headersPayload } from "./pages/admin/HeadersEditor";
import { spanRu } from "./util";

const base: ProtocolItem = {
  id: "1", meeting_id: "m", kind: "protocol", status: "ready", error: null, created_by: "user1", created_at: "2026-10-09T07:42:10Z", updated_at: "2026-10-09T07:46:51Z",
  model: "qwen3-1.7b-q4_k_m", location: null, title: null, edited_at: null, edited_by: null,
};

describe("время и модель документа", () => {
  it("пишет длительность словами", () => {
    expect(spanRu(52)).toBe("52 с");
    expect(spanRu(278)).toBe("4 мин 38 с");
    expect(spanRu(300)).toBe("5 мин");
    expect(spanRu(3900)).toBe("1 ч 05 мин");
    expect(spanRu(null)).toBe("");
  });

  it("собирает строку «модель · локальная · начато · готово · длительность»", () => {
    const line = generationLine({
      ...base,
      timing: { requested_at: "2026-10-09T07:42:13Z", started_at: null, llm_started_at: null, llm_finished_at: null, finished_at: "2026-10-09T07:46:51Z", queue_s: 0, prepare_s: 1, llm_s: 270, total_s: 278 },
      generation: { model: "qwen3-1.7b-q4_k_m", model_title: "Qwen3 1.7B Q4_K_M", llm_local: true } as never,
    });
    expect(line).toContain("Qwen3 1.7B Q4_K_M · локальная · начато ");
    expect(line).toContain("готово ");
    expect(line.endsWith("4 мин 38 с")).toBe(true);
    const ext = generationLine({ ...base, generation: { model: "gpt-x", model_title: "gpt-x", llm_local: false, llm_profile: "Шлюз" } as never });
    expect(ext).toContain("внешняя API «Шлюз»");
  });

  it("обрезанный документ не выглядит готовым", () => {
    expect(docState(base)).toEqual({ label: "готово", tone: "ok" });
    expect(docState({ ...base, truncated: true, warnings: ["оборван"] }).label).toBe("обрезано");
    expect(docState({ ...base, warnings: ["что-то"] }).tone).toBe("warn");
    expect(docState({ ...base, status: "failed" }).tone).toBe("error");
    expect(docState({ ...base, status: "pending" }).tone).toBe("pending");
  });
});

describe("дополнительные заголовки API", () => {
  it("секретные значения не возвращаются и не затираются при сохранении", () => {
    const rows = headersFromValues({ extra_headers: { "X-Org": "org-1" }, secret_header_names: ["X-Key"] } as never);
    expect(rows).toEqual([
      { name: "X-Org", value: "org-1", secret: false, keep: false },
      { name: "X-Key", value: "", secret: true, keep: true },
    ]);
    const p = headersPayload(rows);
    expect(p.extra_headers).toEqual({ "X-Org": "org-1" });
    expect(JSON.parse(p.secret_headers)).toEqual({ "X-Key": null });          // null — оставить прежнее значение
    rows[1] = { ...rows[1], value: "new-secret", keep: false };
    expect(JSON.parse(headersPayload(rows).secret_headers)).toEqual({ "X-Key": "new-secret" });
    expect(JSON.parse(headersPayload(rows.slice(0, 1)).secret_headers)).toEqual({});         // строку удалили — заголовок убирается
  });
});
