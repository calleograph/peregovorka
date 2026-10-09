import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError, type UpdateAttempt, type UpdatesOverview } from "../../api";
import { ConfirmDialog } from "../../components/Dialogs";
import { Markdown } from "../../components/Markdown";
import { downloadText, fmt, shortCommit, versionLabel } from "../../util";
import AutoUpdatePanel from "./AutoUpdatePanel";
import ChangesDialog from "./ChangesDialog";
import ComponentsTable from "./ComponentsTable";
import RepairsPanel from "./RepairsPanel";

const MAX_LOG = 600_000;

function ago(sec?: number | null): string {
  if (sec == null) return "—";
  if (sec < 90) return `${sec} с назад`;
  if (sec < 5400) return `${Math.round(sec / 60)} мин назад`;
  return `${Math.round(sec / 3600)} ч назад`;
}

/**
 * Обновление проекта из веб-интерфейса. Сам backend ничего не обновляет: запрос передаётся исполнителю на сервере (scripts/updater.sh),
 * который запускает штатный scripts/update.sh. Ход обновления (этапы, сборка образов, миграции, проверки) показывается построчно в окне;
 * окно переживает перезапуск самого сервиса — журнал читается с диска, соединение восстанавливается автоматически.
 */
export default function UpdatesAdmin({ onOpen }: { onOpen?: (page: string) => void } = {}) {
  const [ov, setOv] = useState<UpdatesOverview | null>(null);
  const [err, setErr] = useState("");
  const [log, setLog] = useState("");
  const [lost, setLost] = useState(false);
  const [force, setForce] = useState(false);
  const [pull, setPull] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [changesOpen, setChangesOpen] = useState(false);
  const [histChanges, setHistChanges] = useState<{ from: string; to: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [ranHere, setRanHere] = useState(false);   // обновление запускали из этого окна: показываем его ход и после завершения
  const [follow, setFollow] = useState(true);
  const offset = useRef(0);
  const term = useRef<HTMLPreElement>(null);
  const watchUntil = useRef(0);   // после запуска опрашиваем часто, даже пока исполнитель не успел сменить состояние

  const loadOverview = useCallback(async () => {
    try { setOv(await api.admin.updates()); setErr(""); setLost(false); } catch (e) { const ae = e as ApiError; if (ae.status === 0 || ae.status >= 500) setLost(true); else setErr(ae.message); }
  }, []);

  const pollLog = useCallback(async () => {
    try {
      const r = await api.admin.updatesLog(offset.current);
      setLost(false);
      if (r.reset) setLog("");
      offset.current = r.offset;
      if (r.text) setLog((l) => (l + r.text).slice(-MAX_LOG));
    } catch (e) { const ae = e as ApiError; if (ae.status === 0 || ae.status >= 500) setLost(true); }
  }, []);

  useEffect(() => { void loadOverview(); void pollLog(); }, [loadOverview, pollLog]);
  const running = ov?.updater.state === "updating" || ov?.updater.state === "repairing" || ov?.updater.request_pending || Date.now() < watchUntil.current;
  useEffect(() => {
    const t = window.setInterval(() => { void pollLog(); void loadOverview(); }, running ? 1500 : 8000);
    return () => window.clearInterval(t);
  }, [running, pollLog, loadOverview]);
  useEffect(() => { if (follow && term.current) term.current.scrollTop = term.current.scrollHeight; }, [log, follow]);

  const act = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what); setErr("");
    try { await fn(); } catch (e) { setErr((e as ApiError).message); } finally { setBusy(""); }
  };
  const check = () => act("check", async () => { await api.admin.updatesCheck(); watchUntil.current = Date.now() + 15000; await loadOverview(); });
  const start = async () => {
    await api.admin.updatesRun({ confirm: true, force_build: force, pull });
    watchUntil.current = Date.now() + 60000;
    setRanHere(true);
    offset.current = 0; setLog(""); setFollow(true);
    await loadOverview();
  };

  const u = ov?.updater, rem = ov?.remote;
  const unknownBuild = !!ov && (!ov.installed.commit || ov.installed.commit === "unknown");
  const pct = u?.step_total ? Math.min(100, Math.round(((u.step_no ?? 0) / u.step_total) * 100)) : 0;
  const isRepair = u?.action === "repair";
  const oc = ov?.outcome ?? null;
  // Итоги раздельно: сбой проверки интеграций (LDAP и т. п.) — не «обновление завершено с ошибкой»: версия установлена, сервисы работают
  const finishedOk = u?.state === "idle" && u?.result === "ok", finishedBad = u?.state === "idle" && u?.result === "failed";
  const integFail = finishedOk && !isRepair && oc?.integrations === "fail";
  const st = (v?: string) => (v === "ok" ? <span className="badge ok">успешно</span> : v === "fail" || v === "failed" ? <span className="badge warn">ошибка</span> : v === "skipped" ? <span className="badge">пропущено</span> : <span className="badge">нет данных</span>);
  const behind = rem?.behind ?? 0;

  return (
    <section className="updates">
      <div className="row"><h2>Обновления</h2><div className="spacer" />
        <button className="btn" onClick={check} disabled={!!busy || !u?.available}>{busy === "check" ? "Проверка…" : "Проверить сейчас"}</button></div>
      <p className="muted">Здесь видно, какая редакция проекта установлена на сервере и что опубликовано на GitHub, и можно обновиться одной кнопкой. Обновление выполняет тот же скрипт, что и в командной строке
        (<code>scripts/update.sh</code>): резервная копия базы и настроек, сборка, миграции, перезапуск, проверки. Данные, модели и настройки не удаляются.</p>
      {err && <div className="alert error" role="alert">{err}</div>}
      {lost && <div className="alert info" role="status">Связь с сервером прервана — вероятно, он перезапускается при обновлении. Окно переподключится само; обновление продолжается на сервере.</div>}

      <div className="card upd-project">
        <div className="upd-versions">
          <div><div className="l">Установлено на сервере</div><div className="v small-v">{ov ? versionLabel(ov.installed.version, ov.installed.commit) : "—"}</div>
            {ov?.last_success && <div className="l">последнее успешное обновление: {fmt(new Date(ov.last_success.at * 1000).toISOString())} · {attemptRoute(ov.last_success)} · {ov.last_success.source === "web" ? "из веб-интерфейса" : "из терминала"}</div>}
            <div className="l">{unknownBuild ? <span className="badge warn" title="Образ собран без данных Git — вероятно, вручную командой docker compose build">собран вне штатного обновления</span> : ov?.installed.built_at && ov.installed.built_at !== "unknown" ? `сборка ${ov.installed.built_at}` : ""}</div></div>
          <div><div className="l">Опубликовано на GitHub (ветка main)</div><div className="v small-v">{rem?.ok ? (rem.remote_version ? versionLabel(rem.remote_version, rem.remote) : shortCommit(rem.remote)) : "—"}</div>
            <div className="l">{rem ? `проверено ${ago(rem.age_s)}` : "проверка ещё не выполнялась"}</div></div>
          <div><div className="l">Помощник обновлений</div>
            <div className="v small-v">{u?.available ? (ov?.helper.privileged ? <span className="badge ok">работает</span> : <span className="badge warn" title="Служба прежней версии работает без прав администратора сервера">работает без прав</span>) : <span className="badge warn">не установлен</span>}</div>
            <div className="l">{u?.heartbeat_age_s != null ? `пульс ${ago(u.heartbeat_age_s)}` : "нет данных"}</div></div>
        </div>

        {unknownBuild && (
          <div className="alert error unknown-build" role="alert">
            <b>Установленный образ собран вне штатного обновления</b> (commit «unknown»): обычно это следствие ручной команды <code>docker compose build</code>, которая не передаёт версию и commit.
            Работа системы это не нарушает, но проверка версий и отладка затруднены. <b>Рекомендация:</b> выполните обновление с опцией «Полная пересборка всех образов» (или на сервере <code>./scripts/rebuild.sh</code>).
            <div className="row" style={{ marginTop: 6 }}>{!force && <button className="btn mini primary" onClick={() => setForce(true)}>Отметить полную пересборку</button>}{force && <span className="badge ok">полная пересборка отмечена</span>}</div>
          </div>
        )}
        {!u?.available && ov && (
          <div className="alert error updater-missing" role="alert">
            <b>Кнопка «Обновить проект» пока недоступна: нужно один раз установить помощник обновлений на сервере.</b>
            При обычной установке («sudo ./install.sh») он ставится сам; здесь он не найден — например, сервер установлен прежней версией. Выполните один раз от администратора сервера:
            <pre className="cmd">cd каталог_проекта{"\n"}sudo ./scripts/updater.sh install --yes</pre>
            Помощник работает от root, но выполняет только фиксированный набор действий (обновление, исправление известных проблем, проверки); произвольные команды из веб-интерфейса выполнить нельзя.
            Пока он не установлен, обновляйте командой <code>sudo ./scripts/update.sh</code> на сервере — результат тот же, а помощник при этом установится сам.
          </div>
        )}
        {u?.available && ov && !ov.helper.privileged && (
          <div className="alert error updater-missing" role="alert">
            <b>Помощник обновлений работает без прав администратора сервера</b> (так его устанавливали прежние версии), поэтому «Исправить автоматически» и часть обновления (права на каталоги, настройки веб-сервера) из браузера недоступны.
            Один раз выполните от администратора сервера: <pre className="cmd">sudo ./scripts/updater.sh install --yes</pre> Либо просто обновите проект командой <code>sudo ./scripts/update.sh</code> — служба обновится сама.
          </div>
        )}
        {rem && !rem.ok && <div className="alert error">Не удалось проверить GitHub: {rem.error || "нет данных"}. Возможно, у сервера нет выхода в интернет — тогда обновляйте на сервере командой <code>./scripts/update.sh --env ...</code> из заранее полученного репозитория.</div>}
        {rem?.ok && behind === 0 && <div className="alert ok">Установлена актуальная редакция проекта{ov ? ` (версия ${ov.installed.version})` : ""}.</div>}
        {rem?.ok && behind > 0 && (
          <div className="upd-available">
            <div className="alert info"><b>Доступно обновление{rem.remote_version && ov && rem.remote_version !== ov.installed.version ? `: версия ${ov.installed.version} → ${rem.remote_version}` : ""}</b>
              {ov?.changes && !ov.changes.empty && (
                <div className="changelog small">
                  {ov.changes.groups.map((g) => `${g.title}: ${g.items.length}`).join(" · ")}
                  {(ov.changes.important.length > 0 || ov.changes.facts.length > 0) && <b className="field-err"> · есть важные изменения — откройте «Что нового»</b>}
                </div>
              )}
              {!ov?.changes && rem.changelog && <div className="changelog"><b>Что нового</b><Markdown source={rem.changelog} /></div>}
              <details className="muted small"><summary>Технический список: {behind} {behind === 1 ? "изменение" : behind < 5 ? "изменения" : "изменений"} в репозитории</summary>
              <ul className="commits">{rem.commits.slice(0, 12).map((c) => <li key={c.sha}><code>{c.sha}</code> <span className="muted small">{c.date}</span> {c.subject}</li>)}{behind > 12 && <li className="muted">…и ещё {behind - 12}</li>}</ul></details></div>
            <div className="row chips-info">
              {rem.migrations_changed > 0 && <span className="badge warn" title="Будет создана резервная копия базы, миграции применятся автоматически">изменения схемы БД: {rem.migrations_changed}</span>}
              {rem.env_example_changed && <span className="badge" title="Новые безопасные параметры дописываются в .env автоматически; остальные показываются списком">есть новые параметры .env</span>}
              {rem.local_changes > 0 && <span className="badge warn" title="Обновление остановится, пока на сервере есть несохранённые изменения файлов проекта">локальные изменения на сервере: {rem.local_changes}</span>}
              {!rem.ff_possible && <span className="badge warn" title="Локальная история расходится с GitHub">обновление «вперёд» невозможно</span>}
            </div>
            {(rem.local_changes > 0 || !rem.ff_possible) && <div className="alert error">Автоматическое обновление в этом состоянии остановится, ничего не меняя. Разберитесь на сервере: <code>git status</code>, затем <code>./scripts/update.sh</code>.</div>}
          </div>
        )}

        <fieldset className="group"><legend>Дополнительные параметры (необязательно)</legend>
          <p className="opts-lead"><b>Для обычного обновления оставьте обе опции выключенными.</b> Проект обновится сам: пересоберутся только те образы, у которых изменился код.</p>
          <label className="check"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
            <span className="check-body">Полная пересборка всех образов (обычно не требуется)
              <span className="help">Включайте после ручной сборки образов, если у установленного образа «commit unknown» или он помечен «собран вне штатного обновления», при проблемах с кэшем сборки или сомнениях в содержимом образов.
                Занимает намного дольше — десятки минут из-за ASR (PyTorch и GigaAM).</span></span></label>
          <label className="check"><input type="checkbox" checked={pull} onChange={(e) => setPull(e.target.checked)} />
            <span className="check-body">Обновить базовые образы PostgreSQL/Redis (необязательно)
              <span className="help">Это не обновление самого проекта и не обязательная часть каждого обновления: подтягиваются свежие сборки образов PostgreSQL и Redis (исправления безопасности) в пределах закреплённой мажорной версии.
                Обновление проекта работает и без этого.</span></span></label>
        </fieldset>
        <div className="alert">
          <b>Что произойдёт.</b> Сервис перезапустится на время от 1–2 минут (дольше при пересборке): <b>идущие звонки прервутся</b>, участники переподключатся сами. Поэтому обновляйтесь вне встреч.
          Перед миграциями делается резервная копия базы; если что-то пойдёт не так, обновление остановится с понятным сообщением, а откат — <code>./scripts/rollback.sh</code> (откат кода не откатывает базу — скрипт предупредит). Если есть сомнения — выполните обновление на сервере вручную.
        </div>
        {(ov?.active_meetings ?? 0) > 0 && <div className="alert error">Сейчас идут встречи: {ov?.active_meetings}. Обновление их прервёт.</div>}
        <div className="row">
          {behind > 0 && ov?.changes && (
            <button className="btn" onClick={() => setChangesOpen(true)} title="Что изменится между установленной и доступной версиями">Что нового{ov.changes.important.length + ov.changes.facts.length > 0 ? " ⚠" : ""}</button>
          )}
          <button className="btn primary" disabled={!ov?.can_update || !!busy} onClick={() => setConfirm(true)}
                  title={ov?.can_update ? "Запустить scripts/update.sh на сервере" : ov?.reasons.join(" ")}>⬆ Обновить проект{behind > 0 ? ` (${behind})` : ""}</button>
          {behind === 0 && rem?.ok && ov?.can_update && <span className="muted small">Новых изменений нет; обновление пересоберёт и проверит текущую редакцию.</span>}
          {!ov?.can_update && ov?.reasons.map((r) => <span key={r} className="muted small">{r}</span>)}
        </div>
      </div>

      <RepairsPanel onOpen={onOpen} quiet />

      {(running || ranHere) && (
        <div className="card upd-term">
          <div className="row">
            <h3 style={{ margin: 0 }}>{isRepair ? "Ход исправления" : "Ход обновления"}</h3>
            {running && <span className="badge rec">выполняется</span>}
            {finishedOk && !integFail && <span className="badge ok">завершено успешно</span>}
            {integFail && <span className="badge ok">обновление завершено</span>}
            {integFail && <span className="badge warn">интеграции: нужна проверка</span>}
            {finishedBad && <span className="badge warn">{isRepair ? "исправить не удалось" : "остановлено"} (код {u?.exit_code})</span>}
            {u?.by && <span className="muted small">запустил: {u.by}</span>}
            {u?.finished_at ? <span className="muted small">окончание: {fmt(new Date(u.finished_at * 1000).toISOString())}</span> : null}
            <div className="spacer" />
            <label className="check small"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> следить за концом</label>
            <button className="btn mini" disabled={!log} onClick={() => downloadText(log, `peregovorka-update-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.log`)}>Скачать журнал</button>
          </div>
          {u && (running || (u.step_no ?? 0) > 0) && (
            <div className="upd-progress" aria-label="Ход обновления">
              <div className="bar"><i style={{ width: `${running ? Math.max(pct, 3) : finishedOk ? 100 : pct}%` }} /></div>
              <div className="small">{u.step_no ? `Этап ${u.step_no} из ${u.step_total}: ${u.step_name}` : "Запуск…"}</div>
            </div>
          )}
          <pre className="term" ref={term} tabIndex={0} aria-live="off" onScroll={(e) => { const t = e.currentTarget; setFollow(t.scrollHeight - t.scrollTop - t.clientHeight < 40); }}>{log || "Ожидание вывода…"}</pre>
          {oc && !isRepair && !running && (
            <ul className="outcome" aria-label="Итог обновления">
              <li>Обновление программы: {st(oc.update)}</li>
              <li>Развёртывание (сервисы запущены): {st(oc.deployment)}</li>
              <li>Работоспособность (база, ASR, звонки, веб): {st(oc.health)}</li>
              <li>Интеграции (каталог LDAP и др.): {oc.integrations === "fail" ? <span className="badge warn">требуют внимания{oc.integration_issues ? `: ${oc.integration_issues}` : ""}</span> : st(oc.integrations)}</li>
            </ul>
          )}
          {integFail && (
            <div className="alert info">
              <b>Версия установлена, сервисы работают.</b> Проверка подключения к каталогу{oc?.integration_issues ? ` (${oc.integration_issues})` : ""} не прошла — это не ошибка обновления, но вход по домену может не работать.
              <div className="row" style={{ marginTop: 6 }}>
                <button className="btn primary" onClick={() => onOpen?.("ldap")}>Исправить LDAP / открыть диагностику LDAP</button>
                <button className="btn" onClick={() => onOpen?.("system")}>Обзор</button></div>
            </div>
          )}
          {finishedBad && <div className="alert error">{isRepair ? "Исправление не выполнено. Остальная система не затронута; причина — в последних строках журнала." : "Обновление остановилось."} Данные, настройки и модели не затронуты. Причина — в последних строках журнала. Исправьте её и нажмите «Обновить» снова (повтор безопасен) либо выполните <code>./scripts/update.sh</code> на сервере.</div>}
          {finishedOk && !isRepair && <div className="alert ok">Обновление выполнено{integFail ? "" : ": проверки пройдены"}. Обновите страницу, чтобы загрузить новую версию интерфейса.
            <button className="btn mini primary" onClick={() => window.location.reload()}>Обновить страницу</button></div>}
        </div>
      )}

      <AutoUpdatePanel />

      <div className="card upd-history">
        <h3 style={{ margin: "0 0 6px" }}>История попыток обновления</h3>
        {!ov?.history.length && <p className="muted small">Попыток пока нет (учитываются обновления через <code>scripts/update.sh</code> — и из терминала, и из этого раздела).</p>}
        {!!ov?.history.length && (
          <div className="table-scroll"><table className="table">
            <thead><tr><th>Когда</th><th>Версия</th><th>Откуда</th><th>Результат</th></tr></thead>
            <tbody>{ov.history.map((h, i) => {
              const superseded = h.result === "failed" && ov.history.slice(0, i).some((n) => n.result === "ok");
              return (
                <tr key={`${h.at}-${i}`} className={superseded ? "muted" : undefined}>
                  <td>{fmt(new Date(h.at * 1000).toISOString())}</td>
                  <td>{attemptRoute(h)}{h.has_changes && h.from_version && h.to_version && <> <button className="btn mini ghost" onClick={() => setHistChanges({ from: h.from_version, to: h.to_version })}>что изменилось</button></>}</td>
                  <td>{h.by === "auto-update" ? <span className="badge" title="Запущено автоматическим обновлением по расписанию">автоматически</span> : h.source === "web" ? `веб-интерфейс${h.by ? ` (${h.by})` : ""}` : "терминал"}</td>
                  <td>{h.result === "ok" ? <span className="badge ok">успешно</span> : <><span className="badge warn">ошибка</span> <span className="small">{h.stage}</span>{superseded && <span className="small"> · устранено последующим успешным обновлением</span>}</>}</td>
                </tr>);
            })}</tbody>
          </table></div>
        )}
        {!running && !ranHere && log && (
          <details className="small"><summary>Журнал последнего запуска из веб-интерфейса{u?.finished_at ? ` (${fmt(new Date(u.finished_at * 1000).toISOString())}${u.stale ? ", устарел — позднее обновление прошло успешно" : ""})` : ""}</summary>
            <pre className="term" tabIndex={0}>{log}</pre></details>
        )}
      </div>

      <div className="card"><ComponentsTable /></div>

      {changesOpen && ov?.changes && (
        <ChangesDialog data={ov.changes} onClose={() => setChangesOpen(false)}
          footer={<button className="btn primary" disabled={!ov.can_update || !!busy} onClick={() => { setChangesOpen(false); setConfirm(true); }}>⬆ Обновить проект</button>} />
      )}
      {histChanges && <ChangesDialog from={histChanges.from} to={histChanges.to} onClose={() => setHistChanges(null)} />}
      {confirm && (
        <ConfirmDialog title="Обновить проект на сервере?" confirmLabel="Обновить" danger={(ov?.active_meetings ?? 0) > 0} typed={(ov?.active_meetings ?? 0) > 0 ? "ОБНОВИТЬ" : undefined}
          onClose={() => setConfirm(false)}
          body={<>
            <p>Будет выполнен <code>scripts/update.sh</code>{force ? ", с пересборкой образов" : ""}{pull ? ", с обновлением базовых образов" : ""}. Сервис перезапустится, звонки прервутся на несколько минут.</p>
            {(ov?.active_meetings ?? 0) > 0 && <p><b>Сейчас идут встречи ({ov?.active_meetings}) — они будут прерваны.</b> Для подтверждения введите слово ниже.</p>}
            {behind > 0 && rem?.migrations_changed ? <p>В обновлении есть изменения схемы БД: перед ними автоматически создаётся резервная копия.</p> : null}
            <p className="muted small">Окно можно закрыть: обновление продолжится на сервере, ход останется в этом разделе.</p>
          </>}
          onConfirm={start} />
      )}
    </section>
  );
}

/** «0.1.3 · b93967a → 0.1.4 · 99defd0» — откуда и куда шла попытка (версия может быть неизвестна у очень старых записей). */
function attemptRoute(h: UpdateAttempt): string {
  const a = [h.from_version, h.from_commit && h.from_commit.slice(0, 7)].filter(Boolean).join(" · ");
  const b = [h.to_version, h.to_commit && h.to_commit.slice(0, 7)].filter(Boolean).join(" · ");
  return a && b && a !== b ? `${a} → ${b}` : b || a || "—";
}
