import { expect, it } from "vitest";
import { stepNet, type NetRaw, type NetShown } from "./tileNet";

it("нагрузка индикатора связи: пересчёт 100 плиток раз в 2 с — доли миллисекунды", () => {
  const raws: NetRaw[] = ["excellent", "good", "poor", "lost", "unknown"];
  const map = new Map<string, NetShown>();
  const ticks = 1000, n = 100;
  const t0 = performance.now();
  for (let k = 0; k < ticks; k++) for (let i = 0; i < n; i++) map.set(`u-${i}`, stepNet(map.get(`u-${i}`), raws[(i + (k >> 4)) % 5], k * 2000));
  const perTick = (performance.now() - t0) / ticks;
  console.log(`[замер] пересчёт индикаторов 100 участников: ${(perTick * 1000).toFixed(1)} мкс за тик (раз в 2 с)`);
  expect(perTick).toBeLessThan(2);
});
