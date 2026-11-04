import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type ApiError, type CdnCalc, type CdnNode, type CdnNodeIn, type CdnOverview } from "../../api";
import { Modal } from "../../components/Dialogs";
import InfoTip from "../../components/InfoTip";
import { bytes, clock } from "../../util";
import { WIKI } from "./ServerComponents";

/** «Трансляции → Узлы CDN»: свои серверы раздачи эфиров. Подключение по SSH (или скриптом), состояние, проверка, обновление, удаление, расчёт оборудования. */
const MODE: Record<CdnOverview["mode"], [string, string]> = {
  origin: ["Прямая раздача с этого сервера", ""], external: ["Внешний CDN", "ok"], nodes: ["Свои узлы CDN", "ok"], fallback: ["Узлы недоступны — раздаёт этот сервер", "warn"],
};
const STATE: Record<CdnNode["state"], string> = { new: "новый", checked: "проверен, не установлен", installing: "устанавливается", ready: "работает", error: "ошибка", removing: "удаляется" };
const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const mbit = (v: number | null | undefined) => (v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(2)} Гбит/с` : `${Math.round(v)} Мбит/с`);
const err = (e: unknown) => (e as ApiError).message || "Ошибка";

function health(n: CdnNode): [string, string] {
  if (!n.enabled) return ["выключен", ""];
  if (n.state !== "ready") return [STATE[n.state], n.state === "error" ? "err" : n.state === "installing" ? "warn" : ""];
  return n.health === "up" ? ["работает", "ok"] : n.health === "down" ? ["не отвечает", "err"] : ["проверяется", "warn"];
}

export default function CdnNodesAdmin() {
  const [ov, setOv] = useState<CdnOverview | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [origin, setOrigin] = useState<string | null>(null);
  const load = useCallback(() => api.cdn.overview().then((o) => { setOv(o); setLoadErr(""); }).catch((e) => setLoadErr(err(e))), []);
  const installing = ov?.nodes.some((n) => n.state === "installing" || n.busy);
  useEffect(() => { void load(); const t = window.setInterval(() => void load(), installing ? 2500 : 15000); return () => window.clearInterval(t); }, [load, installing]);

  const save = async (patch: Parameters<typeof api.cdn.settings>[0]) => {
    setMsg(null);
    try {
      const r = await api.cdn.settings(patch);
      const bad = r.applied ? Object.values(r.applied).filter((v) => v !== "ok") : [];
      setMsg({ ok: !bad.length, text: bad.length ? `Сохранено, но не все узлы приняли настройки: ${bad.join("; ")}` : "Сохранено." });
      setOrigin(null);
      await load();
    } catch (e) { setMsg({ ok: false, text: err(e) }); }
  };
  const rotate = async () => {
    if (!window.confirm("Сменить ключ подписи ссылок узлов? Узлы получат новый ключ, ссылки, выданные до прошлой смены, перестанут открываться.")) return;
    try { const r = await api.cdn.rotate(); setMsg({ ok: r.ok, text: r.ok ? `Ключ сменён (${r.kid}).` : `Ключ сменён, но не все узлы приняли его: ${Object.entries(r.applied).filter(([, v]) => v !== "ok").map(([k, v]) => `${k.slice(0, 8)}: ${v}`).join("; ")}` }); await load(); }
    catch (e) { setMsg({ ok: false, text: err(e) }); }
  };

  if (!ov) return <div className="card" style={{ marginBottom: 12 }}><b>Узлы CDN</b>{loadErr ? <div className="alert warn small" role="alert">{loadErr}</div> : <p className="muted small">Загрузка…</p>}</div>;
  const s = ov.settings;
  const [modeText, modeCls] = MODE[ov.mode];
  return (
    <div className="card cdn-admin" style={{ marginBottom: 12 }}>
      <div className="row"><b>Узлы CDN</b>
        <InfoTip doc={`${WIKI}Узлы-CDN`}>Свои серверы раздачи: основной сервер формирует эфир и выдаёт зрителю подписанную ссылку на узел, узел сам проверяет подпись, забирает сегменты с этого сервера один раз и раздаёт их всем зрителям из своего кеша. Внешние площадки (YouTube, RUTUBE) работают как раньше.</InfoTip>
        <span className={`badge ${modeCls}`}>{modeText}</span><div className="spacer" />
        <button className="btn mini primary" onClick={() => setAdding(true)}>Подключить сервер</button>
      </div>
      <div className="cdn-opts">
        <label className="check"><input type="checkbox" checked={s.cdn_nodes_enabled} onChange={(e) => void save({ cdn_nodes_enabled: e.target.checked })} />
          <span>Раздавать эфиры через свои узлы <span className="muted small">(в комнате — «Раздавать через CDN»)</span></span></label>
        <label className="check"><input type="checkbox" checked={s.cdn_fallback_origin} onChange={(e) => void save({ cdn_fallback_origin: e.target.checked })} />
          <span>Если все узлы недоступны — раздавать с этого сервера</span></label>
        <div className="cdn-origin">
          <label>Адрес источника для узлов
            <input value={origin ?? s.cdn_origin_url} placeholder={ov.origin_effective || "https://meet.example.org"} onChange={(e) => setOrigin(e.target.value)} spellCheck={false} /></label>
          <button className="btn mini" disabled={origin === null} onClick={() => void save({ cdn_origin_url: origin ?? "" })}>Сохранить</button>
          <span className="help">Узлы забирают сегменты с этого адреса (путь /cdn-origin/). Пусто — публичный адрес сервера ({ov.origin_effective || "не задан"}).</span>
        </div>
      </div>
      {msg && <div className={`alert ${msg.ok ? "ok" : "warn"} small`} role="status">{msg.text}</div>}
      {loadErr && <div className="alert warn small" role="alert">{loadErr}</div>}
      {ov.nodes.length === 0 ? <p className="muted small">Узлов пока нет. «Подключить сервер» — Ubuntu 22.04/24.04/26.04 или Debian 12/13, доступ по SSH с правами sudo.</p> : (
        <div className="table-wrap"><table className="table small cdn-nodes">
          <thead><tr><th>Узел</th><th>Состояние</th><th title="Адресов зрителей за последние 40 с">Зрители</th><th>CPU</th><th>RAM</th><th>Отдаёт</th><th title="С этого сервера на узел">С источника</th><th>Кеш</th><th>Запросов/с</th><th>Из кеша</th><th>Проверен</th></tr></thead>
          <tbody>{ov.nodes.map((n) => {
            const [h, cls] = health(n);
            const st = n.stats || {};
            const mem = st.mem_total && st.mem_avail != null ? 1 - st.mem_avail / st.mem_total : null;
            return (
              <tr key={n.id} className={open === n.id ? "sel" : ""}>
                <td><button className="linkish" onClick={() => setOpen(open === n.id ? null : n.id)} aria-expanded={open === n.id}><b>{n.name}</b></button><br /><span className="muted">{n.public_name}{n.region ? ` · ${n.region}` : ""}</span></td>
                <td><span className={`badge ${cls}`}>{h}</span>{n.outdated && <span className="badge warn" title={`Пакет узла ${n.version}, доступен ${ov.node_version}`}>обновить</span>}</td>
                <td>{st.viewers ?? "—"}{n.capacity ? <span className="muted"> / ~{n.capacity}</span> : null}</td>
                <td>{st.cpu_pct == null ? "—" : `${Math.round(st.cpu_pct)}%`}</td>
                <td>{pct(mem)}</td>
                <td>{mbit(st.out_mbit)}</td>
                <td>{mbit(st.origin_mbit)}</td>
                <td>{st.disk ? `${bytes(st.disk.cache_bytes)} / ${st.disk.cache_max_gb} ГБ` : "—"}</td>
                <td>{st.req_rate ?? "—"}</td>
                <td>{pct(st.hit_ratio_now ?? st.hit_ratio)}</td>
                <td>{n.last_check_at ? clock(n.last_check_at) : "—"}</td>
              </tr>);
          })}</tbody>
        </table></div>)}
      {open && ov.nodes.find((n) => n.id === open) && <NodePanel node={ov.nodes.find((n) => n.id === open)!} latest={ov.node_version} onChanged={load} onRemoved={() => { setOpen(null); void load(); }} />}
      <details className="cdn-more"><summary className="small">Расчёт оборудования и ключ подписи</summary>
        <Calc profiles={ov.profiles} />
        <p className="small">Ключ подписи ссылок узлов: {ov.keys.join(", ")} (поколение {s.cdn_key_gen}). <button className="btn mini" onClick={() => void rotate()}>Сменить ключ</button>
          <InfoTip>Узлы принимают ссылки, подписанные текущим и предыдущим ключом. Двойная смена полностью отзывает старый ключ — например, если узел был скомпрометирован.</InfoTip></p>
      </details>
      {adding && <AddNode onClose={() => setAdding(false)} onDone={(id) => { setAdding(false); setOpen(id); void load(); }} />}
    </div>
  );
}

function NodePanel({ node, latest, onChanged, onRemoved }: { node: CdnNode; latest: string; onChanged: () => void; onRemoved: () => void }) {
  const [busy, setBusy] = useState("");
  const [out, setOut] = useState<{ ok: boolean; text: string } | null>(null);
  const [log, setLog] = useState<string | null>(null);
  const [pw, setPw] = useState("");
  const act = async (name: string, fn: () => Promise<string | void>) => {
    setBusy(name); setOut(null);
    try { const t = await fn(); if (t) setOut({ ok: true, text: t }); } catch (e) { setOut({ ok: false, text: err(e) }); } finally { setBusy(""); onChanged(); }
  };
  const f = node.facts as Record<string, unknown> | null;
  const a = node.assessment;
  const manual = node.ssh.user === "pgcdn";
  const failed = node.stages.find((x) => x.status === "fail");
  return (
    <div className="cdn-panel" aria-label={`Узел ${node.name}`}>
      <div className="row"><b>{node.name}</b><span className="muted small">{node.public_name} · SSH {node.ssh.user}@{node.ssh.host}:{node.ssh.port} · {node.ssh.fingerprint} · TLS: {node.tls_mode === "acme" ? "Let's Encrypt" : node.tls_mode === "files" ? "свой сертификат" : "самоподписанный (только испытания)"}{node.version ? ` · пакет ${node.version}` : ""}</span></div>
      {node.last_error && <div className="alert warn small" role="alert">{node.last_error}{failed && <> <Hint stage={failed.name} /></>}</div>}
      {node.stages.length > 0 && (
        <ol className="cdn-stages">{node.stages.map((x) => <li key={x.name} className={x.status}><span className="st">{x.status === "ok" ? "✓" : x.status === "fail" ? "✕" : "…"}</span> {x.title}{x.msg ? <span className="muted"> — {x.msg}</span> : null}</li>)}</ol>)}
      {f && <p className="small muted">{String(f.os_name)} · {String(f.arch)} · {String(f.cpus)} vCPU · {bytes(Number(f.mem_bytes))} ОЗУ · свободно для кеша {bytes(Number(f.cache_fs_avail))}{a?.link_mbit ? ` · сеть ${a.link_mbit} Мбит/с` : ""}</p>}
      {a && <p className="small">Оценка: около <b>{a.viewers}</b> зрителей при {Math.round(a.video_kbps / 1000)} Мбит/с (процессор — {a.by_cpu}, память — {a.by_ram}{a.by_link != null ? `, сеть — ${a.by_link}` : ""}).
        {a.notes.map((n, i) => <span key={i} className="muted"> {n.text}</span>)}{a.blockers.map((b, i) => <span key={`b${i}`} className="err"> {b}</span>)}</p>}
      {node.stats?.per_stream && Object.keys(node.stats.per_stream).length > 0 && <p className="small">По эфирам: {Object.entries(node.stats.per_stream).map(([k, v]) => `${k.slice(0, 6)}… — ${v}`).join(", ")}</p>}
      {(node.state === "checked" || (node.state === "error" && !node.installed_at)) && !manual && (
        <div className="row"><label className="grow">Пароль SSH (если прошло больше 30 минут после проверки)<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="off" /></label>
          <button className="btn primary" disabled={!!busy || !!a?.blockers.length} onClick={() => void act("install", async () => { await api.cdn.install(node.id, pw ? { password: pw } : {}); setPw(""); return "Установка запущена: этапы появятся выше."; })}>{node.state === "error" ? "Повторить установку" : "Установить"}</button></div>)}
      {manual && node.state !== "ready" && (
        <div className="small cdn-manual"><b>Установка скриптом на сервере</b>
          <ol><li><a href="/api/v1/admin/cdn/package.tar.gz">Скачайте пакет узла</a> и <a href={`/api/v1/admin/cdn/nodes/${node.id}/node.json`}>настройки этого узла</a> (секрет — передавайте защищённо).</li>
            <li>На сервере: <code>mkdir pgcdn &amp;&amp; tar -xzf peregovorka-cdn-node-*.tar.gz -C pgcdn &amp;&amp; sudo bash pgcdn/install.sh --config pgcdn-{node.public_name}.json</code></li>
            <li>Нажмите «Подключить установленный».</li></ol>
          <button className="btn primary" disabled={!!busy} onClick={() => void act("adopt", async () => { await api.cdn.adopt(node.id); return "Узел подключён."; })}>{busy === "adopt" ? "Проверка…" : "Подключить установленный"}</button></div>)}
      <div className="row cdn-actions">
        {node.state === "ready" && <button className="btn mini" disabled={!!busy} onClick={() => void act("test", async () => { const r = await api.cdn.test(node.id); if (!r.ok) throw new Error(r.message); return r.message; })}>{busy === "test" ? "Проверка…" : "Проверить раздачу"}</button>}
        {node.state === "ready" && <button className="btn mini" disabled={!!busy} onClick={() => void act("poll", async () => { await api.cdn.poll(node.id); })}>Обновить показатели</button>}
        {node.state === "ready" && <button className="btn mini" disabled={!!busy} onClick={() => void act("apply", async () => { const r = await api.cdn.apply(node.id); if (!r.ok) throw new Error(r.message); return r.message; })}>Применить настройки</button>}
        {node.state === "ready" && node.version !== latest && <button className="btn mini" disabled={!!busy} onClick={() => void act("upgrade", async () => { await api.cdn.upgrade(node.id); return "Обновление запущено."; })}>Обновить узел до {latest}</button>}
        <button className="btn mini" disabled={!!busy} onClick={() => void act("enable", async () => { await api.cdn.patch(node.id, { enabled: !node.enabled }); })}>{node.enabled ? "Не направлять зрителей" : "Направлять зрителей"}</button>
        <button className="btn mini" disabled={!!busy} onClick={() => void api.cdn.logs(node.id).then((l) => setLog([l.error ? `Журнал узла недоступен: ${l.error}` : "", l.text, l.install ? `\n--- журнал установки ---\n${l.install}` : ""].join("\n").trim() || "Журнал пуст.")).catch((e) => setLog(err(e)))}>Журнал</button>
        <div className="spacer" />
        <button className="btn mini danger" disabled={!!busy} onClick={() => void act("remove", async () => {
          if (!window.confirm(`Удалить узел ${node.public_name}? Зрители перестанут на него направляться, служба на сервере будет удалена.`)) return;
          try { await api.cdn.remove(node.id); onRemoved(); }
          catch (e) { if ((e as ApiError).code === "uninstall_failed" && window.confirm(`${err(e)}\n\nЗабыть узел?`)) { await api.cdn.remove(node.id, true); onRemoved(); } else throw e; }
        })}>Удалить</button>
      </div>
      {out && <div className={`alert ${out.ok ? "ok" : "warn"} small`} role="status">{out.text}</div>}
      {log !== null && <pre className="cdn-log" tabIndex={0}>{log}</pre>}
    </div>
  );
}

const HINTS: Record<string, string> = {
  connect: "Проверьте адрес, порт SSH, логин и пароль; ключ сервера должен совпадать с подтверждённым.",
  check: "Нужны Ubuntu 22.04/24.04/26.04 или Debian 12/13 (amd64/arm64), systemd и права sudo.",
  packages: "Сервер должен иметь доступ к nginx.org и зеркалам пакетов по HTTPS; прежний nginx из пакетов системы нужно удалить.",
  tls: "Свой сертификат: PEM сертификата (с цепочкой) и ключ без пароля.",
  acme: "Let's Encrypt: имя узла должно указывать на этот сервер в DNS, порт 80 — открыт из интернета.",
  start: "Порты 80 и 443 на сервере должны быть свободны.",
  selfcheck: "Узел не может забрать данные с источника: проверьте «Адрес источника для узлов» и что сервер узла видит его по сети.",
  mgmt: "Вход ключом pgcdn не работает: проверьте, что sshd разрешает вход по ключу (PubkeyAuthentication yes).",
  test: "Основной сервер не может открыть узел по его публичному имени: DNS, порт 443 и сертификат.",
};
const Hint = ({ stage }: { stage: string }) => (HINTS[stage] ? <span className="muted">{HINTS[stage]}</span> : null);

function AddNode({ onClose, onDone }: { onClose: () => void; onDone: (id: string) => void }) {
  const [f, setF] = useState({ name: "", public_name: "", region: "", ssh_host: "", ssh_port: 22, ssh_user: "root", password: "", private_key: "", tls_mode: "acme" as CdnNodeIn["tls_mode"],
    acme_email: "", tls_cert_pem: "", tls_key_pem: "", quality: "1080p" as CdnNodeIn["quality"], manual: false, auth: "password" as "password" | "key" });
  const [hk, setHk] = useState<{ host_key: string; fingerprint: string; type: string } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [node, setNode] = useState<CdnNode | null>(null);
  const [busy, setBusy] = useState(false);
  const [e, setE] = useState("");
  const set = (k: keyof typeof f) => (ev: { target: { value: string } }) => setF({ ...f, [k]: k === "ssh_port" ? Number(ev.target.value) || 22 : ev.target.value });
  const getKey = async () => {
    setBusy(true); setE(""); setHk(null); setConfirmed(false);
    try { setHk(await api.cdn.hostKey(f.ssh_host.trim(), f.ssh_port)); } catch (x) { setE(err(x)); } finally { setBusy(false); }
  };
  const check = async () => {
    if (!hk) return;
    setBusy(true); setE("");
    try {
      const body: CdnNodeIn = { name: f.name.trim() || f.public_name.trim(), public_name: f.public_name.trim(), region: f.region.trim() || undefined, ssh_host: f.ssh_host.trim(), ssh_port: f.ssh_port,
        ssh_user: f.ssh_user.trim(), host_key: hk.host_key, fingerprint: hk.fingerprint, tls_mode: f.tls_mode, acme_email: f.acme_email.trim() || undefined, quality: f.quality, manual: f.manual,
        ...(f.manual ? {} : f.auth === "password" ? { password: f.password } : { private_key: f.private_key }),
        ...(f.tls_mode === "files" ? { tls_cert_pem: f.tls_cert_pem, tls_key_pem: f.tls_key_pem } : {}) };
      const n = await api.cdn.add(body);
      setF((x) => ({ ...x, password: "", private_key: "" }));
      if (f.manual) onDone(n.id); else setNode(n);
    } catch (x) { setE(err(x)); } finally { setBusy(false); }
  };
  const install = async () => {
    if (!node) return;
    setBusy(true); setE("");
    try { await api.cdn.install(node.id, {}); onDone(node.id); } catch (x) { setE(err(x)); setBusy(false); }
  };
  const ready = !!hk && confirmed && !!f.public_name.trim() && (f.manual || (f.auth === "password" ? !!f.password : !!f.private_key.trim())) && (f.tls_mode !== "files" || (!!f.tls_cert_pem && !!f.tls_key_pem));
  const a = node?.assessment;
  return (
    <Modal title="Подключить сервер CDN" onClose={onClose} wide>
      {!node ? (
        <div className="stack cdn-add">
          <div className="cols">
            <label>Адрес сервера (SSH)<input value={f.ssh_host} onChange={set("ssh_host")} placeholder="203.0.113.10 или cdn1.example.org" autoComplete="off" spellCheck={false} /></label>
            <label>Порт<input type="number" min={1} max={65535} value={f.ssh_port} onChange={set("ssh_port")} /></label>
          </div>
          <div className="row"><button className="btn" disabled={busy || !f.ssh_host.trim()} onClick={() => void getKey()}>{busy && !hk ? "Подключение…" : "Получить отпечаток сервера"}</button>
            <span className="help">Шаг 1: ключ SSH сервера. Сверьте отпечаток с выводом на сервере: <code>ssh-keygen -lf /etc/ssh/ssh_host_{hk?.type === "ssh-rsa" ? "rsa" : hk?.type?.startsWith("ecdsa") ? "ecdsa" : "ed25519"}_key.pub</code></span></div>
          {hk && <label className="check cdn-fp"><input type="checkbox" checked={confirmed} onChange={(ev) => setConfirmed(ev.target.checked)} /><span>Отпечаток совпадает: <code>{hk.fingerprint}</code> ({hk.type})</span></label>}
          {hk && confirmed && (<>
            <div className="cols">
              <label>Публичное имя узла<input value={f.public_name} onChange={set("public_name")} placeholder="cdn1.example.org" spellCheck={false} /><span className="help">Зрители открывают https://это-имя; оно должно указывать на сервер в DNS.</span></label>
              <label>Название в списке<input value={f.name} onChange={set("name")} placeholder="Москва-1" /></label>
              <label>Регион <span className="muted small">(необязательно)</span><input value={f.region} onChange={set("region")} /></label>
            </div>
            <label className="check"><input type="checkbox" checked={f.manual} onChange={(ev) => setF({ ...f, manual: ev.target.checked })} /><span>Установлю сам скриптом на сервере (пароль администратора сервера не нужен)</span></label>
            {!f.manual && (<>
              <div className="cols">
                <label>Пользователь<input value={f.ssh_user} onChange={set("ssh_user")} autoComplete="off" spellCheck={false} /></label>
                <label>Вход<select value={f.auth} onChange={(ev) => setF({ ...f, auth: ev.target.value as "password" | "key" })}><option value="password">пароль</option><option value="key">закрытый ключ</option></select></label>
                {f.auth === "password" && <label>Пароль<input type="password" value={f.password} onChange={set("password")} autoComplete="new-password" /></label>}
              </div>
              {f.auth === "key" && <label>Закрытый ключ (OpenSSH/PEM, без пароля)<textarea rows={4} value={f.private_key} onChange={set("private_key")} spellCheck={false} /></label>}
              <span className="help">Пароль или ключ нужен только для установки: хранится зашифрованным не дольше 30 минут и удаляется после неё. Дальше узлом управляет отдельная учётная запись pgcdn с ключом этого сервера и только командой pgcdn-ctl.</span>
            </>)}
            <div className="cols">
              <label>Сертификат TLS<select value={f.tls_mode} onChange={(ev) => setF({ ...f, tls_mode: ev.target.value as CdnNodeIn["tls_mode"] })}>
                <option value="acme">Let's Encrypt (автоматически)</option><option value="files">Свой сертификат</option><option value="selfsigned">Самоподписанный — только для испытаний</option></select></label>
              {f.tls_mode === "acme" && <label>Почта для Let's Encrypt <span className="muted small">(необязательно)</span><input value={f.acme_email} onChange={set("acme_email")} type="email" /></label>}
              <label>Качество эфиров (для оценки)<select value={f.quality} onChange={(ev) => setF({ ...f, quality: ev.target.value as CdnNodeIn["quality"] })}><option value="720p">720p</option><option value="1080p">1080p</option><option value="1440p">1440p</option></select></label>
            </div>
            {f.tls_mode === "files" && <div className="cols"><label>Сертификат (PEM, с цепочкой)<textarea rows={4} value={f.tls_cert_pem} onChange={set("tls_cert_pem")} spellCheck={false} /></label>
              <label>Закрытый ключ (PEM)<textarea rows={4} value={f.tls_key_pem} onChange={set("tls_key_pem")} spellCheck={false} /></label></div>}
            {f.tls_mode === "selfsigned" && <div className="alert warn small">Браузеры зрителей не примут самоподписанный сертификат: этот режим — для испытательного стенда.</div>}
            <div className="row"><button className="btn primary" disabled={busy || !ready} onClick={() => void check()}>{busy ? "Проверка сервера…" : f.manual ? "Создать узел" : "Проверить сервер"}</button>
              <span className="help">{f.manual ? "Дальше — скачать пакет и настройки узла." : "Шаг 2: вход, сведения о системе, права, диски и сеть. Ничего не устанавливается."}</span></div>
          </>)}
        </div>
      ) : (
        <div className="stack">
          <p className="small">{String(node.facts?.os_name)} · {String(node.facts?.arch)} · {String(node.facts?.cpus)} vCPU · {bytes(Number(node.facts?.mem_bytes))} ОЗУ · свободно для кеша {bytes(Number(node.facts?.cache_fs_avail))} · права администратора: {node.facts?.sudo ? "есть" : "нет"}</p>
          {a && (a.ok
            ? <div className="alert ok small">Сервер подходит: около <b>{a.viewers}</b> зрителей при {Math.round(a.video_kbps / 1000)} Мбит/с (ограничивает {a.viewers === a.by_cpu ? "процессор" : a.viewers === a.by_ram ? "память" : "сеть"}).</div>
            : <div className="alert warn small" role="alert">{a.blockers.join(" ")}</div>)}
          {a?.notes.map((n, i) => <div key={i} className={`alert ${n.level === "warn" ? "warn" : "info"} small`}>{n.text}</div>)}
          <div className="row"><button className="btn primary" disabled={busy || !a?.ok} onClick={() => void install()}>{busy ? "Запуск…" : "Установить"}</button>
            <button className="btn ghost" onClick={() => onDone(node.id)}>Позже</button>
            <span className="help">Шаг 3: nginx и njs из nginx.org, учётная запись pgcdn, сертификат, запуск, проверка раздачи тестового эфира. Повторный запуск безопасен.</span></div>
        </div>)}
      {e && <div className="alert error" role="alert">{e}</div>}
    </Modal>
  );
}

function Calc({ profiles }: { profiles: CdnCalc[] }) {
  const [viewers, setViewers] = useState(500);
  const [quality, setQuality] = useState("1080p");
  const [streams, setStreams] = useState(1);
  const [link, setLink] = useState(0);
  const [r, setR] = useState<CdnCalc | null>(null);
  useEffect(() => {
    const t = window.setTimeout(() => void api.cdn.calc(Math.max(1, viewers), quality, Math.max(1, streams), link || undefined).then(setR).catch(() => setR(null)), 300);
    return () => window.clearTimeout(t);
  }, [viewers, quality, streams, link]);
  const rows = useMemo(() => profiles, [profiles]);
  return (
    <div className="cdn-calc">
      <div className="cols">
        <label>Зрителей одновременно<input type="number" min={1} value={viewers} onChange={(e) => setViewers(Number(e.target.value))} /></label>
        <label>Качество<select value={quality} onChange={(e) => setQuality(e.target.value)}><option>720p</option><option>1080p</option><option>1440p</option></select></label>
        <label>Эфиров одновременно<input type="number" min={1} max={50} value={streams} onChange={(e) => setStreams(Number(e.target.value))} /></label>
        <label>Канал сервера, Мбит/с <span className="muted small">(если известен)</span><input type="number" min={0} value={link || ""} onChange={(e) => setLink(Number(e.target.value))} /></label>
      </div>
      {r && <p className="small">Нужно: исходящий канал <b>{r.egress_gbps} Гбит/с</b> (с запасом 30%), <b>{r.cpu_cores} vCPU</b>, <b>{r.ram_gb} ГБ</b> ОЗУ, <b>{r.disk_gb} ГБ</b> NVMe/SSD под кеш. Трафик — около {r.traffic_tb_per_hour} ТБ за час эфира.
        {r.link_viewers != null && <> Канал {link} Мбит/с выдержит около <b>{r.link_viewers}</b> зрителей.</>}</p>}
      <table className="table small"><thead><tr><th>Профиль</th><th>Зрителей</th><th>Качество</th><th>Канал</th><th>vCPU</th><th>ОЗУ</th><th>Диск</th></tr></thead>
        <tbody>{rows.map((p, i) => <tr key={i}><td>{p.name}</td><td>{p.viewers}</td><td>{Math.round(p.video_kbps / 1000)} Мбит/с</td><td>{p.egress_gbps} Гбит/с</td><td>{p.cpu_cores}</td><td>{p.ram_gb} ГБ</td><td>{p.disk_gb} ГБ</td></tr>)}</tbody></table>
      <p className="muted small">Расчёт — по замерам на испытательном стенде (см. вики «Узлы CDN»); предел узла чаще всего — канал провайдера, а не процессор.</p>
    </div>
  );
}
