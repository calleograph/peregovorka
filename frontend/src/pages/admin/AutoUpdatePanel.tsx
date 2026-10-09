import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type AutoUpdateInfo } from "../../api";
import { spanRu } from "../../util";
import { fmtDate } from "./common";

const RESULT: Record<string, { text: string; tone: string }> = {
  updated: { text: "обновлено", tone: "ok" }, no_update: { text: "новой версии нет", tone: "ok" }, failed: { text: "ошибка", tone: "warn" },
  deferred: { text: "отложено — были встречи", tone: "warn" }, skipped: { text: "пропущено", tone: "warn" },
};
const PHASE: Record<string, string> = { checking: "проверяет наличие новой версии…", waiting: "найдена новая версия, ждёт окна без встреч", running: "идёт обновление…" };

/** Автообновление: раз в сутки в заданное время; идущие встречи не прерываются. Показывает следующий запуск и итог последнего. */
export default function AutoUpdatePanel() {
  const [info, setInfo] = useState<AutoUpdateInfo | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [time, setTime] = useState("00:00");
  const [winH, setWinH] = useState(6);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api.admin.autoUpdate().then((i) => { setInfo(i); setEnabled(i.settings.enabled); setTime(i.settings.time); setWinH(i.settings.window_hours); }).catch((e) => setMsg({ ok: false, text: (e as ApiError).message })), []);
  useEffect(() => { void load(); const t = window.setInterval(() => { void api.admin.autoUpdate().then(setInfo).catch(() => undefined); }, 15000); return () => window.clearInterval(t); }, [load]);

  const save = async () => {
    setBusy(true); setMsg(null);
    try { await api.admin.saveSettings("autoupdate", { enabled, time, window_hours: winH } as never); setMsg({ ok: true, text: "Сохранено." }); await load(); }
    catch (e) { setMsg({ ok: false, text: (e as ApiError).message }); }
    setBusy(false);
  };
  const runNow = async () => {
    setBusy(true); setMsg(null);
    try { await api.admin.autoUpdateRun(); setMsg({ ok: true, text: "Цикл запущен: проверка версии и, если нужно, обновление — по тем же правилам, с защитой от идущих встреч." }); await load(); }
    catch (e) { setMsg({ ok: false, text: (e as ApiError).message }); }
    setBusy(false);
  };

  const last = info?.last;
  const res = last ? RESULT[last.result] ?? { text: last.result, tone: "" } : null;
  const dirty = !!info && (enabled !== info.settings.enabled || time !== info.settings.time || winH !== info.settings.window_hours);
  return (
    <section className="card autoupd" aria-labelledby="autoupd-h">
      <h3 id="autoupd-h" style={{ margin: "0 0 6px" }}>Автоматическое обновление</h3>
      <p className="muted small" style={{ marginTop: 0 }}>Раз в сутки в выбранное время сервис проверяет, есть ли новая версия, и если есть — ставит её тем же способом, что кнопка «Обновить проект».
        <b> Идущие встречи не прерываются:</b> пока они есть, обновление ждёт окна без встреч, а если за отведённое время его нет — переносится на следующие сутки.</p>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <label className="check" style={{ margin: 0 }}><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /><span className="check-body">Автоматически устанавливать обновления</span></label>
        <label style={{ margin: 0 }}>Время запуска<input type="time" value={time} onChange={(e) => setTime(e.target.value)} disabled={!enabled} style={{ width: 130 }} /></label>
        <label style={{ margin: 0 }}>Ждать окна без встреч<select value={winH} onChange={(e) => setWinH(Number(e.target.value))} disabled={!enabled}>
          {[1, 3, 6, 12].map((h) => <option key={h} value={h}>до {h} ч после назначенного времени</option>)}</select></label>
        <button className="btn primary" onClick={() => void save()} disabled={busy || !dirty}>Сохранить</button>
        <button className="btn" onClick={() => void runNow()} disabled={busy || (info?.phase ?? "idle") !== "idle"} title="Один цикл по тем же правилам прямо сейчас — например, чтобы проверить настройку">Выполнить сейчас</button>
      </div>
      <p className="small" style={{ margin: "10px 0 4px" }}>
        {info?.phase && info.phase !== "idle" ? <span className="badge warn">{PHASE[info.phase] ?? info.phase}</span>
          : info?.settings.enabled && info.next_run_at ? <>Следующий запуск: <b>{fmtDate(info.next_run_at)}</b></> : <span className="muted">Автообновление выключено.</span>}
        {info?.deferred && <span className="field-err"> · откладывается: {info.deferred}</span>}
      </p>
      {last && res && (
        <div className={`alert ${res.tone === "ok" ? "ok" : "error"} small`} role="status">
          <b>Последний автоматический запуск — {fmtDate(new Date(last.at * 1000).toISOString())}: {res.text}.</b>
          {(last.from_version || last.to_version) && <> Версия {last.from_version || "?"}{last.to_version && last.to_version !== last.from_version ? ` → ${last.to_version}` : ""}.</>}
          {last.duration_s != null && <> Длительность: {spanRu(last.duration_s)}.</>}
          {last.error && <div>{last.error}</div>}
          {last.detail && <div>{last.detail}</div>}
        </div>
      )}
      {msg && <div className={`alert ${msg.ok ? "ok" : "error"} small`} role="status">{msg.text}</div>}
    </section>
  );
}

