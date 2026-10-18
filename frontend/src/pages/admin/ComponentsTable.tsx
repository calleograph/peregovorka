import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type ComponentRow, type ComponentsInfo } from "../../api";
import { fmt } from "../../util";

export const STATUS: Record<ComponentRow["status"], [string, string]> = {
  current: ["Актуально", "badge ok"], available: ["Доступно обновление", "badge warn"], deferred: ["Обновление отложено", "badge warn"], review: ["Требуется проверка", "badge warn"],
  error: ["Ошибка определения версии", "badge bad"], manual: ["Не отслеживается", "badge"], off: ["Не запущен", "badge"],
};

/**
 * «Версии компонентов»: что РАБОТАЕТ на сервере (из запущенных контейнеров) · с чем проверен этот релиз · что доступно · статус и решение по обновлению.
 * Версии берёт сервер из манифеста релиза (`backend/app/components.json`) и работающих служб; здесь только показ. Полная пересборка воспроизводит комплект текущего релиза —
 * новые версии приходят вместе с релизом после проверки совместимости (docs/DEPENDENCIES.md).
 */
export default function ComponentsTable({ compact }: { compact?: boolean }) {
  const [info, setInfo] = useState<ComponentsInfo | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [onlyIssues, setOnlyIssues] = useState(false);
  const load = useCallback(async (refresh = false) => {
    setBusy(true); setErr("");
    try { setInfo(await api.admin.updateComponents(refresh)); } catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const all = info?.rows ?? [];
  const issues = all.filter((r) => ["available", "deferred", "review", "error"].includes(r.status) || r.security.state === "advisories");
  const rows = onlyIssues || compact ? (issues.length || !info ? issues : []) : all;
  const groups = [...new Set(rows.map((r) => r.group))];
  const counts = { review: all.filter((r) => r.status === "review").length, error: all.filter((r) => r.status === "error").length, deferred: all.filter((r) => r.status === "deferred").length, available: all.filter((r) => r.status === "available").length };
  const pc = info?.postcheck;

  return (
    <div className="components">
      <div className="row"><h3 style={{ margin: 0 }}>Версии компонентов</h3><div className="spacer" />
        {!compact && <label className="check"><input type="checkbox" checked={onlyIssues} onChange={(e) => setOnlyIssues(e.target.checked)} /> <span className="check-body">Только требующие внимания</span></label>}
        <button className="btn mini" onClick={() => load(true)} disabled={busy}>{busy ? "Запрос…" : "Обновить данные"}</button></div>
      {info && <p className="muted small">Релиз {info.release}; ревизия версий и уязвимостей — {info.reviewed_at}{info.fetched_at ? `; свежие данные из интернета — ${fmt(new Date(info.fetched_at * 1000).toISOString())}` : ""}.
        {" "}Компонентов: {all.length}; требуют проверки: {counts.review}; не определены: {counts.error}; обновления отложены: {counts.deferred}; доступны: {counts.available}.</p>}
      {err && <div className="alert error" role="alert">{err}</div>}
      {info && !info.internet && <div className="alert info">Сервер не получил сведений из интернета (нет выхода наружу или источники недоступны): «Доступно» показано по ревизии релиза от {info.reviewed_at}. Работающие версии определены.</div>}
      {pc && (
        <div className={`alert ${pc.worst === 0 ? "ok" : pc.worst === 1 ? "info" : "error"}`} role="status">
          <b>Сверка после последнего обновления ({fmt(pc.checked_at)}):</b> {pc.worst === 0 ? `работающие компоненты соответствуют релизу ${pc.release}.` : pc.worst === 1 ? "есть расхождения, не критичные для работы." : "КРИТИЧЕСКИЕ расхождения: работает не тот комплект, что в релизе."}
          {pc.worst > 0 && <ul className="stage-list">{pc.components.filter((x) => x.status !== "ok").map((x) => <li key={x.key} className="bad"><span>{x.title}: работает {x.running ?? "не определено"}, по релизу {x.expected}{x.critical ? " (критично)" : ""}</span></li>)}</ul>}
        </div>)}
      {!rows.length && !err && info && <p className="muted">Все компоненты соответствуют релизу и актуальны.</p>}
      {groups.map((g) => (
        <div key={g} className="table-scroll">
          <table className="table compact">
            <caption className="muted small" style={{ textAlign: "left", fontWeight: 600 }}>{g}</caption>
            <thead><tr><th>Компонент</th><th title="Версия, которая работает на сервере сейчас">Работает</th><th title="Версия, с которой проверен этот релиз">Проверено с релизом</th><th title="Последняя версия на дату проверки">Доступно</th><th>Статус</th>{!compact && <th>Решение и подробности</th>}</tr></thead>
            <tbody>
              {rows.filter((r) => r.group === g).map((r) => {
                const [label, cls] = STATUS[r.status];
                return (
                  <tr key={r.key}>
                    <td>{r.title}{r.bundle && <span className="badge" title="Проверяется вместе с остальными компонентами комплекта LiveKit"> комплект {r.bundle}</span>}</td>
                    <td><code>{r.installed ?? "—"}</code></td>
                    <td><code>{r.tested.length > 18 ? r.tested.slice(0, 12) : r.tested}</code></td>
                    <td>{r.latest ? <code>{r.latest.length > 18 ? r.latest.slice(0, 12) : r.latest}</code> : <span className="muted">—</span>}</td>
                    <td><span className={cls}>{label}</span>{r.security.state === "advisories" && <div><span className="badge bad" title={r.security.ids.join(", ")}>уязвимости: {r.security.ids.length}</span></div>}</td>
                    {!compact && (
                      <td className="small">
                        {r.note && <div className="muted">{r.note}</div>}
                        {r.deferred && <div>Задача <b>{r.deferred.issue}</b>, повторная проверка до {r.deferred.recheck}.{r.deferred.plan ? ` План: ${r.deferred.plan}` : ""}</div>}
                        <div className="muted">проверено {r.checked_at}{r.changelog && <> · <a href={r.changelog} target="_blank" rel="noreferrer">что изменилось</a></>}</div>
                      </td>)}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ))}
      {!info && !err && <p className="muted">Загрузка…</p>}
      <p className="muted small"><b>Как обновляются компоненты.</b> Версии закреплены в проекте и проверяются при каждом релизе: новая версия попадает в релиз после проверки совместимости либо откладывается с причиной, задачей и датой повторной проверки.
        «Полная пересборка» воспроизводит комплект текущего релиза (она не ищет новые версии) — новые версии приходят вместе с релизом через «Обновить проект». Устанавливать компоненты по отдельности не нужно и не рекомендуется.
        Статус «Требуется проверка» означает, что работает версия, отличная от проверенной в релизе (например, образ не пересобран).</p>
    </div>
  );
}
