import { Fragment, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ApiError, type JournalFacets, type JournalFilter, type JournalQuery, type JournalRow, type JournalStats } from "../../api";
import { ConfirmDialog } from "../../components/Dialogs";
import { CATEGORY_LABEL, FIELD_LABEL, LEVEL_LABEL, OP_LABEL, PRESETS, addEquals, describeFilter, isEmptyQuery, opsFor } from "../../journalFilters";
import { ListFooter, useInfinite } from "../../useInfinite";
import { bytes } from "../../util";

const RANGES: [string, string][] = [["1h", "Последний час"], ["24h", "Последние сутки"], ["7d", "7 дней"], ["30d", "30 дней"], ["", "За всё время"]];
const time = (iso: string) => new Date(iso).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "medium" });

/** Значение ячейки, по которому можно отфильтровать одним щелчком. */
function Cell({ field, value, label, onPick }: { field: string; value: string | null; label?: string; onPick: (field: string, value: string) => void }) {
  if (!value) return <span className="muted">—</span>;
  return <button type="button" className="cell-link" title={`Показать только: ${FIELD_LABEL[field]} = ${value}`} onClick={(e) => { e.stopPropagation(); onPick(field, value); }}>{label ?? value}</button>;
}

/**
 * Журнал событий: бесконечная лента (без страниц), фильтры с условиями «равно / не равно / содержит…», выбор записей флажками и удаление,
 * выгрузка архива за сутки или 30 дней, объём журнала и состояние внешнего хранилища.
 */
