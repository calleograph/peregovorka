import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, type ApiError, type DiagResult, type LdapProfile, type LegacyLdap } from "../../api";
import { SecretInput, StageList, secretToSend } from "./common";

interface Form {
  id?: string; name: string; enabled: boolean; host: string; port: number; protocol: "ldaps" | "starttls"; base_dn: string; upn_suffix: string; netbios_domain: string;
  timeout_s: number; bind_dn: string; secret: string; secret_set: boolean; login_attribute: string; display_name_attribute: string; email_attribute: string;
  use_for_users: boolean; use_for_admins: boolean;
}

const empty: Form = { name: "", enabled: true, host: "", port: 636, protocol: "ldaps", base_dn: "", upn_suffix: "", netbios_domain: "", timeout_s: 5, bind_dn: "", secret: "", secret_set: false,
  login_attribute: "sAMAccountName", display_name_attribute: "displayName", email_attribute: "mail", use_for_users: true, use_for_admins: true };

const toForm = (p: LdapProfile): Form => ({ ...p, secret: "", secret_set: p.secret_set });

/**
 * Подключения к каталогу (LDAPS) — вместо правки .env. Можно добавить несколько (несколько доменов/лесов): вход проверяется по порядку, подсказки
 * `ДОМЕН\логин` и `логин@суффикс` выбирают нужное подключение. Пароль сервисной учётной записи хранится зашифрованно и не показывается.
 * «Проверить» делает реальную проверку: DNS → TCP → TLS с проверкой цепочки по загруженным CA → вход → чтение Base DN.
 */
