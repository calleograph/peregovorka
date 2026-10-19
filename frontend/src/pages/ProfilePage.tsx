import { useEffect, useRef, useState } from "react";
import { api, type ApiError, type Profile, type SessionInfo, type TotpStatus } from "../api";
import TotpSetup from "../components/TotpSetup";
import Avatar from "../components/Avatar";
import AvatarCropper from "../components/AvatarCropper";
import { Icon } from "../components/Icons";
import { chatSoundEnabled, playChatSound, setChatSoundEnabled, setSoundVolume, soundVolume } from "../chatSound";
import { handSoundEnabled, playHandSound, setHandSoundEnabled } from "../handSound";
import { fmt } from "../util";
import { THEMES, getUserTheme, setUserTheme, type ThemeId } from "../site";

/** «Активные устройства»: сессии этой учётной записи; можно завершить любую (в том числе все остальные). Вход с другого устройства сессию на этом не завершает. */
function ActiveDevices() {
  const [items, setItems] = useState<SessionInfo[] | null>(null);
  const [msg, setMsg] = useState("");
  const load = () => api.mySessions().then((r) => setItems(r.items)).catch((e) => setMsg((e as ApiError).message));
  useEffect(() => { void load(); }, []);
  const end = async (id: string) => { try { await api.endSession(id); setMsg("Сеанс завершён."); void load(); } catch (e) { setMsg((e as ApiError).message); } };
  const others = async () => { try { const r = await api.endOtherSessions(); setMsg(r.ended ? `Завершено сеансов: ${r.ended}.` : "Других сеансов нет."); void load(); } catch (e) { setMsg((e as ApiError).message); } };
  return (
    <div className="card" aria-label="Активные устройства">
      <div className="row"><h2 style={{ margin: 0 }}>Активные устройства</h2><div className="spacer" />
        {items && items.length > 1 && <button className="btn mini" onClick={() => void others()}>Завершить все остальные</button>}</div>
      <p className="muted small">Вы можете одновременно работать на нескольких устройствах — вход с телефона не завершает сеанс на компьютере. Незнакомое устройство в списке — завершите его сеанс и смените пароль.</p>
      {msg && <div className="alert info small" role="status">{msg}</div>}
      {!items ? <p className="muted">Загрузка…</p> : (
        <ul className="sess-list">
          {items.map((s) => (
            <li key={s.id}><span><b>{s.device}</b>{s.current && <span className="badge ok"> это устройство</span>}
              <small className="muted"> · {s.ip || "адрес не определён"} · последняя активность {s.last_seen ? fmt(new Date(s.last_seen * 1000).toISOString()) : "—"}</small></span>
              {!s.current && <button className="btn mini" onClick={() => void end(s.id)}>Завершить</button>}</li>))}
        </ul>)}
    </div>
  );
}

/** Личные звуковые уведомления комнаты (хранятся в браузере): сообщения чата, поднятая рука, общая громкость. */
/** Тема оформления этого пользователя (хранится в браузере, как и звуки): «как в системе» по умолчанию, светлая, тёмная и три пастельные. */
function ThemeSettings() {
  const [cur, setCur] = useState<ThemeId>(getUserTheme);
  return (
    <div className="card">
      <h2>Оформление</h2>
      <p className="muted small" style={{ margin: "0 0 6px" }}>Тема интерфейса — только для вас, на этом устройстве. По умолчанию используется тема устройства.</p>
      <div className="theme-grid" role="group" aria-label="Тема оформления">
        {THEMES.map((t) => (
          <button key={t.id} type="button" className="theme-opt" aria-pressed={cur === t.id} onClick={() => { setUserTheme(t.id); setCur(t.id); }} title={t.hint}>
            <span className="sw" aria-hidden>{t.swatch.map((c, i) => <i key={i} style={{ background: c }} />)}</span>
            <b>{t.label}</b><small>{t.hint}</small>
          </button>
        ))}
      </div>
    </div>
  );
}

