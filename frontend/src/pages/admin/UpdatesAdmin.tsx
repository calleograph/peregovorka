import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError, type UpdatesOverview } from "../../api";
import { ConfirmDialog } from "../../components/Dialogs";
import { downloadText, fmt } from "../../util";
import ComponentsTable from "./ComponentsTable";

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
export default function UpdatesAdmin() {
  const [ov, setOv] = useState<UpdatesOverview | null>(null);
  const [err, setErr] = useState("");
  const [log, setLog] = useState("");
  const [lost, setLost] = useState(false);
  const [force, setForce] = useState(false);
  const [pull, setPull] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState("");
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
  const running = ov?.updater.state === "updating" || ov?.updater.request_pending || Date.now() < watchUntil.current;
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
    offset.current = 0; setLog(""); setFollow(true);
    await loadOverview();
  };

  const u = ov?.updater, rem = ov?.remote;
  const pct = u?.step_total ? Math.min(100, Math.round(((u.step_no ?? 0) / u.step_total) * 100)) : 0;
  const finishedOk = u?.state === "idle" && u?.result === "ok", finishedBad = u?.state === "idle" && u?.result === "failed";
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
          <div><div className="l">Установлено на сервере</div><div className="v small-v">{ov ? `${ov.installed.version} · ${ov.installed.commit.slice(0, 12)}` : "—"}</div>
            <div className="l">{ov?.installed.built_at && ov.installed.built_at !== "unknown" ? `сборка ${ov.installed.built_at}` : ""}</div></div>
          <div><div className="l">Опубликовано на GitHub (ветка main)</div><div className="v small-v">{rem?.ok ? rem.remote : "—"}</div>
            <div className="l">{rem ? `проверено ${ago(rem.age_s)}` : "проверка ещё не выполнялась"}</div></div>
          <div><div className="l">Исполнитель обновлений</div>
            <div className="v small-v">{u?.available ? <span className="badge ok">работает</span> : <span className="badge warn">не запущен</span>}</div>
            <div className="l">{u?.heartbeat_age_s != null ? `пульс ${ago(u.heartbeat_age_s)}` : "нет данных"}</div></div>
        </div>

        {!u?.available && ov && (
          <div className="alert info">
            <b>Кнопка обновления станет доступна после запуска исполнителя на сервере</b> — один раз, под тем пользователем, который обычно выполняет <code>./scripts/update.sh</code>:
            <pre className="cmd">cd каталог_проекта{"\n"}./scripts/updater.sh install        # установить как службу systemd (спросит подтверждение, нужен sudo){"\n"}./scripts/updater.sh run            # или запустить вручную в этом терминале</pre>
            Пока он не запущен, обновляйте командой <code>./scripts/update.sh</code> на сервере — результат тот же. Исполнитель выполняет только штатный <code>update.sh</code>; произвольные команды из веб-интерфейса выполнить нельзя.
          </div>
        )}
        {rem && !rem.ok && <div className="alert error">Не удалось проверить GitHub: {rem.error || "нет данных"}. Возможно, у сервера нет выхода в интернет — тогда обновляйте на сервере командой <code>./scripts/update.sh --env ...</code> из заранее полученного репозитория.</div>}
        {rem?.ok && behind === 0 && <div className="alert ok">Установлена актуальная редакция проекта.</div>}
        {rem?.ok && behind > 0 && (
          <div className="upd-available">
            <div className="alert info"><b>Доступно обновление: {behind} {behind === 1 ? "изменение" : behind < 5 ? "изменения" : "изменений"}.</b>
              <ul className="commits">{rem.commits.slice(0, 12).map((c) => <li key={c.sha}><code>{c.sha}</code> <span className="muted small">{c.date}</span> {c.subject}</li>)}{behind > 12 && <li className="muted">…и ещё {behind - 12}</li>}</ul></div>
            <div className="row chips-info">
              {rem.migrations_changed > 0 && <span className="badge warn" title="Будет создана резервная копия базы, миграции применятся автоматически">изменения схемы БД: {rem.migrations_changed}</span>}
              {rem.env_example_changed && <span className="badge" title="Новые безопасные параметры дописываются в .env автоматически; остальные показываются списком">есть новые параметры .env</span>}
              {rem.local_changes > 0 && <span className="badge warn" title="Обновление остановится, пока на сервере есть несохранённые изменения файлов проекта">локальные изменения на сервере: {rem.local_changes}</span>}
              {!rem.ff_possible && <span className="badge warn" title="Локальная история расходится с GitHub">обновление «вперёд» невозможно</span>}
            </div>
            {(rem.local_changes > 0 || !rem.ff_possible) && <div className="alert error">Автоматическое обновление в этом состоянии остановится, ничего не меняя. Разберитесь на сервере: <code>git status</code>, затем <code>./scripts/update.sh</code>.</div>}
          </div>
        )}

        <fieldset className="group"><legend>Параметры</legend>
          <label className="check"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> <span className="check-body">Пересобрать образы заново<span className="help">Нужно, если образы собирались вручную или есть сомнения в их содержимом. Дольше (десятки минут из-за ASR).</span></span></label>
          <label className="check"><input type="checkbox" checked={pull} onChange={(e) => setPull(e.target.checked)} /> <span className="check-body">Обновить базовые образы (PostgreSQL, Redis)<span className="help">Только обновления безопасности в пределах закреплённой мажорной версии.</span></span></label>
        </fieldset>
        <div className="alert">
          <b>Что произойдёт.</b> Сервис перезапустится на время от 1–2 минут (дольше при пересборке): <b>идущие звонки прервутся</b>, участники переподключатся сами. Поэтому обновляйтесь вне встреч.
          Перед миграциями делается резервная копия базы; если что-то пойдёт не так, обновление остановится с понятным сообщением, а откат — <code>./scripts/rollback.sh</code> (откат кода не откатывает базу — скрипт предупредит). Если есть сомнения — выполните обновление на сервере вручную.
        </div>
        {(ov?.active_meetings ?? 0) > 0 && <div className="alert error">Сейчас идут встречи: {ov?.active_meetings}. Обновление их прервёт.</div>}
        <div className="row">
          <button className="btn primary" disabled={!ov?.can_update || !!busy} onClick={() => setConfirm(true)}
                  title={ov?.can_update ? "Запустить scripts/update.sh на сервере" : ov?.reasons.join(" ")}>⬆ Обновить проект{behind > 0 ? ` (${behind})` : ""}</button>
          {behind === 0 && rem?.ok && ov?.can_update && <span className="muted small">Новых изменений нет; обновление пересоберёт и проверит текущую редакцию.</span>}
          {!ov?.can_update && ov?.reasons.map((r) => <span key={r} className="muted small">{r}</span>)}
        </div>
      </div>

      {(log || running || u?.result) && (
        <div className="card upd-term">
          <div className="row">
            <h3 style={{ margin: 0 }}>Ход обновления</h3>
            {running && <span className="badge rec">выполняется</span>}
            {finishedOk && <span className="badge ok">завершено успешно</span>}
            {finishedBad && <span className="badge warn">завершено с ошибкой (код {u?.exit_code})</span>}
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
          {finishedBad && <div className="alert error">Обновление остановилось. Данные, настройки и модели не затронуты. Причина — в последних строках журнала. Исправьте её и нажмите «Обновить» снова (повтор безопасен) либо выполните <code>./scripts/update.sh</code> на сервере.</div>}
          {finishedOk && <div className="alert ok">Обновление выполнено: проверки пройдены. Обновите страницу, чтобы загрузить новую версию интерфейса.
            <button className="btn mini primary" onClick={() => window.location.reload()}>Обновить страницу</button></div>}
        </div>
      )}

      <div className="card"><ComponentsTable /></div>

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

