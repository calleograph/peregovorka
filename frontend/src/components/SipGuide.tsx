import { Fragment, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type DiagResult, type SipCallRow, type SipGuide, type SipProfile, type SipSummaryItem } from "../api";
import { normalizeNumber } from "../phone";
import { StageList, fmtDate } from "../pages/admin/common";
import { Modal } from "./Dialogs";

const mark = (ok: boolean | null) => (ok === true ? "✓" : ok === false ? "✗" : "•");

/** Пять результатов «Проверить подключение»: каждый подтверждён своей проверкой; «ещё не проверялось» не выдаётся за успех. */
export function SummaryList({ items }: { items: SipSummaryItem[] }) {
  return (
    <ul className="stage-list sip-summary" aria-label="Результаты проверки подключения">
      {items.map((s) => (
        <li key={s.key} className={s.ok === false ? "bad" : s.ok === null ? "skip" : "ok"}>
          <span className="stage-mark" aria-hidden>{mark(s.ok)}</span>
          <span><b>{s.title}.</b> {s.ok === null ? <span className="muted">{s.text}</span> : s.text}</span>
        </li>
      ))}
    </ul>
  );
}

/** Проверка подключения выбранного профиля: настройки → служба → АТС → тестовый вызов → звук. Звук в обратную сторону подтверждает администратор на слух. */
export function ConnectionCheck({ profile, onChanged }: { profile: SipProfile; onChanged?: () => void }) {
  const [summary, setSummary] = useState<SipSummaryItem[] | null>(null);
  const [diag, setDiag] = useState<DiagResult | null>(null);
  const [busy, setBusy] = useState("");
  const [num, setNum] = useState("");
  const [err, setErr] = useState("");
  const load = useCallback(() => api.admin.sipSummary(profile.id).then((r) => setSummary(r.summary)).catch((e: ApiError) => setErr(e.message)), [profile.id]);
  useEffect(() => { setDiag(null); setErr(""); void load(); }, [load]);

  const run = async (kind: "check" | "call") => {
    setErr(""); setBusy(kind);
    try {
      if (kind === "check") { const r = await api.admin.checkSip(profile.id); setDiag(r); setSummary(r.summary ?? null); }
      else {
        const n = normalizeNumber(num);
        if (!n) { setErr("Введите номер для тестового вызова: цифры и символы + * #."); setBusy(""); return; }
        const r = await api.admin.testCallSip(profile.id, n);
        setSummary(r.summary ?? null);
        if (!r.ok) setErr(r.message);
      }
      onChanged?.();
    } catch (e) { setErr((e as ApiError).message); }
    setBusy("");
  };
  const confirm = async (heard: boolean) => {
    setErr(""); setBusy("confirm");
    try { setSummary((await api.admin.sipConfirmAudio(profile.id, heard)).summary); } catch (e) { setErr((e as ApiError).message); }
    setBusy("");
  };
  const callOk = summary?.find((s) => s.key === "test_call")?.ok === true;
  const confirmed = summary?.find((s) => s.key === "audio_confirmed")?.ok;

  return (
    <div className="sip-check">
      <div className="row">
        <button className="btn primary" disabled={busy !== ""} onClick={() => void run("check")}>{busy === "check" ? "Проверка…" : "Проверить подключение"}</button>
        {profile.direction !== "inbound" && <>
          <input value={num} onChange={(e) => setNum(e.target.value)} placeholder="Номер для тестового вызова" inputMode="tel" style={{ maxWidth: 260 }} aria-label="Номер для тестового вызова" />
          <button className="btn" disabled={busy !== ""} onClick={() => void run("call")}>{busy === "call" ? "Вызываем…" : "Тестовый вызов"}</button></>}
      </div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {summary ? <SummaryList items={summary} /> : <p className="muted">Загрузка…</p>}
      {callOk && confirmed === null && (
        <div className="alert info" role="status">
          Абонент ответил. Автоматически проверяется только звук <b>от АТС к серверу</b>; в обратную сторону подтвердите сами: при тестовом вызове вы слышали тональный сигнал/звук и вас было слышно?
          <div className="row" style={{ marginTop: 6 }}>
            <button className="btn mini" disabled={busy !== ""} onClick={() => void confirm(true)}>Да, слышно в обе стороны</button>
            <button className="btn mini danger" disabled={busy !== ""} onClick={() => void confirm(false)}>Нет, звука нет или он односторонний</button></div>
        </div>)}
      {diag && <StageList result={diag} />}
    </div>
  );
}

