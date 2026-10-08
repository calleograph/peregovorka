import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type DiagResult, type SipProfile, type SipStatus } from "../../api";
import { normalizeNumber } from "../../phone";
import { SecretInput, StageList, fmtDate, secretToSend } from "./common";

interface Form {
  id?: string; name: string; enabled: boolean; is_default: boolean; direction: SipProfile["direction"]; host: string; port: number; transport: SipProfile["transport"];
  username: string; secret: string; secret_set: boolean; realm: string; caller_id: string; allowed_numbers: string; inbound_numbers: string; allowed_addresses: string;
  codecs: string[]; media_encryption: SipProfile["media_encryption"]; ring_timeout_s: number;
}
const CODECS = ["PCMU", "PCMA", "G722", "G729", "OPUS"];
const empty: Form = { name: "", enabled: true, is_default: false, direction: "both", host: "", port: 5060, transport: "udp", username: "", secret: "", secret_set: false, realm: "",
  caller_id: "", allowed_numbers: "", inbound_numbers: "", allowed_addresses: "", codecs: [], media_encryption: "disable", ring_timeout_s: 45 };
const toForm = (p: SipProfile): Form => ({ ...p, secret: "", secret_set: p.secret_set, allowed_numbers: p.allowed_numbers.join(", "), inbound_numbers: p.inbound_numbers.join(", "),
  allowed_addresses: p.allowed_addresses.join(", ") });
const DIR: Record<SipProfile["direction"], string> = { outbound: "исходящий", inbound: "входящий", both: "двусторонний" };

const Dot = ({ ok }: { ok: boolean | null | undefined }) => <span className={`dot ${ok === true ? "ok" : ok === false ? "bad" : ""}`} aria-hidden />;

/**
 * Интеграции → SIP-телефония. Архитектура: АТС (Asterisk/PJSIP или другая) ↔ SIP-транк ↔ LiveKit SIP Service ↔ комната Peregovorka. Профиль — это транк LiveKit; при
 * сохранении он синхронизируется. Asterisk — первый проверенный вариант, но не единственный: интерфейс и модель от АТС не зависят.
 */
