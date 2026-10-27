import { useEffect, useRef, useState } from "react";
import { setCaptchaHandler, type CaptchaAnswer, type ImageChallenge } from "../captchaPrompt";
import { Modal } from "./Dialogs";

interface Pending { challenge: ImageChallenge; note: string; resolve: (a: CaptchaAnswer | null) => void }

/** Окно «Подтвердите, что вы не робот»: картинка с символами, поле ввода, «обновить картинку». Показывается, когда сервер требует проверку (по порогу неудач или всегда).
 *  Подключается один раз в корне приложения и обслуживает любой запрос, на который сервер ответил 428. */
export default function CaptchaHost() {
  const [cur, setCur] = useState<Pending | null>(null);
  const queue = useRef<Pending | null>(null);
  useEffect(() => {
    setCaptchaHandler((challenge, note) => new Promise<CaptchaAnswer | null>((resolve) => {
      queue.current?.resolve(null);                                  // новая просьба вытесняет прежнюю (повторные запросы не накапливают окна)
      const p = { challenge, note, resolve };
      queue.current = p;
      setCur(p);
    }));
    return () => setCaptchaHandler(null);
  }, []);
  if (!cur) return null;
  const done = (a: CaptchaAnswer | null) => { cur.resolve(a); queue.current = null; setCur(null); };
  return <CaptchaForm key={cur.challenge.id} pending={cur} onDone={done} />;
}

function CaptchaForm({ pending, onDone }: { pending: Pending; onDone: (a: CaptchaAnswer | null) => void }) {
  const { challenge, note } = pending;
  const [answer, setAnswer] = useState("");
  const [img, setImg] = useState(challenge);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const submit = (e: React.FormEvent) => { e.preventDefault(); if (answer.trim()) onDone({ id: img.id, answer: answer.trim() }); };
  const refresh = async () => {
    setBusy(true); setErr("");
    try {
      const r = await fetch("/api/v1/captcha/challenge?scope=login", { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error(r.status === 429 ? "Слишком много запросов. Подождите минуту." : "Не удалось получить новую картинку.");
      const c = (await r.json()) as ImageChallenge;
      if (c.kind !== "image") throw new Error("Сервер выдал другой вид проверки. Повторите вход.");
      setImg(c); setAnswer("");
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <Modal title="Подтвердите, что вы не робот" onClose={() => onDone(null)}>
      <form onSubmit={submit} className="captcha-form">
        <p className="muted small" style={{ margin: 0 }}>Введите символы с картинки. Регистр не важен. Проверка нужна после нескольких неудачных попыток входа.</p>
        {(note || err) && <div className="alert error small" role="alert">{err || note}</div>}
        <div className="captcha-img-row">
          <img src={img.image} alt="Картинка с символами для проверки" width={img.length * 34 + 80} height={84} className="captcha-img" />
          <button type="button" className="btn ghost mini" onClick={refresh} disabled={busy} title="Показать другую картинку" aria-label="Показать другую картинку">↻ Другая</button>
        </div>
        <label>Символы с картинки
          <input value={answer} onChange={(e) => setAnswer(e.target.value)} autoComplete="off" autoCapitalize="characters" spellCheck={false} maxLength={16} inputMode="text" aria-label="Символы с картинки" />
        </label>
        <div className="row">
          <button className="btn primary" type="submit" disabled={!answer.trim() || busy}>Продолжить</button>
          <button className="btn ghost" type="button" onClick={() => onDone(null)}>Отмена</button>
        </div>
      </form>
    </Modal>
  );
}