function download(name: string, text: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  a.download = name;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

const TABS = [["scheme", "Схема подключения"], ["ports", "Сетевые порты"], ["pbx", "Настройки АТС"], ["diag", "Диагностика"]] as const;

/** Инструкция для инженера АТС: формируется сервером из фактических настроек этой установки и выбранного профиля (пароли не включаются). */
export function SipGuideDialog({ profiles, initialId, onClose }: { profiles: SipProfile[]; initialId?: string; onClose: () => void }) {
  const [pid, setPid] = useState(initialId ?? profiles.find((p) => p.is_default)?.id ?? profiles[0]?.id ?? "");
  const [g, setG] = useState<SipGuide | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("scheme");
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState("");
  useEffect(() => { setG(null); setErr(""); api.admin.sipGuide(pid || undefined).then(setG).catch((e: ApiError) => setErr(e.message)); }, [pid]);
  const profile = profiles.find((p) => p.id === pid);

  const copy = async () => {
    if (!g) return;
    try { await navigator.clipboard.writeText(g.text); setCopied("Скопировано"); }
    catch { setCopied("Не удалось скопировать — используйте «Скачать инструкцию»"); }
    window.setTimeout(() => setCopied(""), 4000);
  };
  return (
    <Modal title="Инструкция по подключению АТС" onClose={onClose} wide noAutofocus>
      <div className="row">
        {profiles.length > 1 && <label className="inline">Профиль <select value={pid} onChange={(e) => setPid(e.target.value)}>{profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>}
        {profiles.length === 1 && <span className="muted">Профиль: {profiles[0].name}</span>}
        {!profiles.length && <span className="muted">Профилей нет — показаны общие параметры сервера. Создайте профиль для точных значений.</span>}
        <div className="spacer" />
        <button className="btn" onClick={() => void copy()} disabled={!g}>Скопировать инструкцию</button>
        <button className="btn" onClick={() => g && download("peregovorka-sip-instruction.txt", g.text)} disabled={!g}>Скачать инструкцию</button>
      </div>
      {copied && <div className="alert ok" role="status">{copied}</div>}
      {err && <div className="alert error" role="alert">{err}</div>}
      <div className="tabs" role="tablist" aria-label="Разделы инструкции">
        {TABS.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => setTab(id)}>{label}</button>)}
      </div>
      {!g && !err && <p className="muted">Формируем инструкцию по настройкам сервера…</p>}
      {g && tab === "scheme" && (
        <div>
          <div className="sg-flow" aria-label="Схема: SIP-телефон, АТС, SIP-служба, LiveKit, участники">
            {g.diagram.nodes.map((n, i) => {
              const link = g.diagram.links[i];
              return (
                <Fragment key={n.id}>
                  <div className="sg-node">{n.label}</div>
                  {link && <div className={`sg-link ${link.firewall ? "fw" : ""}`}><span aria-hidden>↓</span> {link.label}{link.firewall && <b className="badge warn"> нужно правило брандмауэра</b>}</div>}
                </Fragment>
              );
            })}
          </div>
          <table className="table compact"><tbody>
            {g.params.map((p) => <tr key={p.key}><td>{p.key}</td><td><b>{p.value}</b>{p.note && <div className="muted small">{p.note}</div>}</td></tr>)}
          </tbody></table>
        </div>)}
      {g && tab === "ports" && (
        <div>
          <p className="muted">Настройте правила только для соединений между Peregovorka и АТС. Внутренние соединения контейнеров наружу не открываются. Значения взяты из конфигурации этой установки.</p>
          <div className="table-wrap"><table className="table compact">
            <thead><tr><th>Источник</th><th>Назначение</th><th>Протокол</th><th>Порт</th><th>Назначение соединения</th></tr></thead>
            <tbody>
              {g.network.filter((r) => r.scope === "pbx").map((r, i) => <tr key={i}><td>{r.src}</td><td>{r.dst}</td><td>{r.proto}</td><td><b>{r.port}</b></td><td>{r.purpose}</td></tr>)}
              <tr><th colSpan={5} className="muted small">Внутри сервера (наружу не открывать)</th></tr>
              {g.network.filter((r) => r.scope === "internal").map((r, i) => <tr key={`i${i}`} className="muted"><td>{r.src}</td><td>{r.dst}</td><td>{r.proto}</td><td>{r.port}</td><td>{r.purpose}</td></tr>)}
            </tbody></table></div>
          <div className="alert info">{g.tls_note}</div>
        </div>)}
      {g && tab === "pbx" && (
        <div>
          <h3>Передайте инженеру SIP следующие данные</h3>
          <pre className="sg-text" tabIndex={0}>{g.text}</pre>
          <h3>Уточните у инженера АТС</h3>
          <ul>{g.ask.map((a) => <li key={a}>{a}</li>)}</ul>
          <h3>Чек-лист</h3>
          <ul className="sg-checklist">{g.checklist.map((a) => <li key={a}><label className="check"><input type="checkbox" /> {a}</label></li>)}</ul>
          <p className="muted small">Пароли и ключи в инструкцию не входят — передайте их инженеру отдельным защищённым каналом.</p>
        </div>)}
      {tab === "diag" && (profile ? <ConnectionCheck profile={profile} /> : <div className="alert info">Для диагностики нужен SIP-профиль: создайте его и вернитесь сюда.</div>)}
    </Modal>
  );
}

