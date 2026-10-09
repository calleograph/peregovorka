import { useEffect, useRef, useState } from "react";
import { api, type ApiError, type Profile } from "../api";
import Avatar from "../components/Avatar";
import AvatarCropper from "../components/AvatarCropper";
import { Icon } from "../components/Icons";
import { chatSoundEnabled, playChatSound, setChatSoundEnabled, setSoundVolume, soundVolume } from "../chatSound";
import { handSoundEnabled, playHandSound, setHandSoundEnabled } from "../handSound";
import { fmt } from "../util";

/** Личные звуковые уведомления комнаты (хранятся в браузере): сообщения чата, поднятая рука, общая громкость. */
function SoundSettings() {
  const [chat, setChat] = useState(chatSoundEnabled);
  const [hand, setHand] = useState(handSoundEnabled);
  const [vol, setVol] = useState(soundVolume);
  return (
    <div className="card">
      <h2>Звуки уведомлений</h2>
      <label className="check"><input type="checkbox" checked={chat} onChange={(e) => { setChatSoundEnabled(e.target.checked); setChat(e.target.checked); }} />
        <span className="check-body">Звук новых сообщений чата<span className="help">Один короткий сигнал на сообщение другого участника; серия сообщений звучит один раз, своё сообщение — без звука.</span></span></label>
      <label className="check"><input type="checkbox" checked={hand} onChange={(e) => { setHandSoundEnabled(e.target.checked); setHand(e.target.checked); }} />
        <span className="check-body">Звук поднятой руки<span className="help">Другой сигнал (два тона), чтобы отличать его от чата.</span></span></label>
      <label>Громкость уведомлений
        <input type="range" min={0} max={1} step={0.05} value={vol} onChange={(e) => { const v = Number(e.target.value); setSoundVolume(v); setVol(v); }} aria-valuetext={`${Math.round(vol * 100)} %`} /></label>
      <div className="row">
        <button type="button" className="btn mini" onClick={() => playChatSound(true)}>Проверить: сообщение</button>
        <button type="button" className="btn mini" onClick={() => playHandSound(true)}>Проверить: рука</button>
      </div>
    </div>
  );
}

const FIELDS: [keyof Profile, string][] = [["display_name", "ФИО"], ["login", "Логин"], ["email", "E-mail"], ["title", "Должность"], ["department", "Подразделение"], ["phone", "Телефон"]];

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
      <div className="profile-grid">
        <div className="card profile-card">
          <div className="profile-ava">
            <Avatar name={p.display_name} url={p.avatar_url} size={112} />
            <div className="row" style={{ justifyContent: "center" }}>
              <button className="btn mini" onClick={() => input.current?.click()} disabled={busy}>{p.avatar_url ? "Заменить" : "Загрузить фото"}</button>
              {p.avatar_url && <button className="btn mini ghost danger" onClick={() => void run(() => api.deleteAvatar(), "Аватарка удалена — будут показаны инициалы.")} disabled={busy}>Удалить</button>}
            </div>
            <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) setFile(f); }} />
            <span className="muted small">JPEG, PNG или WebP. Хранится на сервере; из каталога фото не берётся.</span>
          </div>
        </div>
        <div className="card">
          <h2>Данные учётной записи</h2>
          <dl className="profile-dl">
            {FIELDS.map(([k, label]) => (
              <div key={k}><dt>{label}</dt><dd>{(p[k] as string | null) || <span className="muted">не указано</span>}</dd></div>
            ))}
            <div><dt>Источник</dt><dd>{p.source === "ad" ? "Active Directory" : "Локальная учётная запись"}</dd></div>
            <div><dt>Данные из AD обновлены</dt><dd>{p.synced_at ? fmt(p.synced_at) : <span className="muted">ещё не обновлялись</span>}</dd></div>
          </dl>
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
      <SoundSettings />
      {file && <AvatarCropper file={file} onCancel={() => setFile(null)} onDone={async (blob) => { await api.uploadAvatar(blob).then((x) => { setP(x); onChanged(x); setMsg({ ok: true, text: "Аватарка сохранена." }); }); setFile(null); }} />}
    </section>
  );
}
