import { useCallback, useEffect, useRef, useState, type DependencyList } from "react";

export interface Page<T> { rows: T[]; more: boolean }

/**
 * «Бесконечная лента» вместо постраничных кнопок: первая порция грузится сразу, следующие — когда конец списка приближается к экрану.
 * `load(offset, last)` возвращает очередную порцию и признак «есть ещё»; при смене `deps` (фильтры, поиск) список начинается заново.
 * Устаревшие ответы (пока пользователь менял фильтр) отбрасываются.
 */
export function useInfinite<T>(load: (offset: number, last: T | undefined) => Promise<Page<T>>, deps: DependencyList) {
  const [items, setItems] = useState<T[]>([]);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  const gen = useRef(0);
  const el = useRef<HTMLElement | null>(null);
  const state = useRef({ items: [] as T[], loading: false, done: false });
  const loadRef = useRef(load);
  loadRef.current = load;

  const more = useCallback(async () => {
    const s = state.current;
    if (s.loading || s.done) return;
    const g = gen.current;
    s.loading = true;
    setLoading(true);
    try {
      const page = await loadRef.current(s.items.length, s.items[s.items.length - 1]);
      if (g !== gen.current) return;
      s.items = [...s.items, ...page.rows];
      s.done = !page.more || page.rows.length === 0;
      setItems(s.items);
      setDone(s.done);
      setError("");
      // если конец списка всё ещё виден (короткие порции, большой экран) — догружаем сразу, не дожидаясь прокрутки
      window.requestAnimationFrame(() => { const n = el.current; if (n && !s.done && n.getBoundingClientRect().top < window.innerHeight + 600) void more(); });
    } catch (e) {
      if (g === gen.current) { setError((e as Error).message || "Не удалось загрузить список"); s.done = true; setDone(true); }
    } finally {
      if (g === gen.current) { s.loading = false; setLoading(false); }
    }
  }, []);

  const reload = useCallback(() => {
    gen.current += 1;
    state.current = { items: [], loading: false, done: false };
    setItems([]); setDone(false); setError(""); setLoading(false);
    void more();
  }, [more]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, [reload, ...deps]);

  const observer = useRef<IntersectionObserver | null>(null);
  /** Ref для элемента-«сторожа» в конце списка. */
  const sentinel = useCallback((node: HTMLElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    el.current = node;
    if (!node || typeof IntersectionObserver === "undefined") return;
    observer.current = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) void more(); }, { rootMargin: "600px 0px" });
    observer.current.observe(node);
  }, [more]);

  /** Убрать строки из списка локально (после удаления на сервере), не перезагружая ленту. */
  const remove = useCallback((pred: (t: T) => boolean) => {
    state.current.items = state.current.items.filter((t) => !pred(t));
    setItems(state.current.items);
  }, []);

  /** Добавить новые строки в начало ленты (автообновление), не трогая уже загруженное. */
  const prepend = useCallback((rows: T[]) => {
    if (!rows.length) return;
    state.current.items = [...rows, ...state.current.items];
    setItems(state.current.items);
  }, []);

  return { items, loading, done, error, sentinel, reload, more, remove, prepend };
}

/** Подпись под списком: идёт загрузка / всё показано / ошибка. Вместе с `sentinel` образует «конец ленты». */
export function ListFooter({ loading, done, error, empty, count, sentinel }: {
  loading: boolean; done: boolean; error: string; empty: string; count: number; sentinel: (el: HTMLElement | null) => void;
}) {
  return (
    <div ref={sentinel} className="list-footer muted small" aria-live="polite">
      {error ? <span className="field-err">{error}</span> : loading ? "Загрузка…" : done ? (count === 0 ? empty : `Показано всё: ${count}`) : " "}
    </div>
  );
}
