import { describe, expect, it } from "vitest";
import viewerSource from "../public/mapview/viewer.js?raw";
import { buildStandaloneHtml, findSegment, jsonForScript, publicMeta } from "./mapExport";
import type { Segment } from "./api";

const seg = (id: number, iso: string): Segment => ({ id, uid: String(id), meeting_id: "m", user_id: null, display_name: "A", identity: "x", started_at: iso, ended_at: iso, text: "t", language: "ru" });

describe("выгрузка карты в HTML", () => {
  it("данные нельзя использовать, чтобы закрыть тег скрипта или выполнить разметку", () => {
    const evil = { title: "</script><img src=x onerror=alert(1)>", note: "<!-- a --> " };
    const json = jsonForScript(evil);
    expect(json).not.toMatch(/<\/?script/i);
    expect(json).not.toContain("<");
    expect(JSON.parse(json)).toEqual(evil);
    const html = buildStandaloneHtml("Карта </title><script>alert(1)</script>", "body{}", "var a='</script>';", { data: evil, meta: null });
    expect(html.match(/<script/gi)?.length).toBe(2);             // только наши два тега
    expect(html).not.toContain("</title><script>alert(1)");
    expect(html).toContain('id="pg-map-data"');
    expect(html).toContain('http-equiv="Content-Security-Policy"');
    expect(html).toContain("default-src 'none'");
  });
  it("в файл не попадают служебные сведения: профиль API, кто нажал, токены", () => {
    const meta = { model: "m", model_title: "Qwen", llm_profile: "Корпоративный шлюз", created_by: "Иванов", duration_s: 12, token: "secret", warnings: ["w"] };
    expect(publicMeta(meta)).toEqual({ model: "m", model_title: "Qwen", duration_s: 12, warnings: ["w"] });
    const html = buildStandaloneHtml("t", "", "", { data: {}, meta });
    expect(html).not.toContain("Корпоративный шлюз");
    expect(html).not.toContain("secret");
    expect(html).not.toContain("Иванов");
  });
  it("просмотр карты не вставляет данные как HTML", () => {
    expect(viewerSource).not.toMatch(/innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(|new Function/);
  });
});

describe("переход к первоисточнику", () => {
  const segs = [seg(1, "2026-10-09T10:00:05Z"), seg(2, "2026-10-09T10:05:00Z"), seg(3, "2026-10-09T10:20:00Z")];
  it("по id записи, иначе по времени от начала встречи", () => {
    expect(findSegment(segs, "2026-10-09T10:00:00Z", 0, 3)?.id).toBe(3);
    expect(findSegment(segs, "2026-10-09T10:00:00Z", 290)?.id).toBe(2);
    expect(findSegment(segs, "2026-10-09T10:00:00Z", 5000)?.id).toBe(3);
  });
  it("стенограммы нет (удалена по сроку хранения) — перехода нет", () => {
    expect(findSegment([], "2026-10-09T10:00:00Z", 10, 5)).toBeNull();
  });
});
