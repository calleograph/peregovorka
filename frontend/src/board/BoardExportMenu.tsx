import { useState } from "react";
import Menu from "../components/Menu";
import { pngToPdf, saveBlob, saveDataUrl, saveXml } from "./boardExport";
import type { ExportFormat, FrameMsg } from "./frameRpc";

/** Меню «Скачать»: PNG, SVG, PDF, .drawio (XML — чтобы открыть и продолжить редактирование в draw.io). Всё формируется на странице. */
export default function BoardExportMenu({ request, fileBase }: { request: (f: ExportFormat, extra?: Record<string, unknown>) => Promise<FrameMsg | null>; fileBase: string }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const run = async (what: "png" | "svg" | "pdf" | "xml") => {
    setBusy(true); setErr("");
    try {
      if (what === "xml") {
        const r = await request("xml");
        if (typeof r?.xml !== "string") throw new Error("Редактор не ответил");
        saveXml(r.xml, `${fileBase}.drawio`);
      } else if (what === "svg") {
        const r = await request("svg", { background: "#ffffff" });
        if (typeof r?.data !== "string" || !saveDataUrl(r.data, `${fileBase}.svg`)) throw new Error("Редактор не вернул SVG");
      } else {
        const r = await request("png", { scale: 2, background: "#ffffff", transparent: false });
        if (typeof r?.data !== "string") throw new Error("Редактор не вернул изображение");
        if (what === "png") { if (!saveDataUrl(r.data, `${fileBase}.png`)) throw new Error("Изображение повреждено"); }
        else saveBlob(await pngToPdf(r.data), `${fileBase}.pdf`);
      }
    } catch (e) { setErr((e as Error).message || "Не удалось сформировать файл"); }
    setBusy(false);
  };

  return (
    <span className="row tight">
      <Menu label={busy ? "Готовим файл…" : "Скачать"} className="btn mini" title="Сохранить схему на компьютер">
        <button onClick={() => void run("png")}>Картинка PNG</button>
        <button onClick={() => void run("svg")}>Вектор SVG</button>
        <button onClick={() => void run("pdf")}>Документ PDF</button>
        <button onClick={() => void run("xml")}>Файл draw.io (.drawio) — для редактирования</button>
      </Menu>
      {err && <span className="field-err" role="alert">{err}</span>}
    </span>
  );
}