/** Личные звуковые уведомления комнаты (хранятся в браузере): сообщения чата, поднятая рука. */
function SoundSettings() {
  const [chat, setChat] = useState(chatSoundEnabled);
  const [hand, setHand] = useState(handSoundEnabled);
  const [vol, setVol] = useState(soundVolume);
  return (
    <div className="card">
      <h2>Уведомления</h2>
      <p className="muted small" style={{ margin: "0 0 6px" }}>Звуки в комнате — только на этом устройстве.</p>
      <label className="check"><input type="checkbox" checked={chat} onChange={(e) => { setChatSoundEnabled(e.target.checked); setChat(e.target.checked); }} />
        <span className="check-body">Звук новых сообщений чата<span className="help">Короткий сигнал на сообщение другого участника.</span></span></label>
      <label className="check"><input type="checkbox" checked={hand} onChange={(e) => { setHandSoundEnabled(e.target.checked); setHand(e.target.checked); }} />
        <span className="check-body">Звук поднятой руки<span className="help">Другой сигнал, не похожий на чат.</span></span></label>
      <label>Громкость уведомлений
        <input type="range" min={0} max={1} step={0.05} value={vol} onChange={(e) => { const v = Number(e.target.value); setSoundVolume(v); setVol(v); }} aria-valuetext={`${Math.round(vol * 100)} %`} /></label>
      <div className="row">
        <button type="button" className="btn mini" onClick={() => playChatSound(true)}>Проверить: сообщение</button>
        <button type="button" className="btn mini" onClick={() => playHandSound(true)}>Проверить: рука</button>
      </div>
    </div>
  );
}


const FIELD_LABEL: Record<string, string> = { display_name: "ФИО", email: "E-mail", title: "Должность", department: "Подразделение", phone: "Телефон" };
const SOURCE_LABEL: Record<string, string> = { ad: "Каталог", bitrix: "Портал", local: "Вы" };

/** Данные учётной записи: итог по каждому полю, источник, значение каталога и правка пользователем (сильнее каталога, до «Восстановить из LDAP»). */
function ProfileFields({ p, onChange, disabled }: { p: Profile; onChange: (p: Profile) => void; disabled: boolean }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [val, setVal] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<Profile>) => { setBusy(true); setErr(""); try { onChange(await fn()); setEditing(null); } catch (e) { setErr((e as ApiError).message); } setBusy(false); };
  return (
    <div>
      <dl className="profile-dl">
        <div><dt>Логин</dt><dd>{p.login}</dd></div>
        {(p.fields ?? []).map((f) => (
          <div key={f.name}>
            <dt>{FIELD_LABEL[f.name]}</dt>
            <dd>
              {editing === f.name ? (
                <form className="row" onSubmit={(e) => { e.preventDefault(); void run(() => api.editProfile({ [f.name]: val })); }}>
                  <input value={val} onChange={(e) => setVal(e.target.value)} autoFocus maxLength={f.name === "email" ? 320 : 300} aria-label={FIELD_LABEL[f.name]} />
                  <button className="btn mini primary" disabled={busy || !val.trim()}>Сохранить</button>
                  <button type="button" className="btn mini" onClick={() => setEditing(null)}>Отмена</button>
                </form>
              ) : (
                <>
                  {f.value || <span className="muted">не указано</span>}
                  {p.source === "ad" && f.value && <span className={`badge ${f.manual ? "warn" : ""}`} style={{ marginLeft: 8 }} title={f.manual && f.manual_at ? `Изменено вами ${fmt(f.manual_at)}` : undefined}>{f.manual ? "изменено вами" : SOURCE_LABEL[f.source]}</span>}
                  {f.editable && <button className="btn mini ghost" style={{ marginLeft: 8 }} disabled={disabled || busy} onClick={() => { setEditing(f.name); setVal(f.value ?? ""); setErr(""); }}>Изменить</button>}
                  {f.manual && p.restorable && <>
                    <div className="muted small">В каталоге: {f.ad_value || "не указано"}{f.manual_at ? ` · изменено ${fmt(f.manual_at)}` : ""}</div>
                    <button className="btn mini ghost" disabled={disabled || busy} onClick={() => void run(() => api.restoreProfile([f.name]))}>Восстановить из LDAP</button>
                  </>}
                </>
              )}
            </dd>
          </div>
        ))}
        <div><dt>Источник</dt><dd>{p.source === "ad" ? "Active Directory" : "Локальная учётная запись"}</dd></div>
        <div><dt>Данные из AD обновлены</dt><dd>{p.synced_at ? fmt(p.synced_at) : <span className="muted">ещё не обновлялись</span>}</dd></div>
      </dl>
      {err && <div className="alert error" role="alert">{err}</div>}
      {(p.fields ?? []).some((f) => f.manual) && p.restorable && <p className="small muted">Значения, которые вы изменили, не перезаписываются данными из каталога. Чтобы вернуть значение из каталога, нажмите «Восстановить из LDAP».</p>}
    </div>
  );
}

