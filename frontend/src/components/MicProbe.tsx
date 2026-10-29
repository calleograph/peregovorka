import { useState } from "react";
import { runMicProbe, summarizeMicProbe, type MicProbeReport } from "../micProbe";
import { copyText } from "../util";

/** «Подробная проверка микрофона»: для поддержки и сравнения Edge с приложением Windows. Открывает микрофон несколько раз; звук встречи при этом не прерывается. */
export default function MicProbe() {
  const [busy, setBusy] = useState(false);
  const [rep, setRep] = useState<MicProbeReport | null>(null);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);
  const run = async () => {
    setBusy(true); setErr(""); setCopied(false);
    try { setRep(await runMicProbe()); } catch (e) { setErr(`Проверка не выполнена: ${(e as Error)?.message ?? e}`); } finally { setBusy(false); }
  };
  return (
    <details className="mic-probe">
      <summary>Для поддержки: подробная проверка микрофона</summary>
      <button type="button" className="btn mini ghost" onClick={() => void run()} disabled={busy}
              title="Несколько раз открывает микрофон и показывает время, параметры и ошибки. Один и тот же отчёт можно снять в Edge и в приложении и сравнить">
        {busy ? "Проверяем…" : "Подробная проверка микрофона"}
      </button>
      {err && <p className="field-err" role="alert">{err}</p>}
      {rep && (
        <div className="probe-out" role="status">
          <ul className="small">{summarizeMicProbe(rep).map((l, i) => <li key={i}>{l}</li>)}</ul>
          <button type="button" className="btn mini" onClick={() => void copyText(JSON.stringify(rep, null, 2)).then((ok) => setCopied(ok))}>{copied ? "Скопировано" : "Скопировать полный отчёт"}</button>
        </div>
      )}
    </details>
  );
}
