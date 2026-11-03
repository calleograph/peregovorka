import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";

// Внутри формы редактора комнаты не должно быть других <form>: вложенная форма в HTML недопустима, кнопка «Добавить» площадки отправляла
// её штатно браузером (GET на тот же адрес — перезагрузка, предупреждение о потере данных, площадка не сохранялась). Внутри — components/SubForm.tsx.
const ROOT = join(__dirname);

function localImports(file: string): string[] {
  const src = readFileSync(file, "utf8");
  return [...src.matchAll(/from "(\.{1,2}\/[^"]+)"/g)].map((m) => {
    const base = join(dirname(file), m[1]);
    return [`${base}.tsx`, `${base}.ts`, join(base, "index.tsx")].find(existsSync) ?? "";
  }).filter((f) => f.endsWith(".tsx"));
}

describe("формы внутри редактора комнаты", () => {
  it("ни один компонент, вложенный в RoomEditor, не рисует свой <form>", () => {
    const start = join(ROOT, "components", "RoomEditor.tsx");
    const seen = new Set<string>([start]);
    const queue = localImports(start);
    const bad: string[] = [];
    while (queue.length) {
      const f = queue.shift()!;
      if (seen.has(f)) continue;
      seen.add(f);
      if (/<form[\s>]/.test(readFileSync(f, "utf8"))) bad.push(f.slice(ROOT.length + 1));
      queue.push(...localImports(f));
    }
    expect(seen.size).toBeGreaterThan(5);
    expect(bad).toEqual([]);
  });
});
