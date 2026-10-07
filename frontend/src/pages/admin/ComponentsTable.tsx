import { useCallback, useEffect, useState } from "react";
import { version as lkClientVersion } from "livekit-client";
import { api, type ApiError, type ComponentRow, type ComponentsInfo } from "../../api";
import { fmt } from "../../util";

const STATUS: Record<ComponentRow["status"], [string, string]> = {
  ok: ["актуально", "badge ok"], newer: ["есть новая", "badge warn"], ahead: ["новее известной", "badge"], unknown: ["нет данных", "badge"],
};

/**
 * Версии компонентов: что установлено на сервере · с какой версией проект проверен · что актуально в интернете · статус и что делать.
 * Версии браузерной части (livekit-client) известны только браузеру — подставляются здесь. Сервер без выхода в интернет получает «нет данных».
 */
export default function ComponentsTable({ compact }: { compact?: boolean }) {
  const [info, setInfo] = useState<ComponentsInfo | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async (refresh = false) => {
    setBusy(true); setErr("");
    try { setInfo(await api.admin.updateComponents(refresh)); } catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const rows = (info?.rows ?? []).map((r) => (r.key === "livekit_client_js" ? { ...r, installed: lkClientVersion, status: statusFor(lkClientVersion, r.latest) } : r));
  return (
    <div className="components">
      <div className="row"><h3 style={{ margin: 0 }}>Версии компонентов</h3><div className="spacer" />
        {info?.fetched_at && <span className="muted small">данные из интернета: {fmt(new Date(info.fetched_at * 1000).toISOString())}</span>}
        <button className="btn mini" onClick={() => load(true)} disabled={busy}>{busy ? "Запрос…" : "Обновить данные"}</button></div>
      {err && <div className="alert error">{err}</div>}
      {info && !info.internet && <div className="alert info">Сервер не получил сведений из интернета (нет выхода наружу или источники недоступны) — столбец «Актуально в интернете» пуст. Установленные версии показаны.</div>}
      <div className="table-scroll">
        <table className="table compact">
          <thead><tr><th>Компонент</th><th>Установлено на сервере</th><th title="Версия, с которой проект проверен разработчиками">Проверено с проектом</th><th>Актуально в интернете</th><th>Статус</th>{!compact && <th>Что делать</th>}</tr></thead>
          <tbody>
            {rows.map((r) => {
              const [label, cls] = STATUS[r.status];
              return (
                <tr key={r.key}>
                  <td>{r.title}{r.pinned && <span className="badge" title="Версия закреплена проектом"> закреплён</span>}</td>
                  <td><code>{r.installed ?? "—"}</code></td>
                  <td>{r.tested ? <code>{r.tested}</code> : <span className="muted">—</span>}</td>
                  <td>{r.latest ? <code>{r.latest}</code> : <span className="muted">—</span>}</td>
                  <td><span className={cls}>{label}</span></td>
                  {!compact && <td className="small muted">{r.note}</td>}
                </tr>
              );
            })}
            {!rows.length && !err && <tr><td colSpan={compact ? 5 : 6} className="muted">Загрузка…</td></tr>}
          </tbody>
        </table>
      </div>
      <p className="muted small">Компоненты обновляются вместе с проектом: отдельные версии ставить не нужно и не рекомендуется — новая версия, не проверенная вместе с проектом, может нарушить совместимость
        (например, старый LiveKit даёт медленный вход, а непроверенный новый — неизвестные риски). Совместимость проверяется на практике: scripts/smoke-test.sh и раздел «Состояние системы».</p>
    </div>
  );
}

function statusFor(installed: string, latest: string | null): ComponentRow["status"] {
  if (!latest) return "unknown";
  const a = installed.split(".").map(Number), b = latest.replace(/^v/, "").split(".").map(Number);
  for (let i = 0; i < Math.max(a.length, b.length); i++) { const x = a[i] ?? 0, y = b[i] ?? 0; if (x !== y) return x < y ? "newer" : "ahead"; }
  return "ok";
}
