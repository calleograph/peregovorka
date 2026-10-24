// Индикатор поверх окон: идёт показ экрана (остановить одним нажатием) или встреча продолжается при свёрнутом окне (открыть, покинуть).
import { call, h, icon, pg } from "./ui";

type Info = { org: string; sharing: boolean; inCall: boolean; mic: boolean } | null;
const root = document.getElementById("app")!;

function render(i: Info): void {
  if (!i) { root.replaceChildren(); return; }
  const act = (a: string) => () => void call("shell:indicator:act", { act: a });
  root.replaceChildren(h("div", { class: `ind${i.sharing ? "" : " call"}`, role: "status" },
    h("span", { class: "rec", "aria-hidden": "true" }),
    h("b", null, i.sharing ? "Идёт показ экрана" : i.mic ? `Встреча · микрофон включён` : "Идёт встреча"),
    h("button", { class: "btn sm", title: "Открыть окно встречи", on: { click: act("open") } }, "Открыть"),
    i.sharing ? h("button", { class: "btn sm stop", on: { click: act("stop-share") } }, icon("screenStop"), "Остановить")
      : h("button", { class: "btn sm stop", title: "Покинуть встречу", "aria-label": "Покинуть встречу", on: { click: act("leave") } }, icon("hangup"))));
}

pg.on("shell:indicator", (d) => render(d as Info));
void call<Info>("shell:indicator:data").then(render);
