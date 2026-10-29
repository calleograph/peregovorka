import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type NetRule, type NetworkInfo, type NetworkProbe } from "../../api";
import { useToast } from "../../components/Toast";
import { originMismatch } from "../../networkView";
import InfoTip from "../../components/InfoTip";

const REQ: Record<NetRule["required"], string> = { required: "обязательно", recommended: "рекомендуется", conditional: "если используется" };
const SCOPE_IN: Record<string, string> = { internet: "из интернета, от клиентов", internal: "внутренняя сеть", pbx: "только от АТС", local: "только сервер" };
const SCOPE_OUT: Record<string, string> = { internet: "в интернет", internal: "внутренняя сеть организации", pbx: "к АТС", local: "только сервер" };
const FORMATS: { id: "text" | "md" | "csv"; label: string }[] = [{ id: "text", label: "TXT" }, { id: "csv", label: "CSV" }, { id: "md", label: "Markdown" }];

/** Тот же перечень, что выводит `scripts/ports.sh`: строится по настройкам этого экземпляра, а не по зашитой таблице. */
export default function NetworkAdmin() {
  const [info, setInfo] = useState<NetworkInfo | null>(null);
  const [err, setErr] = useState("");
  const [probe, setProbe] = useState<NetworkProbe | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, show] = useToast();
  const load = useCallback(() => api.admin.network().then((d) => { setInfo(d); setErr(""); }).catch((e) => setErr((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);

  const copy = async (format: "text" | "request", done: string) => {
    try {
      const res = await fetch(`/api/v1/admin/network/export?format=${format}`, { credentials: "same-origin" });
      if (!res.ok) throw new Error(String(res.status));
      await navigator.clipboard.writeText(await res.text());
      show(done, "ok");
    } catch { show("Не удалось скопировать: разрешите доступ к буферу обмена или скачайте файл", "error"); }
  };
  const runProbe = async () => {
    setBusy(true);
    try { setProbe(await api.admin.networkProbe()); setErr(""); } catch (e) { setErr((e as ApiError).message); } finally { setBusy(false); }
  };

  const rows = (dir: "in" | "out") => (info?.rules ?? []).filter((r) => r.direction === dir);
  const mismatch = info ? originMismatch(window.location.hostname, info.public_host) : null;
  const table = (dir: "in" | "out") => (
    <div style={{ overflowX: "auto" }}>
      <table className="table compact">
        <thead><tr><th>Протокол</th><th>Порт</th><th>Обязательность</th><th>Откуда → куда</th><th>Компонент и назначение</th></tr></thead>
        <tbody>
          {rows(dir).map((r) => (
            <tr key={r.id + r.port + r.where}>
              <td>{r.proto}</td><td><code>{r.port}</code></td>
              <td><span className={`badge ${r.required === "required" ? "warn" : ""}`}>{REQ[r.required]}</span><div className="muted small">{(dir === "in" ? SCOPE_IN : SCOPE_OUT)[r.scope]}</div></td>
              <td>{r.where}</td>
              <td><strong>{r.component}</strong>. {r.purpose}{r.notes.map((n, i) => <div key={i} className="muted small">• {n}</div>)}</td>
            </tr>
          ))}
          {rows(dir).length === 0 && <tr><td colSpan={5} className="muted">Нет правил.</td></tr>}
        </tbody>
      </table>
    </div>
  );

  return (
    <section>
      <div className="row"><h2>Сетевая доступность</h2><div className="spacer" /><button className="btn mini" onClick={load}>Обновить</button></div>
      <p className="muted">Какие порты нужно открыть на межсетевом экране, чтобы сервис работал у ваших пользователей. Перечень строится по настройкам этого сервера; команда для терминала — <code>scripts/ports.sh</code> (работает и без веб-интерфейса).</p>
      {err && <div className="alert error">{err}</div>}
      {!info && !err && <p className="muted">Загрузка…</p>}
      {info && (
        <>
          <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
            <button className="btn" onClick={() => copy("text", "Список портов скопирован")}>Скопировать список</button>
            <button className="btn" onClick={() => copy("request", "Заявка сетевому администратору скопирована")}>Скопировать заявку для сетевого администратора</button>
            {FORMATS.map((f) => <a key={f.id} className="btn" href={`/api/v1/admin/network/export?format=${f.id}`} download>Скачать {f.label}</a>)}
          </div>
          <h3>Как установлен этот экземпляр</h3>
          <ul>{info.variant.map((v, i) => <li key={i}>{v}</li>)}</ul>
          {info.warnings.map((w, i) => <div key={i} className="alert warn">{w}</div>)}
          {mismatch && <div className="alert warn">{mismatch}</div>}
          <h3>Входящие соединения — открыть на межсетевом экране перед сервером</h3>
          {table("in")}
          <h3>Исходящие соединения сервера</h3>
          {table("out")}
          <h3>Не публиковать наружу</h3>
          <div style={{ overflowX: "auto" }}>
            <table className="table compact">
              <thead><tr><th>Протокол</th><th>Порт</th><th>Компонент</th><th>Назначение</th><th>Кто достаёт</th></tr></thead>
              <tbody>{info.local_only.map((x, i) => <tr key={i}><td>{x.proto}</td><td><code>{x.port}</code></td><td>{x.component}</td><td>{x.purpose}</td><td>{x.reachable}</td></tr>)}</tbody>
            </table>
          </div>
          <h3>Проверка с сервера</h3>
          <p className="muted small">Соединение устанавливается с самого сервера. Оно не проходит внешний NAT и межсетевой экран и не доказывает, что порт доступен из интернета.</p>
          <button className="btn" disabled={busy} onClick={runProbe}>{busy ? "Проверка…" : "Проверить с сервера"}</button>
          {probe && (
            <>
              <table className="table compact" style={{ marginTop: 8 }}>
                <thead><tr><th>Что</th><th>Адрес</th><th>Результат</th></tr></thead>
                <tbody>
                  {probe.checks.map((c) => <tr key={c.id + c.host + c.port}><td>{c.what}</td><td><code>{c.host}:{c.port}</code></td><td>{c.ok ? <span className="badge ok">отвечает, {c.ms} мс</span> : <span className="badge warn">{c.error}</span>}</td></tr>)}
                  {probe.not_checked.map((c) => <tr key={c.id}><td>{c.proto} {c.port}</td><td />
                    <td className="muted">не проверяется: {c.reason}</td></tr>)}
                </tbody>
              </table>
              <p className="muted small">{probe.disclaimer}</p>
            </>
          )}
          <h3>Как подтвердить доступ снаружи</h3>
          <p className="muted small">Достоверно — войти в звонок с компьютера вне сети организации (например, через мобильную сеть) и открыть «Диагностика клиентов»: там видно, шёл ли звук по UDP или по TCP.<InfoTip>Проверка порта с сервера или из этого окна такого подтверждения не даёт.</InfoTip></p>
          <h3>Ограничения</h3>
          <ul className="muted small">{info.limits.map((l, i) => <li key={i}>{l}</li>)}</ul>
        </>
      )}
      {toast}
    </section>
  );
}
