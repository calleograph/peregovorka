import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

/** Короткое подтверждение внизу экрана («Ссылка скопирована»): исчезает само, читается скринридерами (aria-live). */
export function useToast(ms = 2200): [ReactNode, (text: string, tone?: "ok" | "error") => void] {
  const [t, setT] = useState<{ text: string; tone: "ok" | "error"; n: number } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = useCallback((text: string, tone: "ok" | "error" = "ok") => {
    window.clearTimeout(timer.current);
    setT((cur) => ({ text, tone, n: (cur?.n ?? 0) + 1 }));
    timer.current = window.setTimeout(() => setT(null), ms);
  }, [ms]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return [<div className="toast-host" role="status" aria-live="polite">{t && <div key={t.n} className={`toast ${t.tone}`}>{t.text}</div>}</div>, show];
}
