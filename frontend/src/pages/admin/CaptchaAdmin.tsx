import { useEffect, useRef, useState } from "react";
import { api, type ApiError, type CaptchaDiag, type CaptchaTestVerdict } from "../../api";
import { solveCaptcha } from "../../captcha";
import { CaptchaImageField } from "../../components/CaptchaDialog";
import { Modal } from "../../components/Dialogs";
import type { ImageChallenge } from "../../captchaPrompt";
import SettingsForm from "./SettingsForm";
import { captchaFields } from "./fields";

const MODE: Record<string, string> = { off: "выключена", after_failures: "после серии неудач", always: "всегда" };

/** «CAPTCHA»: настройки и кнопка «Проверить CAPTCHA» — тестовое окно с тем же компонентом и той же серверной проверкой, что на странице входа. */
export default function CaptchaAdmin({ intro }: { intro: string }) {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  return (
    <>
      <SettingsForm key="captcha" group="captcha" title="CAPTCHA (проверка «я не робот»)" fields={captchaFields} intro={intro} />
      <div className="card">
        <div className="row"><h2>Проверка работы</h2><div className="spacer" /><button ref={btn} className="btn primary" onClick={() => setOpen(true)}>Проверить CAPTCHA</button></div>
        <p className="muted small" style={{ margin: "4px 0 0" }}>Откроется настоящая проверка с текущими настройками: можно увидеть картинку, пройти её и получить ответ сервера. Попытки не считаются неудачными входами и вход не блокируют.</p>
      </div>
      {open && <CaptchaTestDialog onClose={() => setOpen(false)} />}
    </>
  );
}

function CaptchaTestDialog({ onClose }: { onClose: () => void }) {
  const [start, setStart] = useState<{ challenge: ImageChallenge | { kind: "pow" }; diag: CaptchaDiag } | null>(null);
  const [diag, setDiag] = useState<CaptchaDiag | null>(null);
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [verdict, setVerdict] = useState<CaptchaTestVerdict | null>(null);
  const [err, setErr] = useState("");
  const load = async (keep?: CaptchaTestVerdict) => {
    setBusy(true); setErr(""); setAnswer(""); setVerdict(keep ?? null);
    try {
      const r = await api.admin.captchaTestChallenge();
      setDiag(r.diag); setStart(r as never);
    } catch (e) {
      const ex = e as ApiError;
      setErr(ex.message); setStart(null);
      if (ex.extra?.diag) setDiag(ex.extra.diag as CaptchaDiag);             // почему не работает: хранилище, режим, шрифт
    } finally { setBusy(false); }
  };
  useEffect(() => { void load(); }, []);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!start) return;
    setBusy(true); setErr("");
    try {
      const ch = start.challenge;
      const body = ch.kind === "image" ? { id: ch.id, answer: answer.trim() } : { solution: await solveCaptcha(ch as never) };
      const v = await api.admin.captchaTestVerify(body);
      setStart(null);                                          // задача одноразовая: после проверки нужна новая
      if (v.ok) setVerdict(v); else await load(v);             // после отказа сразу показываем новую картинку, ответ сервера остаётся на виду
    } catch (e2) { setErr((e2 as ApiError).message); } finally { setBusy(false); }
  };
  const img = start?.challenge.kind === "image" ? (start.challenge as ImageChallenge) : null;
  return (
    <Modal title="Проверка CAPTCHA" onClose={onClose}>
      <form onSubmit={submit} className="captcha-form">
        {err && <div className="alert error small" role="alert">{err}</div>}
        {verdict && <div className={`alert ${verdict.ok ? "ok" : "error"} small`} role="status"><strong>{verdict.message}</strong></div>}
        {img && <CaptchaImageField img={img} answer={answer} setAnswer={setAnswer} onRefresh={() => void load()} busy={busy} />}
        {start && !img && <p className="muted small" style={{ margin: 0 }}>Выбрана вычислительная проверка: браузер решит её сам при нажатии «Проверить».</p>}
        {diag && (
          <table className="table small"><tbody>
            <tr><th>Режим</th><td>{MODE[diag.mode] ?? diag.mode}{diag.mode === "after_failures" ? ` — после ${diag.failures} неудач за ${diag.window_minutes} мин` : ""}</td></tr>
            <tr><th>Вид</th><td>{diag.kind === "image" ? `картинка, ${diag.length} символов` : "вычислительная (доказательство работы)"}</td></tr>
            <tr><th>Срок жизни</th><td>{diag.ttl_seconds} с, одна попытка на задачу</td></tr>
            <tr><th>Хранилище задач</th><td>{diag.redis ? "работает" : `недоступно${diag.redis_error ? ` (${diag.redis_error})` : ""}`}</td></tr>
            {diag.font && <tr><th>Шрифт картинки</th><td>{diag.font}</td></tr>}
            <tr><th>Где применяется</th><td>{diag.scopes.join(", ")}</td></tr>
          </tbody></table>
        )}
        <div className="row">
          {start && <button className="btn primary" type="submit" disabled={busy || (!!img && !answer.trim())}>Проверить</button>}
          {!start && <button className="btn primary" type="button" onClick={() => void load()} disabled={busy}>{verdict ? "Проверить ещё раз" : "Повторить"}</button>}
          <button className="btn ghost" type="button" onClick={onClose}>Закрыть</button>
        </div>
      </form>
    </Modal>
  );
}
