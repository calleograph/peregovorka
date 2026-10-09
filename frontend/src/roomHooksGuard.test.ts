import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/** Охрана от ошибки 0.8.5: хуки React, добавленные в RoomPage ПОСЛЕ раннего `return` (пока `join` ещё null), роняют комнату при первом же входе
 *  («Rendered more hooks than during the previous render»). Юнит-тестов рендера в проекте нет, поэтому проверяем сам исходник: на верхнем уровне компонента
 *  после первого раннего возврата хуков быть не должно. */
describe("RoomPage: порядок хуков", () => {
  const src = readFileSync(resolve(__dirname, "pages/RoomPage.tsx"), "utf8").split("\n");
  const start = src.findIndex((l) => l.startsWith("export default function RoomPage"));
  const body = src.slice(start + 1);
  // первый ранний возврат верхнего уровня компонента: строка с отступом ровно в 2 пробела вида `if (...) {` с return внутри или `if (...) return`
  const firstEarly = body.findIndex((l, i) => /^  if \(!join/.test(l) && (/return/.test(l) || /^\s{4}return/.test(body[i + 1] ?? "")));
  it("ранний возврат найден (иначе тест устарел)", () => { expect(firstEarly).toBeGreaterThan(0); });
  it("после него на верхнем уровне нет useState/useEffect/useCallback/useMemo/useRef/useLayoutEffect", () => {
    const bad = body.slice(firstEarly).map((l, i) => ({ l, n: start + 2 + firstEarly + i })).filter(({ l }) => /^  (const .*= )?use(State|Effect|Callback|Memo|Ref|LayoutEffect|Reducer)[<(]/.test(l));
    expect(bad.map((b) => `строка ${b.n}: ${b.l.trim().slice(0, 80)}`)).toEqual([]);
  });
});