export default function JournalAdmin({ onOpenSettings }: { onOpenSettings?: () => void }) {
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [range, setRange] = useState("24h");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [level, setLevel] = useState("");          // "" | "gte:warn" | "eq:error" …
  const [category, setCategory] = useState("");
  const [user, setUser] = useState("");
  const [room, setRoom] = useState("");
  const [adv, setAdv] = useState<JournalFilter[]>([]);
  const [facets, setFacets] = useState<JournalFacets | null>(null);
  const [stats, setStats] = useState<JournalStats | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [lastClicked, setLastClicked] = useState<number | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [live, setLive] = useState(false);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [confirm, setConfirm] = useState<"selected" | "matching" | "purge" | null>(null);
  const [advOpen, setAdvOpen] = useState(false);
  const [dense, setDense] = useState(() => { try { return localStorage.getItem("pg:journalDense") === "1"; } catch { return false; } });

  useEffect(() => { const t = window.setTimeout(() => setQ(text), 300); return () => window.clearTimeout(t); }, [text]);

  const filters = useMemo<JournalFilter[]>(() => {
    const out: JournalFilter[] = [];
    if (level) { const [op, v] = level.split(":"); out.push({ field: "level", op: op as "gte" | "eq", value: v }); }
    if (category) out.push({ field: "category", op: "eq", value: category });
    if (user.trim()) out.push({ field: "user", op: "eq", value: user.trim() });
    if (room) out.push({ field: "room", op: "eq", value: room });
    return [...out, ...adv.filter((f) => (Array.isArray(f.value) ? f.value.length : String(f.value).trim()))];
  }, [level, category, user, room, adv]);
  const query = useMemo<JournalQuery>(() => ({ filters, q, range: since || until ? "" : range, since: since ? new Date(since).toISOString() : undefined, until: until ? new Date(until).toISOString() : undefined }),
    [filters, q, range, since, until]);
  const qKey = JSON.stringify(query);

  const list = useInfinite<JournalRow>(async (_o, last) => {
    const p = await api.admin.journal(query, last?.id);
    return { rows: p.items, more: p.next_cursor != null };
  }, [qKey]);

  const loadStats = () => { api.admin.journalStats().then(setStats).catch(() => undefined); };
  useEffect(() => { loadStats(); api.admin.journalFacets().then(setFacets).catch(() => undefined); const t = window.setInterval(loadStats, 30000); return () => window.clearInterval(t); }, []);
  useEffect(() => { setSelected(new Set()); setOpen(null); }, [qKey]);

  // автообновление: каждые 8 с подгружаем только то, что новее самой верхней записи
  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(async () => {
      const top = list.items[0]?.id ?? 0;
      try {
        const p = await api.admin.journal(query, undefined, 100);
        const fresh = p.items.filter((r) => r.id > top);
        if (fresh.length) list.prepend(fresh);
      } catch { /* повторим */ }
    }, 8000);
    return () => window.clearInterval(t);
  }, [live, query, list]);

  const pick = (field: string, value: string) => setAdv((a) => addEquals(a, field, value));
  const reset = () => { setText(""); setQ(""); setLevel(""); setCategory(""); setUser(""); setRoom(""); setAdv([]); setRange("24h"); setSince(""); setUntil(""); };
  const empty = isEmptyQuery({ filters, q }, since || until ? "" : range) ;
  const applyPreset = (id: string) => { const p = PRESETS.find((x) => x.id === id); if (!p) return; setLevel(""); setCategory(""); setUser(""); setRoom(""); setAdv(p.query.filters); setAdvOpen(true); };

  const toggle = (id: number, shift: boolean) => {
    setSelected((s) => {
      const n = new Set(s);
      if (shift && lastClicked !== null) {
        const ids = list.items.map((r) => r.id);
        const a = ids.indexOf(lastClicked), b = ids.indexOf(id);
        if (a >= 0 && b >= 0) { for (const x of ids.slice(Math.min(a, b), Math.max(a, b) + 1)) n.add(x); return n; }
      }
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
    setLastClicked(id);
  };
  const allSelected = list.items.length > 0 && list.items.every((r) => selected.has(r.id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(list.items.map((r) => r.id)));

  const doDelete = async () => {
    if (confirm === "selected") {
      const ids = [...selected];
      const r = await api.admin.journalDelete({ ids });
      list.remove((x) => selected.has(x.id)); setSelected(new Set()); setNote(`Удалено записей: ${r.deleted}`);
    } else if (confirm === "matching") {
      const r = await api.admin.journalDelete({ all_matching: true, ...query });
      setSelected(new Set()); list.reload(); setNote(`Удалено записей: ${r.deleted}`);
    } else if (confirm === "purge") {
      const r = await api.admin.journalPurge();
      list.reload(); setNote(`По сроку хранения удалено: в базе ${r.db}, во внешнем хранилище дней ${r.external_days}`);
    }
    loadStats();
  };

  const updateAdv = (i: number, patch: Partial<JournalFilter>) => setAdv((a) => a.map((f, k) => {
    if (k !== i) return f;
    const next = { ...f, ...patch } as JournalFilter;
    if (patch.field && !opsFor(patch.field).includes(next.op)) next.op = opsFor(patch.field)[0];
    return next;
  }));

  const ext = stats?.external;
  return (
    <section className="journal">
      <div className="row"><h2>Журнал событий</h2><div className="spacer" />
        <a className="btn" href={api.admin.journalExportUrl("24h")} download title="ZIP: journal.csv (для Excel), journal.ndjson и audit.csv за последние 24 часа">⬇ Выгрузить за сутки</a>
        <a className="btn" href={api.admin.journalExportUrl("30d")} download title="ZIP: journal.csv (для Excel), journal.ndjson и audit.csv за 30 дней">⬇ Полный архив за 30 дней</a>
      </div>
      <p className="muted small">Что происходило в системе: входы, подключения к комнатам, обрывы, ошибки микрофона и камеры, создание протоколов, действия администраторов. Пароли, ключи и содержимое разговоров в журнал не попадают.</p>
      {stats && <div className={`alert ${stats.errors_24h ? "error" : "ok"}`} role="status">{stats.errors_24h ? `За сутки ошибок: ${stats.errors_24h}. Покажите только их: уровень «Ошибка» в фильтрах ниже.` : "За сутки ошибок нет."}</div>}

      {stats && (
        <div className="kpi">
          <div className="card"><div className="v">{stats.total.toLocaleString("ru-RU")}</div><div className="l">записей в журнале (за {stats.last_24h.toLocaleString("ru-RU")} — последние сутки)</div></div>
          <div className="card"><div className="v">{bytes(stats.size_bytes)}</div><div className="l">занимает журнал{stats.audit.size_bytes ? ` (аудит: ${bytes(stats.audit.size_bytes)})` : ""}</div></div>
          <div className="card"><div className={`v ${stats.errors_24h ? "bad-text" : ""}`}>{stats.errors_24h}</div><div className="l">ошибок за сутки · предупреждений {stats.warns_24h}</div></div>
          <div className="card"><div className="v">{stats.retention_days} дн.</div><div className="l">срок хранения{stats.oldest ? `; самая старая запись: ${time(stats.oldest)}` : ""}</div></div>
          <div className="card"><div className="v small-v">{!stats.keep_local && !ext?.enabled ? "не ведётся" : ext?.enabled ? (ext.ok === false ? "ошибка" : "включено") : "только локально"}</div>
            <div className="l">внешнее хранилище{ext?.enabled ? ` (${ext.mode === "smb" ? "SMB" : "каталог"})` : ""}{ext?.error ? `: ${ext.error}` : ext?.at ? `; последняя выгрузка ${time(ext.at)}` : ""}{!stats.keep_local ? " · в базе не хранится" : ""}</div></div>
        </div>
      )}
      {stats && !stats.keep_local && <div className="alert info">Хранение журнала в базе сервера выключено: ниже видны только события, записанные до отключения. Новые события уходят во внешнее хранилище{ext?.enabled ? "" : " — но оно не включено, поэтому журнал сейчас не ведётся вовсе"}.</div>}

      <div className="card filters">
        <div className="row filter-row">
          <input className="grow" value={text} onChange={(e) => setText(e.target.value)} placeholder="Поиск по сообщению, событию, пользователю, комнате, IP, браузеру…" aria-label="Поиск по журналу" />
          <select value={range} onChange={(e) => { setRange(e.target.value); setSince(""); setUntil(""); }} aria-label="Период" disabled={!!(since || until)}>
            {RANGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
        <div className="row filter-row">
          <label className="inline">Уровень
            <select value={level} onChange={(e) => setLevel(e.target.value)}>
              <option value="">любой</option><option value="gte:warn">предупреждения и ошибки</option><option value="eq:error">только ошибки</option><option value="eq:warn">только предупреждения</option>
              <option value="eq:info">только сведения</option><option value="eq:debug">отладка</option>
            </select></label>
          <label className="inline">Категория
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">любая</option>
              {(facets?.categories ?? Object.keys(CATEGORY_LABEL)).map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c] ?? c}</option>)}
            </select></label>
          <label className="inline">Пользователь
            <input list="journal-users" value={user} onChange={(e) => setUser(e.target.value)} placeholder="логин" />
            <datalist id="journal-users">{facets?.users.map((u) => <option key={u} value={u} />)}</datalist></label>
          <label className="inline">Комната
            <select value={room} onChange={(e) => setRoom(e.target.value)}>
              <option value="">любая</option>{facets?.rooms.map((r) => <option key={r} value={r}>{r}</option>)}
            </select></label>
          <label className="inline">С <input type="datetime-local" value={since} onChange={(e) => setSince(e.target.value)} /></label>
          <label className="inline">По <input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} /></label>
        </div>
        <div className="row filter-row chips" aria-label="Готовые наборы фильтров">
          <span className="muted small">Быстро:</span>
          {PRESETS.map((p) => <button key={p.id} type="button" className="chip" title={p.hint} onClick={() => applyPreset(p.id)}>{p.label}</button>)}
          <div className="spacer" />
          <button type="button" className="btn mini" onClick={() => setAdvOpen((v) => !v)} aria-expanded={advOpen}>Условия{adv.length ? ` (${adv.length})` : ""} {advOpen ? "▴" : "▾"}</button>
          {!empty && <button type="button" className="btn mini ghost" onClick={reset}>Сбросить фильтры</button>}
        </div>
        {advOpen && (
          <div className="adv">
            {adv.length === 0 && <div className="muted small">Добавьте условие: например, «Пользователь не равно ivanov» или «Сообщение содержит микрофон». Условия объединяются через «И». Щелчок по значению в таблице тоже добавляет условие.</div>}
            {adv.map((f, i) => (
              <div className="row tight adv-row" key={i}>
                <select value={f.field} onChange={(e) => updateAdv(i, { field: e.target.value })} aria-label="Поле">
                  {Object.entries(FIELD_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
                <select value={f.op} onChange={(e) => updateAdv(i, { op: e.target.value as JournalFilter["op"] })} aria-label="Условие">
                  {opsFor(f.field).map((o) => <option key={o} value={o}>{OP_LABEL[o]}</option>)}
                </select>
                {f.field === "level"
                  ? <select value={String(f.value)} onChange={(e) => updateAdv(i, { value: e.target.value })} aria-label="Значение"><option value="">—</option>{Object.entries(LEVEL_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
                  : <input value={Array.isArray(f.value) ? f.value.join(", ") : f.value} onChange={(e) => updateAdv(i, { value: e.target.value })} placeholder="значение" aria-label="Значение" list={`adv-${f.field}`} />}
                <button type="button" className="btn mini ghost danger" onClick={() => setAdv((a) => a.filter((_, k) => k !== i))} aria-label="Убрать условие">✕</button>
              </div>
            ))}
            <datalist id="adv-event">{facets?.events.map((v) => <option key={v} value={v} />)}</datalist>
            <datalist id="adv-user">{facets?.users.map((v) => <option key={v} value={v} />)}</datalist>
            <datalist id="adv-room">{facets?.rooms.map((v) => <option key={v} value={v} />)}</datalist>
            <datalist id="adv-client">{facets?.clients.map((v) => <option key={v} value={v} />)}</datalist>
            <datalist id="adv-category">{facets?.categories.map((v) => <option key={v} value={v} />)}</datalist>
            <button type="button" className="btn mini" disabled={adv.length >= 8} onClick={() => setAdv((a) => [...a, { field: "user", op: "ne", value: "" }])}>＋ Добавить условие</button>
          </div>
        )}
        {adv.length > 0 && <div className="applied small muted">Применено: {adv.map(describeFilter).join(" · ")}</div>}
      </div>

      <div className="bulkbar" role="toolbar" aria-label="Действия с записями">
        <label className="check"><input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="Выбрать все загруженные записи" /> Выбрать все загруженные</label>
        <span className="muted small">{selected.size ? `Выбрано: ${selected.size}` : `Загружено: ${list.items.length}${list.done ? "" : "+"}`}</span>
        <div className="spacer" />
        <label className="check small"><input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Автообновление</label>
        <button className="btn mini danger" disabled={selected.size === 0} onClick={() => setConfirm("selected")}>Удалить выбранные{selected.size ? ` (${selected.size})` : ""}</button>
        <button className="btn mini ghost danger" disabled={list.items.length === 0} onClick={() => setConfirm("matching")} title="Удалить все записи, подходящие под текущие фильтры (в том числе ещё не загруженные)">Удалить все по фильтру</button>
        <button className="btn mini" onClick={() => setConfirm("purge")} title="Удалить записи старше срока хранения прямо сейчас">Применить срок хранения</button>
        {onOpenSettings && <button className="btn mini" onClick={onOpenSettings}>Настройки хранения →</button>}
      </div>
      {note && <div className="alert ok" role="status">{note} <button className="btn mini ghost" onClick={() => setNote("")}>Закрыть</button></div>}
      {err && <div className="alert error" role="alert">{err}</div>}

      <label className="check dense-toggle"><input type="checkbox" checked={dense} onChange={(e) => { setDense(e.target.checked); try { localStorage.setItem("pg:journalDense", e.target.checked ? "1" : "0"); } catch { /* не запомнится */ } }} />
        <span className="check-body">Компактно</span></label>
      <div className={`journal-scroll ${dense ? "dense" : ""}`}>
        <table className="table journal-table">
          <thead><tr><th className="c-chk" /><th>Время</th><th>Уровень</th><th>Категория</th><th>Событие</th><th>Пользователь</th><th>Комната</th><th>IP</th><th>Браузер и ОС</th><th>Сообщение</th></tr></thead>
          <tbody>
            {list.items.map((r) => (
              <Fragment key={r.id}>
                <tr className={`${selected.has(r.id) ? "sel" : ""} lv-${r.level}`} onClick={() => setOpen(open === r.id ? null : r.id)}>
                  <td className="c-chk" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(r.id)} onChange={() => undefined} onClick={(e) => toggle(r.id, e.shiftKey)} aria-label={`Выбрать запись ${r.id}`} /></td>
                  <td className="nowrap">{time(r.at)}</td>
                  <td><span className={`lvl ${r.level}`}>{LEVEL_LABEL[r.level] ?? r.level}</span></td>
                  <td><Cell field="category" value={r.category} label={CATEGORY_LABEL[r.category]} onPick={pick} /></td>
                  <td><Cell field="event" value={r.event} onPick={pick} /></td>
                  <td><Cell field="user" value={r.user} onPick={pick} /></td>
                  <td><Cell field="room" value={r.room} onPick={pick} /></td>
                  <td><Cell field="ip" value={r.ip} onPick={pick} /></td>
                  <td className="small"><Cell field="client" value={r.client} onPick={pick} /></td>
                  <td className="msg small">{r.message ?? ""}</td>
                </tr>
                {open === r.id && (
                  <tr className="detail"><td colSpan={10}>
                    <div className="detail-grid">
                      <div><b>Сообщение</b><div>{r.message ?? "—"}</div></div>
                      {r.meeting_id && <div><b>Встреча</b><div className="small"><Link to={`/history/${r.meeting_id}`}>открыть встречу</Link></div></div>}
                      <details className="wide tech-details"><summary>Технические детали</summary>
                        <div className="small">запись #{r.id}{r.request_id ? ` · запрос ${r.request_id}` : ""}{r.meeting_id ? ` · встреча ${r.meeting_id}` : ""}</div>
                        {r.data && <pre className="debug-log">{JSON.stringify(r.data, null, 2)}</pre>}
                      </details>
                    </div>
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      <ListFooter loading={list.loading} done={list.done} error={list.error} count={list.items.length} sentinel={list.sentinel} empty="По этим условиям записей нет." />

      {confirm && (
        <ConfirmDialog
          title={confirm === "purge" ? "Применить срок хранения?" : confirm === "matching" ? "Удалить все записи по фильтру?" : `Удалить выбранные записи (${selected.size})?`}
          confirmLabel={confirm === "purge" ? "Применить" : "Удалить"} danger={confirm !== "purge"} typed={confirm === "matching" ? "УДАЛИТЬ" : undefined}
          onClose={() => setConfirm(null)}
          body={confirm === "purge"
            ? <p>Записи старше {stats?.retention_days ?? 30} дней будут удалены сразу (обычно это происходит само раз в час).</p>
            : confirm === "matching"
              ? <><p>Будут удалены <b>все</b> записи журнала, подходящие под текущие фильтры{adv.length || level || category || user || room || q ? "" : " — фильтров нет, это весь журнал за выбранный период"}, включая те, что ещё не прокручены на экране.</p><p className="muted small">Журнал аудита действий администраторов не затрагивается. Удаление записывается в аудит.</p></>
              : <p>Выбранные записи будут удалены безвозвратно. Удаление записывается в журнал аудита.</p>}
          onConfirm={async () => { try { await doDelete(); } catch (e) { setErr((e as ApiError).message); throw e; } }} />
      )}
    </section>
  );
}