function TwoFactorSection() {
  const [st, setSt] = useState<TotpStatus | null>(null);
  const load = () => { api.totp.status().then(setSt).catch(() => setSt(null)); };
  useEffect(load, []);
  if (!st || !st.available) return null;
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2>Двухфакторная аутентификация</h2>
      <TotpSetup status={st} onChanged={load} />
    </div>
  );
}

/** Личный кабинет: данные из Active Directory (только разрешённые атрибуты), обновление из AD и собственная аватарка. */
export default function ProfilePage({ onChanged }: { onChanged: (p: Profile) => void }) {
  const [p, setP] = useState<Profile | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { void api.profile().then((x) => { setP(x); onChanged(x); }).catch((e) => setMsg({ ok: false, text: (e as ApiError).message })); }, [onChanged]);

  const run = async (fn: () => Promise<Profile>, ok: string) => {
    setBusy(true); setMsg(null);
    try { const x = await fn(); setP(x); onChanged(x); setMsg({ ok: true, text: ok }); } catch (e) { setMsg({ ok: false, text: (e as ApiError).message }); }
    setBusy(false);
  };

  if (!p) return msg ? <div className="alert error">{msg.text}</div> : <div className="muted">Загрузка…</div>;
  return (
    <section className="profile-page">
      <p className="eyebrow">Личный кабинет</p>
      <h1>{p.display_name}</h1>
      <ActiveDevices />
      <div className="profile-grid">
        <div className="card profile-card">
          <div className="profile-ava">
            <Avatar name={p.display_name} url={p.avatar_url} size={112} />
            <div className="row" style={{ justifyContent: "center" }}>
              <button className="btn mini" onClick={() => input.current?.click()} disabled={busy}>{p.avatar_url ? "Изменить фото" : "Загрузить фото"}</button>
              {p.avatar_url && <button className="btn mini ghost danger" onClick={() => void run(() => api.deleteAvatar(), "Аватарка удалена — будут показаны инициалы.")} disabled={busy}>Удалить фото</button>}
            </div>
            <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) setFile(f); }} />
            <span className="muted small">JPEG, PNG или WebP. Хранится на сервере; из каталога фото не берётся.</span>
          </div>
        </div>
        <div className="card">
          <h2>Данные учётной записи</h2>
          <ProfileFields p={p} onChange={(np) => { setP(np); onChanged(np); }} disabled={busy} />
          {p.source === "ad" ? (
            <div className="row">
              <button className="btn primary" onClick={() => void run(() => api.refreshProfile(), "Данные обновлены из Active Directory.")} disabled={busy}>
                <Icon name="retry" size={16} /> Обновить данные из Active Directory</button>
              <span className="muted small">Перечитываются ФИО, e-mail, должность, подразделение и телефон. Данные обновляются и при каждом входе.</span>
            </div>
          ) : <p className="muted small">Это локальная (аварийная) учётная запись: данных в каталоге у неё нет.</p>}
          {msg && <div className={`alert ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
        </div>
      </div>
      <TwoFactorSection />
      <ThemeSettings />
      <SoundSettings />
      {file && <AvatarCropper file={file} onCancel={() => setFile(null)} onDone={async (blob) => { await api.uploadAvatar(blob).then((x) => { setP(x); onChanged(x); setMsg({ ok: true, text: "Аватарка сохранена." }); }); setFile(null); }} />}
    </section>
  );
}
