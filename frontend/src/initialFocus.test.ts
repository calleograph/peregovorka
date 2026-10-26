import { describe, expect, it } from "vitest";
import { chooseInitial, isTextEntry, type FocusCandidate } from "./initialFocus";

const f = (over: Partial<FocusCandidate> = {}): FocusCandidate => ({ tag: "input", type: "text", visible: true, disabled: false, readOnly: false, marked: false, optOut: false, ...over });

describe("chooseInitial", () => {
  it("единственное поле названия — фокус в него", () => {
    expect(chooseInitial([f()], false)).toBe(0);
  });
  it("пропускает скрытые, отключённые, только для чтения и hidden", () => {
    expect(chooseInitial([f({ visible: false }), f({ disabled: true }), f({ readOnly: true }), f({ type: "hidden" }), f()], false)).toBe(4);
  });
  it("не перепрыгивает флажок, переключатель и список: если первым идёт не текст — фокус на окне", () => {
    expect(chooseInitial([f({ type: "checkbox" }), f()], false)).toBeNull();
    expect(chooseInitial([f({ tag: "select", type: "select" }), f()], false)).toBeNull();
    expect(chooseInitial([f({ type: "radio" }), f()], false)).toBeNull();
  });
  it("явная отметка побеждает порядок", () => {
    expect(chooseInitial([f({ type: "checkbox" }), f(), f({ marked: true, tag: "textarea", type: "" })], false)).toBe(2);
  });
  it("окно для чтения (opt-out) и отмеченные как «не фокусировать» поля пропускаются", () => {
    expect(chooseInitial([f({ optOut: true })], false)).toBeNull();
  });
  it("на сенсорном экране текстовое поле не фокусируется — не выезжает клавиатура", () => {
    expect(chooseInitial([f()], true)).toBeNull();
    expect(chooseInitial([f({ marked: true })], true)).toBeNull();
  });
  it("нет полей — null", () => {
    expect(chooseInitial([], false)).toBeNull();
  });
});

describe("isTextEntry", () => {
  it("текстовые типы и textarea", () => {
    for (const type of ["", "text", "search", "email", "password", "tel", "url", "number", "TEXT"]) expect(isTextEntry({ tag: "input", type })).toBe(true);
    expect(isTextEntry({ tag: "textarea", type: "" })).toBe(true);
    for (const type of ["checkbox", "radio", "file", "range", "color", "date", "submit", "button"]) expect(isTextEntry({ tag: "input", type })).toBe(false);
  });
});
