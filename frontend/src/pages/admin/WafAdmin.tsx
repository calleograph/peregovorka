import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type WafRule, type WafState } from "../../api";
import { useToast } from "../../components/Toast";
import { fmt } from "../../util";
import InfoTip from "../../components/InfoTip";

const MODES: [WafState["mode"], string, string][] = [
  ["off", "Выключен", "Запросы не проверяются."],
  ["observe", "Наблюдение", "Подозрительные запросы записываются в журнал, но не блокируются. Рекомендуется для начала."],
  ["protect", "Защита", "Проверенные правила блокируют анонимные запросы; новые эвристические остаются в наблюдении, пока вы сами не переведёте их в «Блок»."],
];
const RULE_MODES: [WafRule["mode"], string][] = [["inherit", "По режиму"], ["off", "Выключено"], ["observe", "Наблюдение"], ["block", "Блок"]];

/** «Защита веб-приложения (WAF)»: лёгкий встроенный слой. Не заменяет внешний WAF и сетевой firewall; сначала наблюдение, затем защита. */
export default function WafAdmin() {
  const [st, setSt] = useState<WafState | null>(null);
  const [err, setErr] = useState("");
  const [paths, setPaths] = useState("");
  const [ips, setIps] = useState("");
  const [toast, show] = useToast();
  const load = useCallback(() => api.admin.waf().then((s) => { setSt(s); setPaths(s.exclude_paths); setIps(s.exclude_ips); setErr(""); }).catch((e) => setErr((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);
  const save = async (patch: Record<string, unknown>, ok = "Сохранено") => { try { setSt(await api.admin.wafUpdate(patch)); show(ok, "ok"); setErr(""); } catch (e) { setErr((e as ApiError).message); } };
  if (!st) return <div className="card">{err ? <div className="alert error" role="alert">{err}</div> : <p className="muted">Загрузка…</p>}</div>;
  const rulesMap = Object.fromEntries(st.rules.map((r) => [r.id, r.mode]));
  return (
    <>
      {toast}
      <div className="card">
        <h2>Защита веб-приложения (WAF)</h2>
        <p className="muted small" style={{ margin: "4px 0 10px" }}>Лёгкая встроенная защита от массового сканирования и типовых автоматических атак на API. Она <b>не заменяет</b> ни промышленный WAF (например, с OWASP Core Rule Set), ни межсетевой экран, ни защиту от DDoS, а дополняет проверку прав, параметризованные запросы и экранирование вывода. Тела запросов не сканируются: чаты и протоколы содержат произвольный текст. WebSocket, загрузки и скачивания не затрагиваются. Блокируются только анонимные запросы: вошедший пользователь на ложное срабатывание не наткнётся.</p>
        {err && <div className="alert error" role="alert">{err}</div>}
        <div className="sec-modes" role="radiogroup" aria-label="Режим WAF">
          {MODES.map(([id, title, hint]) => (
            <label key={id} className={`sec-mode${st.mode === id ? " on" : ""}`}>
              <input type="radio" name="wafmode" checked={st.mode === id} onChange={() => void save({ mode: id }, `Режим: ${title}`)} />
              <span><b>{title}</b><span className="muted small" style={{ display: "block" }}>{hint}</span></span>
            </label>
          ))}
        </div>
        <p className="muted small" style={{ margin: "8px 0 0" }}>Если перед сервером уже стоит настоящий WAF, оставьте здесь «Наблюдение» или «Выключен».<InfoTip>Настройки WAF не раскрываются неавторизованным пользователям; откат — переключатель режима (действует в течение нескольких секунд на всех экземплярах).</InfoTip></p>
      </div>
      <div className="card">
        <h2>Правила</h2>
        <div style={{ overflowX: "auto" }}>
          <table className="table small">
            <thead><tr><th>Правило</th><th>Тип</th><th>Режим</th><th>За 24 ч</th><th>Последний раз</th></tr></thead>
            <tbody>
              {st.rules.map((r) => (
                <tr key={r.id}>
                  <td><b>{r.title}</b><div className="muted small">{r.blurb}</div><code className="small">{r.id}</code></td>
                  <td>{r.verified ? <span className="badge ok">проверенное</span> : <span className="badge warn">эвристика</span>}</td>
                  <td><select aria-label={`Режим правила ${r.title}`} value={r.mode} onChange={(e) => void save({ rules: { ...rulesMap, [r.id]: e.target.value } })}>{RULE_MODES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></td>
                  <td>замечено {r.observed_24h}, заблокировано {r.blocked_24h}</td>
                  <td className="nowrap">{r.last_seen ? fmt(r.last_seen) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">Эвристики сначала работают в наблюдении: посмотрите их срабатывания в «События и блокировки» и только потом переводите в «Блок». «По режиму»: в «Защите» проверенные правила блокируют, эвристики остаются в наблюдении.</p>
        <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label>Перебор путей: ответов 404/405 в минуту<input type="number" min={10} max={10000} defaultValue={st.probe_threshold} onBlur={(e) => { const v = Number(e.target.value); if (v !== st.probe_threshold) void save({ probe_threshold: v }); }} style={{ width: 110 }} /></label>
          <label>Блокировка при переборе, минут<input type="number" min={1} max={1440} defaultValue={st.probe_block_minutes} onBlur={(e) => { const v = Number(e.target.value); if (v !== st.probe_block_minutes) void save({ probe_block_minutes: v }); }} style={{ width: 110 }} /></label>
        </div>
      </div>
      <div className="card">
        <h2>Исключения</h2>
        <p className="muted small" style={{ margin: "0 0 8px" }}>Запросы по этим путям и с этих адресов не проверяются. Используйте при ложных срабатываниях: сначала найдите событие в «События и блокировки», затем исключите путь или адрес.</p>
        <div className="row" style={{ gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
          <label style={{ flex: "1 1 280px" }}>Пути (по одному в строке, начало пути)<textarea rows={4} value={paths} onChange={(e) => setPaths(e.target.value)} placeholder="/api/v1/search" /></label>
          <label style={{ flex: "1 1 280px" }}>Адреса и подсети<textarea rows={4} value={ips} onChange={(e) => setIps(e.target.value)} placeholder="192.0.2.10\n198.51.100.0/24" /></label>
        </div>
        <div className="row" style={{ marginTop: 8 }}><button className="btn primary" onClick={() => void save({ exclude_paths: paths, exclude_ips: ips }, "Исключения сохранены")}>Сохранить исключения</button></div>
      </div>
    </>
  );
}
