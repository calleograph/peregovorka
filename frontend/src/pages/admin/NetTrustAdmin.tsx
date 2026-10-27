import { useCallback, useEffect, useState } from "react";
import { api, type ApiError, type IpDiagnostics } from "../../api";
import SettingsForm from "./SettingsForm";
import { netTrustFields } from "./fields";

const SRC: Record<string, string> = { tcp: "адрес TCP-соединения", "x-pg-peer": "адрес, с которого подключились к web", "x-forwarded-for": "цепочка X-Forwarded-For", "x-real-ip": "заголовок X-Real-IP", forwarded: "заголовок Forwarded", header: "заголовок WAF/CDN" };

/** «Сеть и IP-адреса»: как сервер определяет адрес клиента. Рядом с настройками — диагностика ЭТОГО запроса: что видит backend и почему принят именно этот адрес. */
export default function NetTrustAdmin() {
  const [d, setD] = useState<IpDiagnostics | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => api.admin.ipDiagnostics().then((x) => { setD(x); setErr(""); }).catch((e) => setErr((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);
  return (
    <>
      <div className="card">
        <div className="row"><h2>Что сервер видит сейчас</h2><div className="spacer" /><button className="btn" onClick={() => void load()}>Обновить</button></div>
        {err && <div className="alert error" role="alert">{err}</div>}
        {d && (
          <>
            <p style={{ margin: "4px 0" }}>Ваш адрес для сервера: <strong>{d.client_ip}</strong> <span className="muted small">({SRC[d.source] ?? d.source}{d.headers_accepted ? "" : "; заголовки адреса не принимались"})</span></p>
            <p className="muted small" style={{ margin: "2px 0 8px" }}>Ограничения по адресам и счётчики попыток считаются именно по этому адресу{d.counter_key !== d.client_ip ? ` (для IPv6 — по сети ${d.counter_key})` : ""}.</p>
            {d.warnings.map((w) => <div key={w} className="alert info small" role="status">⚠ {w}</div>)}
            <table className="table small"><tbody>
              <tr><th>Адрес соединения с backend</th><td><code>{d.tcp_peer ?? "—"}</code></td></tr>
              <tr><th>Кто подключился к web</th><td><code>{d.web_peer ?? "—"}</code></td></tr>
              <tr><th>X-Forwarded-For</th><td><code>{d.headers.x_forwarded_for || "—"}</code></td></tr>
              <tr><th>X-Real-IP</th><td><code>{d.headers.x_real_ip || "—"}</code></td></tr>
              <tr><th>Forwarded</th><td><code>{d.headers.forwarded || "—"}</code></td></tr>
              <tr><th>Доверенные прокси сейчас</th><td><code>{d.config.trusted_proxies.join(", ") || "—"}</code></td></tr>
            </tbody></table>
            {d.notes.map((n) => <p key={n} className="muted small" style={{ margin: "4px 0" }}>• {n}</p>)}
          </>
        )}
        <details className="tech-details"><summary>Как это работает</summary>
          <p className="small">Заголовок с адресом клиента (X-Forwarded-For и другие) может написать кто угодно, поэтому он принимается только от узлов, которых вы объявили доверенными. Сначала backend проверяет, что соединение пришло из сети контейнеров;
            затем — что к web подключился доверенный прокси (ваш WAF, балансировщик, nginx хоста). В цепочке адреса идут справа налево: доверенные прокси пропускаются, первый оставшийся адрес — клиент.</p>
          <p className="small"><strong>Доверенные прокси</strong> — это узлы, которым разрешено передавать настоящий адрес клиента. <strong>Разрешённые клиентские сети</strong> (ограничение доступа по подсетям) — отдельная настройка; смешивать их нельзя.</p>
          <p className="small">Если за вашим сервером стоит WAF или CDN с публичными адресами, добавьте эти адреса в доверенные прокси. Иначе все сотрудники будут выглядеть как один адрес, и ограничения по IP станут общими.</p>
        </details>
      </div>
      <SettingsForm key="net_trust" group="net_trust" title="Определение адреса клиента" fields={netTrustFields} onSaved={() => void load()}
        intro="Меняется на лету: новый режим действует в течение нескольких секунд на всех экземплярах сервера. Неверная настройка не оставит вас без доступа к этому разделу, но может сделать ограничения по IP общими для всех — проверяйте блок выше после сохранения." />
    </>
  );
}
