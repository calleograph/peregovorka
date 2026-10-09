import { Fragment, useEffect, useMemo, useState } from "react";
import { api, type DeliveryLogRow } from "../api";
import { MATERIAL_KINDS } from "./DeliveryEditor";
import { fmt } from "../util";

const STATE: Record<string, string> = { queued: "в очереди", sending: "отправляется", sent: "отправлено", failed: "ошибка" };
const TRIGGER: Record<string, string> = { auto: "автоматически", manual: "вручную", test: "тест" };

interface Batch { key: string; at: string; by: string; trigger: string; template: string | null; kinds: string[]; formats: string[] | null; rows: DeliveryLogRow[] }

/** «Отправленные материалы» встречи: когда, кем (человек или автоматически), какой шаблон, какие документы, кому и с каким результатом. Тел писем и реквизитов SMTP нет. */
export default function DeliveryLog({ meetingId, refreshKey = 0 }: { meetingId: string; refreshKey?: number }) {
  const [rows, setRows] = useState<DeliveryLogRow[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => { void api.delivery.log(meetingId).then(setRows).catch(() => setRows([])); }, [meetingId, refreshKey]);
  const batches = useMemo<Batch[]>(() => {
    const m = new Map<string, Batch>();
    for (const r of rows ?? []) {
      const key = r.batch_id ?? r.id;
      const b = m.get(key) ?? { key, at: r.at, by: r.by ?? "—", trigger: r.trigger, template: r.template, kinds: r.kinds, formats: r.formats, rows: [] };
      b.rows.push(r);
      m.set(key, b);
    }
    return [...m.values()];
  }, [rows]);
  if (!rows || rows.length === 0) return null;
  const label = (k: string) => MATERIAL_KINDS.find((x) => x.kind === k)?.label ?? k;
  return (
    <div className="card delivery-log">
      <h3 style={{ marginTop: 0 }}>Отправленные материалы</h3>
      <table className="table compact">
        <thead><tr><th>Когда</th><th>Кто</th><th>Шаблон</th><th>Материалы</th><th>Получатели</th><th /></tr></thead>
        <tbody>
          {batches.map((b) => {
            const sent = b.rows.filter((r) => r.state === "sent").length, failed = b.rows.filter((r) => r.state === "failed").length;
            return (
              <Fragment key={b.key}>
                <tr>
                  <td>{fmt(b.at)}</td>
                  <td>{b.by}<div className="muted small">{TRIGGER[b.trigger] ?? b.trigger}</div></td>
                  <td>{b.template ?? <span className="muted">—</span>}</td>
                  <td>{b.kinds.map(label).join(", ")}{b.formats?.length ? <div className="muted small">{b.formats.join(", ").toUpperCase()}</div> : null}</td>
                  <td>{b.rows.length}: {sent} отправлено{failed ? <span className="badge err">ошибок: {failed}</span> : null}{b.rows.length - sent - failed > 0 ? ` · ждут: ${b.rows.length - sent - failed}` : ""}</td>
                  <td><button className="btn mini ghost" onClick={() => setOpen(open === b.key ? null : b.key)} aria-expanded={open === b.key}>{open === b.key ? "Скрыть" : "Подробнее"}</button></td>
                </tr>
                {open === b.key && (
                  <tr><td colSpan={6}>
                    <ul className="plain small">{b.rows.map((r) => (
                      <li key={r.id}>{r.name && r.name !== r.recipient ? `${r.name} · ` : ""}{r.recipient} — <b>{STATE[r.state] ?? r.state}</b>{r.sent_at ? ` (${fmt(r.sent_at)})` : ""}
                        {r.delivery === "link" ? " · ссылкой (документы большие)" : ""}{r.error ? <span className="muted"> · {r.error}{r.attempts > 1 ? ` · попыток: ${r.attempts}` : ""}</span> : null}</li>))}</ul>
                  </td></tr>)}
              </Fragment>);
          })}
        </tbody>
      </table>
    </div>
  );
}
