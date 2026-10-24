// Выбор экрана или окна для показа: миниатюры, звук компьютера (только экран целиком — так работает захват звука в Windows), Enter — показать, Esc — отмена.
import { call, h, icon } from "./ui";

type Src = { id: string; name: string; kind: "screen" | "window"; thumb: string | null; icon: string | null };
type Data = { org: string; audio: boolean; sources: Src[] };

const root = document.getElementById("app")!;
let kind: "screen" | "window" = "screen";
let chosen: string | null = null;

void call<Data | null>("shell:picker:data").then((d) => {
  if (!d) { void call("shell:picker:choose", { id: null }); return; }
  const grid = h("div", { class: "grid", role: "listbox", "aria-label": "Что показать" });
  const ok = h("button", { class: "btn primary", disabled: true, on: { click: () => done() } }, icon("screen"), "Показать");
  const audio = h("input", { type: "checkbox", id: "aud" });
  const audioRow = d.audio ? h("label", { for: "aud" }, audio, "Передавать звук компьютера") : null;
  const segs = h("div", { class: "seg", role: "tablist" });
  const tabs: [typeof kind, string][] = [["screen", `Экран (${d.sources.filter((s) => s.kind === "screen").length})`], ["window", `Окно (${d.sources.filter((s) => s.kind === "window").length})`]];
  for (const [k, label] of tabs) {
    const b = h("button", { type: "button", role: "tab", "aria-pressed": String(k === kind), "aria-selected": String(k === kind) }, label);
    b.addEventListener("click", () => { kind = k; for (const x of segs.children) { x.setAttribute("aria-pressed", String(x === b)); x.setAttribute("aria-selected", String(x === b)); } fill(); });
    segs.append(b);
  }
  function fill(): void {
    chosen = null;
    ok.disabled = true;
    if (audioRow) audioRow.style.visibility = kind === "screen" ? "visible" : "hidden";
    const list = d!.sources.filter((s) => s.kind === kind);
    grid.replaceChildren(...(list.length ? list.map((s) => {
      const b = h("button", { class: "src", role: "option", "aria-pressed": "false", "aria-selected": "false", title: s.name },
        h("div", { class: "th", style: s.thumb ? { backgroundImage: `url("${s.thumb}")` } : {} }),
        h("div", { class: "nm" }, s.icon ? h("img", { src: s.icon, alt: "" }) : null, h("span", null, s.name)));
      b.addEventListener("click", () => { chosen = s.id; ok.disabled = false; for (const x of grid.children) { x.setAttribute("aria-pressed", String(x === b)); x.setAttribute("aria-selected", String(x === b)); } });
      b.addEventListener("dblclick", () => { chosen = s.id; done(); });
      return b;
    }) : [h("p", { class: "muted" }, kind === "window" ? "Нет открытых окон для показа." : "Экраны не найдены.")]));
    (grid.firstElementChild as HTMLElement | null)?.click();
  }
  function done(): void { if (chosen) void call("shell:picker:choose", { id: chosen, audio: kind === "screen" && audio.checked }); }
  root.replaceChildren(h("div", { class: "picker" },
    h("header", null, h("h2", null, "Что показать участникам?"), h("p", { class: "muted small" }, `Встреча в «${d["org"]}». Остановить показ можно в любой момент кнопкой на панели встречи или индикаторе.`)),
    segs, grid,
    h("footer", null, audioRow, h("span", { class: "spacer" }), h("button", { class: "btn", on: { click: () => void call("shell:picker:choose", { id: null }) } }, "Отмена"), ok)));
  fill();
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") void call("shell:picker:choose", { id: null });
    if (e.key === "Enter" && chosen) done();
  });
});
