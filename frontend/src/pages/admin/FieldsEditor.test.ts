import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import FieldsEditor from "./FieldsEditor";
import type { Field } from "./SettingsForm";

const f = (name: string, type: Field["type"], extra: Partial<Field> = {}): Field => ({ name, label: name, type, ...extra }) as Field;

describe("сетка полей настроек", () => {
  it("короткие поля — сеткой, флажки — своей сеткой, текстовая область — во всю ширину; у каждого поля якорь для перехода из «Обзора»", () => {
    const html = renderToStaticMarkup(createElement(FieldsEditor, {
      fields: [f("a", "number"), f("b", "select", { options: [["x", "коротко"]] }), f("c", "bool"), f("d", "bool"), f("e", "textarea"),
               f("g", "select", { options: [["1", "Как аудиозаписи — то же хранилище, папка Video/"]] })],
      values: { a: 1, b: "x", c: true, d: false, e: "", g: "1" }, onChange: () => undefined, secrets: {}, onSecret: () => undefined,
    }));
    const grids = html.match(/<div class="field-grid[^"]*">/g) ?? [];
    expect(grids).toEqual(['<div class="field-grid ">', '<div class="field-grid checks">', '<div class="field-grid ">']);
    expect(html.indexOf('id="f-e"')).toBeGreaterThan(html.indexOf('class="field-grid checks"'));
    expect(html).toMatch(/<label id="f-g" class="wide">/);              // длинные варианты — во всю ширину сетки
    for (const n of ["a", "b", "c", "d", "e", "g"]) expect(html).toContain(`id="f-${n}"`);
    expect(html).not.toContain("max-width:220px");
  });
});
