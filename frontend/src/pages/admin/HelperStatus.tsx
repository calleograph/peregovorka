import { useState } from "react";
import { api, type ApiError, type HelperInfo, type HelperState } from "../../api";

const BADGE: Record<HelperState, { cls: string; text: string; hint: string }> = {
  ok: { cls: "ok", text: "работает", hint: "Служба запущена от root, пульс идёт" },
  manual_process: { cls: "warn", text: "запущен вручную", hint: "Работает, но не как служба systemd: после перезагрузки сервера не запустится" },
  wrong_user: { cls: "warn", text: "работает без прав root", hint: "Процесс запущен не от root: кнопки исправления ограничены" },
  unresponsive: { cls: "warn", text: "запущен, но не отвечает", hint: "Пульс идёт, но основной цикл не обрабатывает запросы" },
  installed_inactive: { cls: "warn", text: "установлен, но не запущен", hint: "Служба есть на сервере, но её процесс не работает" },
  failed: { cls: "warn", text: "служба остановилась с ошибкой", hint: "Служба установлена, но аварийно завершилась" },
  stale_unit: { cls: "warn", text: "служба устарела", hint: "Файл службы отличается от актуального для этой версии" },
  not_installed: { cls: "warn", text: "не установлен", hint: "Службы помощника на сервере нет" },
};

export function HelperBadge({ h }: { h: HelperInfo | undefined }) {
  if (!h) return <span className="badge">…</span>;
  const b = BADGE[h.state] ?? BADGE.not_installed;
  return <span className={`badge ${b.cls}`} title={b.hint}>{b.text}</span>;
}

const FACTS: [string, string][] = [["unit_exists", "файл службы"], ["enabled", "автозапуск"], ["active", "состояние"], ["substate", "подсостояние"], ["result", "результат"], ["restarts", "перезапусков"],
  ["main_pid", "PID службы"], ["proc_user", "пользователь процесса"], ["proc_uid", "uid процесса"], ["expected_uid", "ожидаемый uid"], ["ping", "связь"]];

/** Что именно не так с помощником и что делать; «Проверить связь» отличает «запущен, но не отвечает» от «не запущен». */
export function HelperAlert({ h, onReload }: { h: HelperInfo; onReload: () => Promise<unknown> }) {
  const [ping, setPing] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  if (h.state === "ok") return null;
  const warnOnly = h.state === "manual_process";
  const check = async () => {
    setBusy(true); setPing(null);
    const t0 = Date.now();
    try {
      const { request_id } = await api.admin.updatesPing();
      for (let i = 0; i < 12; i++) {
        await new Promise((r) => setTimeout(r, 1500));
        const ov = await api.admin.updates();
        if (ov.updater.ping_id === request_id) { setPing({ ok: true, text: `Помощник ответил за ${Math.max(1, Math.round((Date.now() - t0) / 1000))} с — связь есть.` }); await onReload(); return; }
      }
      setPing({ ok: false, text: "Помощник не ответил за 18 с: он запущен, но не обрабатывает запросы. Перезапустите службу командой ниже." });
    } catch (e) { setPing({ ok: false, text: (e as ApiError).message }); }
    finally { setBusy(false); }
  };
  const facts = FACTS.filter(([k]) => h.details && h.details[k] !== undefined && h.details[k] !== null && h.details[k] !== "");
  return (
    <div className={`alert ${warnOnly ? "" : "error"} updater-missing`} role={warnOnly ? "status" : "alert"}>
      <b>{BADGE[h.state]?.text ? `Помощник обновлений: ${BADGE[h.state].text}.` : "Помощник обновлений требует внимания."}</b> {h.message}
      {h.fix && (<><br />Выполните на сервере (команда идемпотентна — повторный запуск безопасен):<pre className="cmd">cd каталог_проекта{"\n"}{h.fix.replace("<проект>", "…")}</pre></>)}
      <div className="row" style={{ marginTop: 6 }}>
        {h.available && <button className="btn mini" onClick={() => void check()} disabled={busy}>{busy ? "Проверяю…" : "Проверить связь"}</button>}
        {facts.length > 0 && <button className="btn mini ghost" onClick={() => setMore(!more)}>{more ? "Скрыть подробности" : "Подробности проверки"}</button>}
      </div>
      {ping && <div className={`alert ${ping.ok ? "ok" : "error"}`} role="status">{ping.text}</div>}
      {more && facts.length > 0 && <ul className="small">{facts.map(([k, l]) => <li key={k}>{l}: <code>{String(h.details![k])}</code></li>)}</ul>}
      <p className="muted small">Обновление командой <code>sudo ./scripts/update.sh</code> на сервере работает и без помощника; при этом помощник проверяется и восстанавливается автоматически.</p>
    </div>
  );
}