const DIR: Record<string, string> = { out: "исходящий", in: "входящий", test: "тестовый" };
const RES: Record<string, string> = { answered: "ответили", failed: "не удался", denied: "отклонён политикой", dialing: "набор" };
const END: Record<string, string> = { hangup: "отключён", remote: "завершил абонент", meeting_end: "конец встречи", limit: "предел длительности", lost: "состояние потеряно" };

/** Журнал телефонных вызовов (новые сверху). */
export function SipCallLog() {
  const [rows, setRows] = useState<SipCallRow[] | null>(null);
  const [dir, setDir] = useState("");
  const [res, setRes] = useState("");
  const [err, setErr] = useState("");
  const load = useCallback(() => api.admin.sipCalls({ direction: dir, result: res, limit: 100 }).then((r) => { setRows(r.items); setErr(""); }).catch((e: ApiError) => setErr(e.message)), [dir, res]);
  useEffect(() => { void load(); }, [load]);
  return (
    <div className="card" aria-label="Журнал вызовов">
      <div className="row"><h3 style={{ margin: 0 }}>Журнал вызовов</h3><div className="spacer" />
        <select value={dir} onChange={(e) => setDir(e.target.value)} aria-label="Направление"><option value="">Все направления</option><option value="in">Входящие</option><option value="out">Исходящие</option><option value="test">Тестовые</option></select>
        <select value={res} onChange={(e) => setRes(e.target.value)} aria-label="Результат"><option value="">Любой результат</option><option value="answered">Ответили</option><option value="failed">Не удались</option><option value="denied">Отклонены</option></select>
        <button className="btn mini" onClick={() => void load()}>Обновить</button></div>
      {err && <div className="alert error" role="alert">{err}</div>}
      {rows && !rows.length && <p className="muted">Вызовов пока не было. Отклонённые до попадания в сервер (АТС, адрес не в списке) здесь не видны — смотрите журнал АТС.</p>}
      {rows && rows.length > 0 && (
        <div className="table-wrap"><table className="table compact">
          <thead><tr><th>Время</th><th>Направление</th><th>Комната</th><th>Кто</th><th>Номер</th><th>Результат</th><th>Длительность</th><th>Идентификатор</th></tr></thead>
          <tbody>{rows.map((c) => (
            <tr key={c.id}><td>{fmtDate(c.started_at)}</td><td>{DIR[c.direction]}</td><td>{c.room ?? "—"}</td><td>{c.initiator ?? "—"}</td><td>{c.number || "—"}</td>
              <td className={c.result === "answered" ? "" : "bad-text"}>{RES[c.result]}{c.sip_status ? ` (SIP ${c.sip_status})` : ""}{c.reason ? <div className="muted small">{c.reason}</div> : null}{c.end_reason ? <div className="muted small">{END[c.end_reason] ?? c.end_reason}</div> : null}</td>
              <td>{c.result === "answered" ? (c.ended ? `${Math.floor((c.duration_s ?? 0) / 60)}:${String((c.duration_s ?? 0) % 60).padStart(2, "0")}` : "идёт") : "—"}</td>
              <td className="muted small">{c.tech_id ?? "—"}</td></tr>))}</tbody></table></div>)}
    </div>
  );
}
