import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError, type CaCert, type CaInfo } from "../../api";
import { fmtDate } from "./common";

function toBase64(buf: ArrayBuffer): string {
  let s = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

function CertCard({ c }: { c: CaInfo }) {
  return (
    <dl className="cert-card">
      <div><dt>Subject</dt><dd><code>{c.subject}</code></dd></div>
      <div><dt>Issuer</dt><dd><code>{c.issuer}</code></dd></div>
      <div><dt>Серийный номер</dt><dd><code>{c.serial}</code></dd></div>
      <div><dt>SHA-256</dt><dd><code className="fp">{c.sha256.match(/.{2}/g)?.join(":")}</code></dd></div>
      <div><dt>Действует</dt><dd>{fmtDate(c.not_before)} — {fmtDate(c.not_after)} {c.expired && <span className="badge warn">срок истёк</span>}{c.not_yet_valid && <span className="badge warn">ещё не действует</span>}</dd></div>
      <div><dt>Тип</dt><dd>{c.type}</dd></div>
    </dl>
  );
}

/**
 * Общий набор CA-сертификатов для проверки LDAPS (и, по желанию, SMTP). Загрузить можно PEM или DER файлом либо вставить PEM текстом; перед сохранением
 * сертификат разбирается и показывается. Закрытые ключи не принимаются.
 */
export default function CaAdmin() {
  const [items, setItems] = useState<CaCert[]>([]);
  const [pem, setPem] = useState("");
  const [file, setFile] = useState<{ name: string; b64: string } | null>(null);
  const [label, setLabel] = useState("");
  const [preview, setPreview] = useState<CaInfo[] | null>(null);
  const [confirmNonCa, setConfirmNonCa] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => api.admin.caList().then((r) => setItems(r.items)).catch((e) => setError((e as ApiError).message)), []);
  useEffect(() => { void load(); }, [load]);

  const payload = () => (file ? { data_base64: file.b64 } : { pem });
  const reset = () => { setPem(""); setFile(null); setLabel(""); setPreview(null); setConfirmNonCa(false); if (fileRef.current) fileRef.current.value = ""; };

  const inspect = async () => {
    setError(""); setNote(""); setPreview(null); setBusy(true);
    try { setPreview((await api.admin.caInspect(payload())).items); } catch (e) { setError((e as ApiError).message); }
    setBusy(false);
  };
  const add = async () => {
    setError(""); setNote(""); setBusy(true);
    try {
      const r = await api.admin.caAdd({ ...payload(), label, confirm_non_ca: confirmNonCa });
      setNote(r.added.length ? `Добавлено сертификатов: ${r.added.length}.` : "Такие сертификаты уже есть в наборе.");
      reset(); await load();
    } catch (e) { setError((e as ApiError).message); }
    setBusy(false);
  };
  const remove = async (c: CaCert) => {
    if (!window.confirm(`Удалить сертификат «${c.label}» из набора? Подключения, которым он нужен для проверки, перестанут работать.`)) return;
    try { await api.admin.caDelete(c.id); await load(); } catch (e) { setError((e as ApiError).message); }
  };
  const pick = async (f: File | undefined) => {
    setPreview(null); setError("");
    if (!f) { setFile(null); return; }
    if (f.size > 256 * 1024) { setError("Файл слишком большой для сертификата (больше 256 КБ)."); return; }
    setFile({ name: f.name, b64: toBase64(await f.arrayBuffer()) });
    setPem("");
  };
  const hasInput = !!file || pem.trim() !== "";
  const nonCa = preview?.some((p) => !p.is_ca && !p.self_signed) ?? false;

  return (
    <section>
      <div className="row"><h2>Сертификаты (CA)</h2></div>
      <p className="muted">Корневые и промежуточные сертификаты удостоверяющего центра, которым подписаны сертификаты ваших серверов каталога (и почты). Нужны, чтобы система проверяла, что соединяется именно с вашим сервером, а не с подменой.
        Закрытые ключи сюда загружать нельзя — только сертификат.</p>
      {note && <div className="alert ok" role="status">{note}</div>}
      {error && <div className="alert error" role="alert">{error}</div>}

      <div className="card form">
        <h3 style={{ margin: 0 }}>Добавить сертификат</h3>
        <label>Файл сертификата <span className="muted small">(.pem, .crt, .cer — PEM или DER)</span>
          <input ref={fileRef} type="file" accept=".pem,.crt,.cer,.der,application/x-x509-ca-cert,application/x-pem-file" onChange={(e) => void pick(e.target.files?.[0])} /></label>
        <label>…или вставьте PEM текстом
          <textarea rows={5} value={pem} onChange={(e) => { setPem(e.target.value); setFile(null); setPreview(null); }} spellCheck={false}
                    placeholder={"-----BEGIN CERTIFICATE-----\n…\n-----END CERTIFICATE-----"} style={{ fontFamily: "var(--mono)", fontSize: 12.5 }} /></label>
        {file && <div className="muted small">Выбран файл: {file.name}</div>}
        <label>Название <span className="muted small">(необязательно)</span><input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Корневой CA организации" maxLength={200} /></label>
        <div className="row"><button className="btn" onClick={() => void inspect()} disabled={!hasInput || busy}>Разобрать сертификат</button></div>
        {preview && (
          <>
            {preview.map((p) => <CertCard key={p.sha256} c={p} />)}
            {nonCa && (
              <label className="check alert info"><input type="checkbox" checked={confirmNonCa} onChange={(e) => setConfirmNonCa(e.target.checked)} />
                <span className="check-body">Это сертификат сервера, а не удостоверяющего центра. Добавлять его стоит, только если вы осознанно хотите доверять именно ему.</span></label>
            )}
            <div className="row"><button className="btn primary" onClick={() => void add()} disabled={busy || (nonCa && !confirmNonCa)}>Добавить в набор</button>
              <button className="btn ghost" onClick={reset}>Отмена</button></div>
          </>
        )}
      </div>

      <h3>В наборе{items.length ? ` (${items.length})` : ""}</h3>
      {!items.length && <div className="alert info">Сертификатов пока нет. Без них подключение к каталогу по LDAPS не заработает.</div>}
      {items.map((c) => (
        <div key={c.id} className="card conn-card">
          <div className="row"><h4 style={{ margin: 0 }}>{c.label}</h4>
            {c.expired && <span className="badge warn">срок истёк</span>}{c.source === "file" && <span className="badge">из файла установки</span>}
            <div className="spacer" />{c.source === "web" && <button className="btn mini ghost danger" onClick={() => void remove(c)}>Удалить</button>}</div>
          <CertCard c={{ ...c, not_yet_valid: false, type: c.is_ca ? (c.self_signed ? "корневой (самоподписанный) CA" : "промежуточный CA") : "сертификат сервера (не CA)" }} />
          {c.added_by && <div className="muted small">Добавил: {c.added_by}{c.created_at ? `, ${fmtDate(c.created_at)}` : ""}</div>}
        </div>
      ))}
    </section>
  );
}