export default function LdapAdmin({ onOpen }: { onOpen?: (id: string) => void }) {
  const [items, setItems] = useState<LdapProfile[]>([]);
  const [env, setEnv] = useState<{ configured: boolean; uris: string[] } | null>(null);
  const [legacy, setLegacy] = useState<LegacyLdap | null>(null);
  const [bootErrors, setBootErrors] = useState<Record<string, string>>({});
  const [importing, setImporting] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [form, setForm] = useState<Form | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [diag, setDiag] = useState<Record<string, DiagResult | "run">>({});

  const load = useCallback(() => api.admin.ldapProfiles().then((r) => { setItems(r.items); setEnv(r.env); setErrors(r.errors); setLegacy(r.legacy); setBootErrors(r.boot_errors ?? {}); }).catch((e) => setError((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));

  const body = (f: Form) => ({
    name: f.name.trim(), enabled: f.enabled, host: f.host.trim(), port: f.port, protocol: f.protocol, base_dn: f.base_dn.trim(), upn_suffix: f.upn_suffix.trim(),
    netbios_domain: f.netbios_domain.trim(), timeout_s: f.timeout_s, bind_dn: f.bind_dn.trim(), login_attribute: f.login_attribute.trim(),
    display_name_attribute: f.display_name_attribute.trim(), email_attribute: f.email_attribute.trim(), use_for_users: f.use_for_users, use_for_admins: f.use_for_admins,
    ...(secretToSend(f.secret) !== undefined ? { secret: secretToSend(f.secret) } : {}),
  });

  const test = async (id: string) => {
    setDiag((d) => ({ ...d, [id]: "run" }));
    try { const r = await api.admin.testLdap(id); setDiag((d) => ({ ...d, [id]: r })); }
    catch (e) { setDiag((d) => ({ ...d, [id]: { ok: false, stages: [], message: (e as ApiError).message } })); }
  };

  const save = async (e: FormEvent, thenTest = false) => {
    e.preventDefault();
    if (!form) return;
    setError(""); setNote(""); setBusy(true);
    try {
      const saved = form.id ? await api.admin.updateLdap(form.id, body(form)) : await api.admin.createLdap(body(form));
      setForm(null); setNote("Сохранено."); await load();
      if (thenTest) void test(saved.id);
    } catch (err) { setError((err as ApiError).message); }
    setBusy(false);
  };

  const remove = async (p: LdapProfile) => {
    if (!window.confirm(`Удалить подключение «${p.name}»? Пользователи этого каталога не смогут войти, пока не будет добавлено другое подключение.`)) return;
    setError(""); setNote("");
    try { await api.admin.deleteLdap(p.id); await load(); } catch (e) { setError((e as ApiError).message); }
  };
  const importLegacy = async () => {
    setError(""); setNote(""); setImporting(true);
    try {
      const r = await api.admin.ldapLegacyImport();
      setNote(`Настройки перенесены: ${r.profiles.join(", ")}${r.ca_added ? `; сертификатов CA: ${r.ca_added}` : ""}${r.groups_added ? `; групп доступа: ${r.groups_added}` : ""}. Подключение проверено.`);
      await load();
    } catch (e) { setError((e as ApiError).message); }
    setImporting(false);
  };
  const toggle = async (p: LdapProfile) => { try { await api.admin.updateLdap(p.id, { enabled: !p.enabled }); await load(); } catch (e) { setError((e as ApiError).message); } };
  const move = async (p: LdapProfile, d: -1 | 1) => { try { setItems((await api.admin.moveLdap(p.id, d)).items); } catch (e) { setError((e as ApiError).message); } };

  return (
    <section>
      <div className="row"><h2>Подключения к каталогу (LDAPS)</h2><div className="spacer" />
        {!form && <button className="btn primary" onClick={() => { setForm({ ...empty }); setError(""); setNote(""); }}>＋ Добавить подключение</button>}</div>
      <p className="muted">Каталог Active Directory, по которому сотрудники входят в систему. Соединение — только защищённое (LDAPS или STARTTLS), сертификат сервера проверяется по сертификатам из раздела{" "}
        {onOpen ? <a href="#ca" onClick={(e) => { e.preventDefault(); onOpen("ca"); }}>«Сертификаты (CA)»</a> : "«Сертификаты (CA)»"}. Сервисной учётной записи нужно только чтение.</p>
      {legacy?.needs_import && (
        <div className="alert info legacy-ldap" role="status">
          <b>Обнаружена старая конфигурация LDAP.</b> {legacy.active ? "Вход по домену сейчас работает по настройке из файла .env" : "В файле .env есть настройка каталога, но она не загрузилась"}{" "}
          ({legacy.uris.join(", ")}). Нажмите «Перенести настройки»: сертификат CA, подключение и пароль сервисной учётной записи (хранится зашифрованно) перейдут в управляемые настройки,
          подключение будет проверено. Если проверка не пройдёт — ничего не изменится, а прежний вход продолжит работать.
          {legacy.last_error && <div className="small" style={{ marginTop: 4 }}>Последняя автоматическая попытка: {legacy.last_error}</div>}
          <div className="row" style={{ marginTop: 6 }}><button className="btn primary" disabled={importing} onClick={() => void importLegacy()}>{importing ? "Переношу…" : "Перенести настройки"}</button></div>
        </div>
      )}
      {legacy?.present && legacy.migrated && <div className="muted small">Прежняя настройка LDAP в файле .env перенесена и больше не используется; строки <code>LDAP_*</code> из .env можно удалить.</div>}
      {bootErrors.ca && <div className="alert error">Не удалось записать набор сертификатов при запуске: {bootErrors.ca}. Откройте «Обзор» и нажмите «Исправить автоматически».</div>}
      {env?.configured && !items.length && !legacy?.needs_import && <div className="alert info">Сейчас используется прежняя настройка из файла установки (.env): {env.uris.join(", ")}. Добавьте подключение здесь — и оно заменит её.</div>}
      {!items.length && !env?.configured && !form && <div className="alert info">Каталог пока не подключён: войти можно только локальным администратором. Добавьте подключение, затем загрузите сертификат CA и укажите группы администраторов.</div>}
      {Object.entries(errors).map(([n, m]) => <div key={n} className="alert error">Подключение «{n}» не загружено: {m}</div>)}
      {note && <div className="alert ok" role="status">{note}</div>}
      {error && <div className="alert error" role="alert">{error}</div>}

      {form && (
        <form className="card form" onSubmit={(e) => void save(e)} noValidate>
          <h3 style={{ margin: 0 }}>{form.id ? `Изменение подключения «${form.name}»` : "Новое подключение"}</h3>
          <div className="cols">
            <label>Название<input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Основной домен" maxLength={120} /></label>
            <label className="check"><input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} /> <span className="check-body">Подключение включено</span></label>
          </div>
          <fieldset className="group"><legend>Сервер</legend>
            <div className="cols">
              <label>Сервер (имя или IP)<input value={form.host} onChange={(e) => set("host", e.target.value)} placeholder="dc1.example.local" autoCapitalize="none" spellCheck={false} />
                <span className="help">Без схемы и слэшей. Имя должно совпадать с именем в сертификате сервера.</span></label>
              <label>Протокол
                <select value={form.protocol} onChange={(e) => { const v = e.target.value as "ldaps" | "starttls"; setForm((f) => (f ? { ...f, protocol: v, port: v === "ldaps" ? 636 : 389 } : f)); }}>
                  <option value="ldaps">LDAPS (шифрование с первого байта)</option><option value="starttls">LDAP + STARTTLS</option>
                </select></label>
              <label>Порт<input type="number" min={1} max={65535} value={form.port} onChange={(e) => set("port", Number(e.target.value))} style={{ maxWidth: 140 }} />
                <span className="help">LDAPS — 636 (или 3269 для глобального каталога), STARTTLS — 389.</span></label>
              <label>Таймаут<input type="number" min={1} max={60} value={form.timeout_s} onChange={(e) => set("timeout_s", Number(e.target.value))} style={{ maxWidth: 140 }} /><span className="help">секунд</span></label>
            </div>
            <label>Base DN<input value={form.base_dn} onChange={(e) => set("base_dn", e.target.value)} placeholder="DC=example,DC=local" spellCheck={false} />
              <span className="help">Корень поиска пользователей и групп.</span></label>
            <div className="cols">
              <label>Домен (NetBIOS)<input value={form.netbios_domain} onChange={(e) => set("netbios_domain", e.target.value)} placeholder="EXAMPLE" />
                <span className="help">Для входа в виде <code>EXAMPLE\user1</code>.</span></label>
              <label>Суффикс UPN<input value={form.upn_suffix} onChange={(e) => set("upn_suffix", e.target.value)} placeholder="example.local" />
                <span className="help">Для входа в виде <code>user1@example.local</code>.</span></label>
            </div>
          </fieldset>
          <fieldset className="group"><legend>Сервисная учётная запись (только чтение)</legend>
            <label>DN или логин<input value={form.bind_dn} onChange={(e) => set("bind_dn", e.target.value)} placeholder="CN=svc-read,OU=Service,DC=example,DC=local" spellCheck={false} autoComplete="off" /></label>
            <SecretInput label="Пароль" isSet={form.secret_set} value={form.secret} onChange={(v) => set("secret", v)} help="Хранится в зашифрованном виде и после сохранения не показывается." />
          </fieldset>
          <fieldset className="group"><legend>Для чего используется</legend>
            <label className="check"><input type="checkbox" checked={form.use_for_users} onChange={(e) => set("use_for_users", e.target.checked)} />
              <span className="check-body">Вход пользователей<span className="help">Сотрудники этого каталога входят в комнаты и историю.</span></span></label>
            <label className="check"><input type="checkbox" checked={form.use_for_admins} onChange={(e) => set("use_for_admins", e.target.checked)} />
              <span className="check-body">Вход администраторов<span className="help">Члены групп из раздела «Администраторы» получают права администратора. Если выключено — через это подключение администратором не войти (локальный администратор не затрагивается).</span></span></label>
          </fieldset>
          <details className="group"><summary>Атрибуты каталога (обычно менять не нужно)</summary>
            <div className="cols">
              <label>Логин<input value={form.login_attribute} onChange={(e) => set("login_attribute", e.target.value)} /></label>
              <label>Отображаемое имя<input value={form.display_name_attribute} onChange={(e) => set("display_name_attribute", e.target.value)} /></label>
              <label>Электронная почта<input value={form.email_attribute} onChange={(e) => set("email_attribute", e.target.value)} />
                <span className="help">Откуда брать адрес для рассылки материалов встреч.</span></label>
            </div>
          </details>
          <div className="row form-actions">
            <button className="btn primary" disabled={busy}>{busy ? "Сохранение…" : "Сохранить"}</button>
            <button type="button" className="btn" disabled={busy} onClick={(e) => void save(e as unknown as FormEvent, true)}>Сохранить и проверить</button>
            <button type="button" className="btn ghost" onClick={() => setForm(null)}>Отмена</button>
          </div>
        </form>
      )}

      {items.map((p, i) => {
        const d = diag[p.id];
        return (
          <div key={p.id} className="card conn-card">
            <div className="row">
              <h3 style={{ margin: 0 }}>{p.name}</h3>
              <span className={`badge ${p.enabled ? "ok" : "warn"}`}>{p.enabled ? "включено" : "выключено"}</span>
              {!p.use_for_users && <span className="badge">только администраторы</span>}
              {!p.use_for_admins && <span className="badge">без администраторского входа</span>}
              <div className="spacer" />
              <button className="btn mini ghost" disabled={i === 0} onClick={() => void move(p, -1)} title="Выше (проверяется раньше)" aria-label="Выше">↑</button>
              <button className="btn mini ghost" disabled={i === items.length - 1} onClick={() => void move(p, 1)} title="Ниже" aria-label="Ниже">↓</button>
            </div>
            <div className="muted small"><code>{p.uri}</code> · Base DN <code>{p.base_dn}</code>{p.netbios_domain && <> · домен {p.netbios_domain}</>}{p.upn_suffix && <> · @{p.upn_suffix}</>} · сервисная запись <code>{p.bind_dn}</code> · пароль {p.secret_set ? "задан" : "не задан"}</div>
            <div className="row form-actions">
              <button className="btn primary" disabled={d === "run"} onClick={() => void test(p.id)}>{d === "run" ? "Проверка…" : "Проверить"}</button>
              <button className="btn" onClick={() => { setForm(toForm(p)); setError(""); setNote(""); }}>Изменить</button>
              <button className="btn ghost" onClick={() => void toggle(p)}>{p.enabled ? "Выключить" : "Включить"}</button>
              <button className="btn ghost danger" onClick={() => void remove(p)}>Удалить</button>
            </div>
            {d && d !== "run" && <StageList result={d} />}
          </div>
        );
      })}
    </section>
  );
}