export default function SipAdmin() {
  const [st, setSt] = useState<SipStatus | null>(null);
  const [items, setItems] = useState<SipProfile[]>([]);
  const [form, setForm] = useState<Form | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const [diag, setDiag] = useState<Record<string, DiagResult | "run">>({});
  const [callNum, setCallNum] = useState<Record<string, string>>({});
  const [callRes, setCallRes] = useState<Record<string, { ok: boolean; text: string } | "run">>({});
  const [srvBusy, setSrvBusy] = useState(false);

  const load = useCallback(async () => {
    try { const [s, p] = await Promise.all([api.admin.sipStatus(), api.admin.sipProfiles()]); setSt(s); setItems(p.items); } catch (e) { setError((e as ApiError).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));
  const list = (v: string) => v.split(/[\s,;]+/).filter(Boolean);

  const body = (f: Form) => ({
    name: f.name.trim(), enabled: f.enabled, is_default: f.is_default, direction: f.direction, host: f.host.trim(), port: f.port, transport: f.transport, username: f.username.trim(),
    realm: f.realm.trim(), caller_id: f.caller_id.trim(), allowed_numbers: list(f.allowed_numbers), inbound_numbers: list(f.inbound_numbers), allowed_addresses: list(f.allowed_addresses),
    codecs: f.codecs, media_encryption: f.media_encryption, ring_timeout_s: f.ring_timeout_s,
    ...(secretToSend(f.secret) !== undefined ? { secret: secretToSend(f.secret) } : {}),
  });

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setError(""); setNote(""); setBusy("save");
    try {
      const r = form.id ? await api.admin.updateSip(form.id, body(form)) : await api.admin.createSip(body(form));
      setForm(null);
      setNote(`Сохранено. ${r.sync?.message ?? ""}`.trim());
      await load();
    } catch (err) { setError((err as ApiError).message); }
    setBusy("");
  };
  const remove = async (p: SipProfile) => {
    if (!window.confirm(`Удалить SIP-профиль «${p.name}»? Транки в LiveKit будут удалены.`)) return;
    setError(""); setNote("");
    try { await api.admin.deleteSip(p.id); await load(); } catch (e) { setError((e as ApiError).message); }
  };
  const check = async (p: SipProfile) => {
    setDiag((d) => ({ ...d, [p.id]: "run" }));
    try { const r = await api.admin.checkSip(p.id); setDiag((d) => ({ ...d, [p.id]: r })); await load(); }
    catch (e) { setDiag((d) => ({ ...d, [p.id]: { ok: false, stages: [], message: (e as ApiError).message } })); }
  };
  const sync = async (p: SipProfile) => {
    setError(""); setNote(""); setBusy(`sync-${p.id}`);
    try { const r = await api.admin.syncSip(p.id); (r.ok === false ? setError : setNote)(r.message); await load(); } catch (e) { setError((e as ApiError).message); }
    setBusy("");
  };
  const testCall = async (p: SipProfile) => {
    const n = normalizeNumber(callNum[p.id] ?? "");
    if (!n) { setCallRes((r) => ({ ...r, [p.id]: { ok: false, text: "Введите номер: цифры и символы + * #." } })); return; }
    setCallRes((r) => ({ ...r, [p.id]: "run" }));
    try { const r = await api.admin.testCallSip(p.id, n); setCallRes((x) => ({ ...x, [p.id]: { ok: r.ok, text: `${r.message} (${r.ms} мс)` } })); await load(); }
    catch (e) { setCallRes((x) => ({ ...x, [p.id]: { ok: false, text: (e as ApiError).message } })); }
  };
  const server = async (id: "sip_enable" | "sip_disable") => {
    setError(""); setNote(""); setSrvBusy(true);
    try { await api.admin.repairFix(id); setNote(id === "sip_enable" ? "Включение запущено на сервере (перезапуск LiveKit и запуск службы SIP — до минуты; идущие звонки прервутся)." : "Выключение запущено на сервере."); window.setTimeout(() => void load(), 6000); }
    catch (e) { setError((e as ApiError).message); }
    setSrvBusy(false);
  };

  const ports = st?.ports;
  return (
    <section>
      <div className="row"><h2>SIP-телефония</h2><div className="spacer" />
        {!form && <button className="btn primary" onClick={() => { setForm({ ...empty }); setError(""); setNote(""); }}>＋ Добавить SIP-профиль</button>}</div>
      <p className="muted">Подключение телефонной станции (Asterisk или другой SIP-АТС) и звонки в комнаты: АТС ↔ SIP-транк ↔ <b>LiveKit SIP Service</b> ↔ комната. Телефонный абонент появляется среди участников как обычный, его речь попадает в стенограмму и запись.
        Asterisk — первый проверенный вариант; профили не привязаны к конкретной АТС.</p>
      {error && <div className="alert error" role="alert">{error}</div>}
      {note && <div className="alert ok" role="status">{note}</div>}

      <div className="card sip-status" aria-label="Состояние телефонии">
        <div className="row"><h3 style={{ margin: 0 }}>Состояние</h3><div className="spacer" />
          <button className="btn mini" onClick={() => void load()}>Обновить</button>
          {st && !st.enabled_on_server && <button className="btn mini primary" disabled={srvBusy} onClick={() => void server("sip_enable")} title="Запустить службу LiveKit SIP и открыть порты на сервере (выполняет помощник обновлений)">Включить телефонию на сервере</button>}
          {st?.enabled_on_server && <button className="btn mini" disabled={srvBusy} onClick={() => { if (window.confirm("Выключить телефонию на сервере? Служба SIP остановится, порты перестанут публиковаться; профили сохранятся.")) void server("sip_disable"); }}>Выключить</button>}
        </div>
        {!st ? <p className="muted">Загрузка…</p> : (
          <table className="table compact"><tbody>
            <tr><td>Телефония на сервере</td><td><Dot ok={st.enabled_on_server ? true : null} /> {st.enabled_on_server ? "включена" : "выключена — служба не запущена, порты не открыты"}</td></tr>
            {st.enabled_on_server && <>
              <tr><td>Служба LiveKit SIP</td><td><Dot ok={st.service.running} /> {st.service.detail}</td></tr>
              <tr><td>Связь SIP → LiveKit</td><td><Dot ok={st.livekit.ok} /> {st.livekit.detail}</td></tr>
              <tr><td>Redis (общая шина)</td><td><Dot ok={st.redis.ok} /> {st.redis.detail}</td></tr></>}
            <tr><td>Транки</td><td>профилей: {st.trunks.profiles}, включено: {st.trunks.enabled}, синхронизировано с LiveKit: {st.trunks.synced}{st.trunks.missing?.length ? <span className="badge warn"> нет в LiveKit: {st.trunks.missing.join(", ")}</span> : null}</td></tr>
            <tr><td>Профиль по умолчанию</td><td>{st.active_trunk ?? "—"}</td></tr>
            <tr><td>Последняя проверка</td><td>{st.last_check ? <><Dot ok={st.last_check.result.ok} /> {st.last_check.profile}: {st.last_check.result.ok ? "успешно" : "есть проблемы"} · {fmtDate(st.last_check.at)}</> : "ещё не выполнялась"}</td></tr>
            <tr><td>SIP signalling</td><td>порт <b>{ports?.signaling_port}</b>, UDP и TCP</td></tr>
            <tr><td>RTP (голос)</td><td>порты <b>{ports?.rtp_start}–{ports?.rtp_end}</b>, UDP{ports?.media_ip ? <> · объявляется адрес {ports.media_ip}</> : null}</td></tr>
          </tbody></table>
        )}
        <details className="group"><summary>Какие порты нужны и как не открыть лишнего</summary>
          <ul className="help">
            <li><b>Браузерные участники</b>: 443 (HTTPS) и порты LiveKit RTC (<code>LIVEKIT_TCP_PORT</code>, <code>LIVEKIT_UDP_PORT</code>).</li>
            <li><b>Телефония</b> — отдельно: SIP signalling ({ports?.signaling_port ?? 5060}/udp+tcp) и RTP ({ports?.rtp_start ?? 20000}–{ports?.rtp_end ?? 20100}/udp). Эти порты нужны только АТС или SIP-провайдеру.</li>
            <li>Если АТС внутренняя, <b>в Интернет эти порты публиковать не нужно</b>: разрешите их только с адресов АТС{ports?.allowed_cidrs.length ? <> (сейчас заданы: {ports.allowed_cidrs.join(", ")})</> : <> — задайте <code>SIP_ALLOWED_CIDRS</code> в настройках сервера</>}. Правила файрвола показывает и применяет по явной команде <code>sudo scripts/sip.sh firewall [--apply]</code>.</li>
            <li>Диапазон RTP ограничен намеренно: одному звонку нужна пара портов. Нужно больше одновременных звонков — расширьте <code>SIP_RTP_END</code>.</li>
          </ul>
        </details>
      </div>

      {form && (
        <form className="card form" onSubmit={(e) => void save(e)} noValidate>
          <h3 style={{ margin: 0 }}>{form.id ? `Изменение профиля «${form.name}»` : "Новый SIP-профиль"}</h3>
          <div className="cols">
            <label>Название<input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Asterisk, офис" maxLength={120} /></label>
            <label className="check"><input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} /><span className="check-body">Профиль включён</span></label>
            <label className="check"><input type="checkbox" checked={form.is_default} onChange={(e) => set("is_default", e.target.checked)} /><span className="check-body">Профиль по умолчанию<span className="help">Используется комнатами с вариантом «профиль по умолчанию».</span></span></label>
          </div>
          <fieldset className="group"><legend>Подключение к АТС</legend>
            <div className="cols">
              <label>Направление
                <select value={form.direction} onChange={(e) => set("direction", e.target.value as Form["direction"])}>
                  <option value="both">Двусторонний (входящие и исходящие)</option><option value="outbound">Исходящий (комната звонит на телефоны)</option><option value="inbound">Входящий (на телефоны комнат звонят с АТС)</option>
                </select></label>
              <label>Адрес АТС (SIP host)<input value={form.host} onChange={(e) => set("host", e.target.value)} placeholder="pbx.example.local" autoCapitalize="none" spellCheck={false} />
                <span className="help">Имя или IP, без схемы. Для Asterisk — адрес сервера с PJSIP.</span></label>
              <label>Порт<input type="number" min={1} max={65535} value={form.port} onChange={(e) => set("port", Number(e.target.value))} style={{ maxWidth: 140 }} /></label>
              <label>Транспорт
                <select value={form.transport} onChange={(e) => { const v = e.target.value as Form["transport"]; setForm((f) => (f ? { ...f, transport: v, port: v === "tls" && f.port === 5060 ? 5061 : v !== "tls" && f.port === 5061 ? 5060 : f.port } : f)); }}>
                  <option value="udp">UDP</option><option value="tcp">TCP</option><option value="tls">TLS</option></select></label>
            </div>
            <div className="cols">
              <label>Логин<input value={form.username} onChange={(e) => set("username", e.target.value)} autoComplete="off" spellCheck={false} placeholder="необязательно" />
                <span className="help">Для Asterisk — имя в секции auth. Если АТС сопоставляет транк только по IP (<code>identify</code>), логин и пароль не нужны.</span></label>
              <SecretInput label="Пароль" isSet={form.secret_set} value={form.secret} onChange={(v) => set("secret", v)} help="Хранится в зашифрованном виде и после сохранения не показывается." />
            </div>
            <div className="cols">
              <label>Caller ID / номер<input value={form.caller_id} onChange={(e) => set("caller_id", e.target.value)} placeholder="+70001112233" inputMode="tel" />
                <span className="help">С какого номера звонит комната. Многие АТС отклоняют вызов без разрешённого номера.</span></label>
              <label>Realm / домен<input value={form.realm} onChange={(e) => set("realm", e.target.value)} placeholder="pbx.example.local" spellCheck={false} /><span className="help">Для входящей авторизации, если АТС её требует.</span></label>
            </div>
          </fieldset>
          <fieldset className="group"><legend>Номера и адреса</legend>
            <label>Допустимые номера назначения <span className="muted small">(префиксы через запятую; пусто — любые)</span>
              <input value={form.allowed_numbers} onChange={(e) => set("allowed_numbers", e.target.value)} placeholder="+7, 8, 2" spellCheck={false} />
              <span className="help">Куда разрешено звонить из комнат: номер должен начинаться с одного из префиксов. Защита от случайных и дорогих звонков.</span></label>
            <label>Принимаемые входящие номера <span className="muted small">(пусто — любые)</span>
              <input value={form.inbound_numbers} onChange={(e) => set("inbound_numbers", e.target.value)} placeholder="201, 202" spellCheck={false} />
              <span className="help">Внутренние номера, на которые транк принимает звонки.</span></label>
            <label>Разрешённые адреса АТС для входящих <span className="muted small">(IP или подсети через запятую)</span>
              <input value={form.allowed_addresses} onChange={(e) => set("allowed_addresses", e.target.value)} placeholder="192.0.2.10, 192.0.2.0/24" spellCheck={false} />
              <span className="help">Звонки принимаются только с этих адресов — так в Asterisk сопоставляют входящий транк по IP. Для входящих рекомендуется всегда.</span></label>
          </fieldset>
          <details className="group"><summary>Кодеки, шифрование и время ожидания</summary>
            <div className="checks">{CODECS.map((c) => (
              <label key={c} className="check"><input type="checkbox" checked={form.codecs.includes(c)} onChange={(e) => set("codecs", e.target.checked ? [...form.codecs, c] : form.codecs.filter((x) => x !== c))} /> {c}</label>))}</div>
            <span className="help">Ничего не отмечено — кодеки по умолчанию LiveKit. Если отмечено, используются только выбранные (должны быть разрешены и на АТС).</span>
            <div className="cols">
              <label>Шифрование медиа (SRTP)
                <select value={form.media_encryption} onChange={(e) => set("media_encryption", e.target.value as Form["media_encryption"])}>
                  <option value="disable">Отключено</option><option value="allow">Допускается</option><option value="require">Обязательно</option></select></label>
              <label>Ждать ответа абонента<input type="number" min={5} max={120} value={form.ring_timeout_s} onChange={(e) => set("ring_timeout_s", Number(e.target.value))} style={{ maxWidth: 140 }} /><span className="help">секунд</span></label>
            </div>
          </details>
          <div className="row form-actions">
            <button className="btn primary" disabled={busy === "save"}>{busy === "save" ? "Сохранение…" : "Сохранить"}</button>
            <button type="button" className="btn ghost" onClick={() => setForm(null)}>Отмена</button>
          </div>
        </form>
      )}

      {!items.length && !form && <div className="alert info">SIP-профилей пока нет. Добавьте подключение к АТС, затем включите телефонию на сервере (если она ещё выключена), нажмите «Проверить настройки» и «Тестовый вызов».</div>}
      {items.map((p) => {
        const d = diag[p.id];
        const cr = callRes[p.id];
        return (
          <div key={p.id} className="card conn-card">
            <div className="row"><h3 style={{ margin: 0 }}>{p.name}</h3>
              {p.is_default && <span className="badge ok">по умолчанию</span>}
              {!p.enabled && <span className="badge warn">выключен</span>}
              <span className="badge">{DIR[p.direction]}</span>
              {p.synced ? <span className="badge ok">транки в LiveKit</span> : <span className="badge warn">не синхронизирован</span>}
              <div className="spacer" />
              <button className="btn mini" onClick={() => { setForm(toForm(p)); setError(""); setNote(""); }}>Изменить</button>
              <button className="btn mini danger" onClick={() => void remove(p)}>Удалить</button></div>
            <div className="muted small">{p.host}:{p.port} · {p.transport.toUpperCase()}{p.username ? ` · логин ${p.username}` : ""} · пароль {p.secret_set ? "задан" : "не задан"}{p.caller_id ? ` · номер ${p.caller_id}` : ""}
              {p.rooms_using ? ` · комнат: ${p.rooms_using}` : ""}</div>
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn" onClick={() => void check(p)} disabled={d === "run"}>{d === "run" ? "Проверка…" : "Проверить настройки"}</button>
              <button className="btn" onClick={() => void sync(p)} disabled={busy === `sync-${p.id}`} title="Создать/обновить транки профиля в LiveKit">Синхронизировать</button>
            </div>
            {d && d !== "run" && <StageList result={d} />}
            {!d && p.last_check?.stages && <div className="muted small">Последняя проверка ({fmtDate(p.last_check_at)}): {p.last_check.ok ? "успешно" : "были проблемы — нажмите «Проверить настройки»"}.</div>}
            {p.direction !== "inbound" && (
              <div className="row" style={{ marginTop: 8 }}>
                <input value={callNum[p.id] ?? ""} onChange={(e) => setCallNum((x) => ({ ...x, [p.id]: e.target.value }))} placeholder="Номер для тестового вызова, например +70001112233" inputMode="tel" style={{ maxWidth: 360 }} aria-label="Номер для тестового вызова" />
                <button className="btn" disabled={cr === "run"} onClick={() => void testCall(p)}>{cr === "run" ? "Вызываем…" : "Тестовый вызов"}</button>
              </div>)}
            {cr && cr !== "run" && <div className={`alert ${cr.ok ? "ok" : "error"}`} role="status">{cr.ok ? "✓ " : "✗ "}{cr.text}</div>}
          </div>
        );
      })}

      <details className="group"><summary>Подсказка: как это соответствует настройкам Asterisk (PJSIP)</summary>
        <div className="help">
          Транк в Asterisk обычно состоит не из одной пары «логин+пароль»: <b>endpoint</b> (параметры вызова), <b>auth</b> (учётные данные), <b>aor</b> (куда отправлять), иногда <b>registration</b>,
          а для входящих часто используется сопоставление по IP (<b>identify</b>). Здесь: «Адрес АТС и порт» — aor; «Логин и пароль» — auth (можно не заполнять, если АТС принимает по IP);
          «Разрешённые адреса АТС» — identify для входящих; «Допустимые номера» и «Caller ID» — правила номеров endpoint; кодеки — <code>allow=</code>. Если звонок не проходит, начните с «Проверить настройки»:
          он покажет, отвечает ли АТС на SIP OPTIONS, есть ли транки в LiveKit и работает ли служба SIP; при отказе АТС в тестовом вызове показывается SIP-код с пояснением (403 — нет прав у транка, 404 — нет маршрута, 408 — нет ответа, 488 — не совпали кодеки).
        </div>
      </details>
    </section>
  );
}
