import { useEffect, useRef, useState } from "react";
import type { ProtocolItem } from "../api";
import { spanRu } from "../util";

/** Что сейчас происходит с документом словами: ожидание в очереди → подготовка материалов → анализ (обработано N из M) → подготовка документа. */
export function stageText(p: ProtocolItem): string {
  if (p.status === "pending") return "Ожидание: задание в очереди (тяжёлые задачи выполняются по одной и уступают звонку)";
  const l = p.live;
  if (l?.stage === "analysis") return `Анализ материалов: обработано ${l.done ?? 0} из ${l.total ?? "?"} фрагментов стенограммы`;
  if (l?.stage === "assemble") return "Подготовка документа";
  return "Подготовка материалов";
}

/** Прошедшее время идущего задания: значение сервера на момент ответа + секунды, прошедшие с тех пор в этом окне (часы клиента с часами сервера не сверяются). */
export function elapsedNow(base: number, fetchedAt: number, now: number): number {
  return base + Math.max(0, now - fetchedAt) / 1000;
}

interface Props { item: ProtocolItem; line?: string; onCancel?: () => void; cancelling?: boolean }

/** Панель «документ формируется»: этап, прошедшее время, предел, кнопка «Отменить». Страница обновляет данные сама (опрос в MeetingPage). */
export default function JobProgress({ item, line, onCancel, cancelling }: Props) {
  const fetchedAt = useRef(Date.now());
  const [, tick] = useState(0);
  useEffect(() => { fetchedAt.current = Date.now(); }, [item.live?.elapsed_s, item.status]);
  useEffect(() => { const t = window.setInterval(() => tick((n) => n + 1), 1000); return () => window.clearInterval(t); }, []);
  const running = item.status === "running" && item.live;
  const limit = item.live?.limit_s;
  return (
    <div className="card">
      <p role="status" style={{ margin: 0 }}><b>{stageText(item)}…</b></p>
      {running && (
        <p className="muted small" style={{ margin: "6px 0 0" }}>
          Прошло {spanRu(elapsedNow(item.live!.elapsed_s, fetchedAt.current, Date.now()))}
          {limit ? ` · задание остановится, если не завершится за ${spanRu(limit)}` : ""}
        </p>
      )}
      {line && <p className="muted small" style={{ margin: "4px 0 0" }}>{line}</p>}
      <p className="muted small" style={{ margin: "4px 0 0" }}>Страница обновится сама. Стенограмма и материалы встречи при этом не меняются.</p>
      {onCancel && <div className="row" style={{ marginTop: 8 }}>
        <button className="btn ghost" onClick={onCancel} disabled={cancelling}>{cancelling ? "Отмена…" : "Отменить формирование"}</button>
      </div>}
    </div>
  );
}
