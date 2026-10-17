import { useCallback, useMemo, useRef, useState } from "react";
import { api } from "../api";
import Menu from "../components/Menu";
import { safeFileName, splitPages, type BoardPage } from "./boardPages";
import { dataUrlBytes, pngsToPdf, saveBlob, saveDataUrl, saveXml, zipStore } from "./boardExport";
import DrawioFrame, { type FrameHandle } from "./DrawioFrame";
import { FrameRpc, type ExportFormat, type FrameMsg } from "./frameRpc";

type Kind = "png" | "svg" | "pdf" | "xml";
const WHITE = { background: "#ffffff" };

/** Меню «Скачать»: PNG, SVG, PDF, .drawio (XML — чтобы открыть и продолжить редактирование в draw.io). Всё формируется на странице, без серверного экспорта.
 *  Редактор выгружает в PNG/SVG только текущую страницу, поэтому у схемы из нескольких страниц каждая страница выгружается отдельно во вспомогательном
 *  скрытом редакторе (рабочая доска и её синхронизация не затрагиваются): PNG и SVG — архивом ZIP по файлу на страницу, PDF — одним документом. */
export default function BoardExportMenu({ request, fileBase, meetingId }: { request: (f: ExportFormat, extra?: Record<string, unknown>) => Promise<FrameMsg | null>; fileBase: string; meetingId?: string }) {
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [hiddenOn, setHiddenOn] = useState(false);
  const hidden = useRef<FrameHandle>(null);
  const rpc = useMemo(() => new FrameRpc((m) => hidden.current?.post(m)), []);
  const waiters = useRef<{ init?: () => void; load?: () => void }>({});

  const onHidden = useCallback((m: FrameMsg) => {
    if (m.event === "init") waiters.current.init?.();
    else if (m.event === "load") waiters.current.load?.();
    else rpc.handle(m);
  }, [rpc]);

  const waitFor = (k: "init" | "load", ms: number) => new Promise<boolean>((done) => {
    const t = window.setTimeout(() => { waiters.current[k] = undefined; done(false); }, ms);
    waiters.current[k] = () => { window.clearTimeout(t); waiters.current[k] = undefined; done(true); };
  });

  /** Каждая страница → PNG или SVG (data URL) через вспомогательный редактор. */
  const exportPages = async (pages: BoardPage[], fmt: "png" | "svg"): Promise<string[]> => {
    const ready = waitFor("init", 60000);
    setHiddenOn(true);
    try {
      if (!(await ready)) throw new Error("Вспомогательный редактор не запустился");
      const out: string[] = [];
      for (const [i, page] of pages.entries()) {
        setBusy(`Готовим файл… страница ${i + 1} из ${pages.length}`);
        const loaded = waitFor("load", 30000);
        hidden.current?.post({ action: "load", xml: page.xml, autosave: 0 });
        if (!(await loaded)) throw new Error(`Страница «${page.name}» не открылась в редакторе`);
        const r = fmt === "png" ? await rpc.request("png", { scale: 2, background: "#ffffff", transparent: false }) : await rpc.request("svg", WHITE);
        if (typeof r?.data !== "string") throw new Error(`Редактор не вернул страницу «${page.name}»`);
        out.push(r.data);
      }
      return out;
    } finally { setHiddenOn(false); rpc.dispose(); }
  };

  const audit = (format: string, pages: number) => { if (meetingId) void api.logBoardExport(meetingId, format, pages).catch(() => undefined); };

  const run = async (what: Kind) => {
    setBusy("Готовим файл…"); setErr("");
    try {
      const xmlMsg = await request("xml");
      if (typeof xmlMsg?.xml !== "string") throw new Error("Редактор не ответил");
      if (what === "xml") { saveXml(xmlMsg.xml, `${fileBase}.drawio`); audit("drawio", splitPages(xmlMsg.xml).length || 1); setBusy(""); return; }
      const pages = splitPages(xmlMsg.xml);
      if (pages.length <= 1) {                                        // одна страница: как раньше, через рабочий редактор
        if (what === "svg") {
          const r = await request("svg", WHITE);
          if (typeof r?.data !== "string" || !saveDataUrl(r.data, `${fileBase}.svg`)) throw new Error("Редактор не вернул SVG");
        } else {
          const r = await request("png", { scale: 2, background: "#ffffff", transparent: false });
          if (typeof r?.data !== "string") throw new Error("Редактор не вернул изображение");
          if (what === "png") { if (!saveDataUrl(r.data, `${fileBase}.png`)) throw new Error("Изображение повреждено"); }
          else saveBlob(await pngsToPdf([r.data]), `${fileBase}.pdf`);
        }
        audit(what === "pdf" ? "pdf" : what, 1);
      } else if (what === "pdf") {
        saveBlob(await pngsToPdf(await exportPages(pages, "png")), `${fileBase}.pdf`);
        audit("pdf", pages.length);
      } else {
        const urls = await exportPages(pages, what);
        const files = urls.map((u, i) => {
          const d = dataUrlBytes(u);
          if (!d) throw new Error(`Страница «${pages[i].name}» повреждена`);
          return { name: `${String(i + 1).padStart(2, "0")}_${safeFileName(pages[i].name)}.${what}`, data: d.bytes };
        });
        saveBlob(new Blob([zipStore(files) as BlobPart], { type: "application/zip" }), `${fileBase}_${what}.zip`);
        audit(what === "png" ? "png" : "svg", pages.length);
      }
    } catch (e) { setErr((e as Error).message || "Не удалось сформировать файл"); }
    setBusy("");
  };

  return (
    <span className="row tight">
      <Menu label={busy || "Скачать"} className="btn mini" title="Сохранить схему на компьютер. Если в схеме несколько страниц: PNG и SVG — архивом ZIP по файлу на страницу, PDF — одним документом">
        <button onClick={() => void run("png")}>Картинка PNG</button>
        <button onClick={() => void run("svg")}>Вектор SVG</button>
        <button onClick={() => void run("pdf")}>Документ PDF</button>
        <button onClick={() => void run("xml")}>Файл draw.io (.drawio) — для редактирования</button>
      </Menu>
      {err && <span className="field-err" role="alert">{err}</span>}
      {hiddenOn && <DrawioFrame ref={hidden} onMessage={onHidden} className="drawio-offscreen" title="Вспомогательный редактор для выгрузки схемы" />}
    </span>
  );
}
