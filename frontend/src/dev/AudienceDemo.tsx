import { useEffect, useMemo, useState } from "react";
import AudiencePanel from "../components/room/AudiencePanel";
import { splitStage, type AudiencePerson } from "../presentation";

/**
 * ТОЛЬКО для разработки (маршрут /__dev/audience в `npm run dev`, в сборку для сервера не попадает): список зрителей презентации на синтетических данных —
 * без единого WebRTC-соединения. Проверяет, что тысяча зрителей не создаёт тысячу элементов и что выдача/отзыв слова переносят человека между сценой и списком.
 */
const NAMES = ["Иванов", "Петров", "Сидорова", "Кузнецов", "Смирнова", "Попов", "Васильева", "Соколов", "Михайлова", "Новиков"];

export default function AudienceDemo() {
  const n = Math.max(1, Math.min(5000, Number(new URLSearchParams(window.location.search).get("n")) || 1000));
  const [people, setPeople] = useState<AudiencePerson[]>(() => Array.from({ length: n }, (_, i) => ({
    identity: `u-${i}`, name: i === 0 ? "Руководитель Главный" : `${NAMES[i % 10]} ${["Анна", "Борис", "Вера", "Глеб"][i % 4]} ${i}`, local: false,
    mic: i === 0, cam: false, screen: false, leader: i === 0, floor: false, hand: i % 97 === 0 && i > 0, handOrder: i % 97 === 0 ? Math.floor(i / 97) : undefined,
  })));
  const [granted, setGranted] = useState<string[]>([]);
  const split = useMemo(() => splitStage(people, true), [people]);
  useEffect(() => {
    (window as unknown as { __demo: unknown }).__demo = {
      grant: (id: string) => { setPeople((cur) => cur.map((p) => (p.identity === id ? { ...p, floor: true } : p))); setGranted((g) => [...g, id]); },
      revoke: (id: string) => { setPeople((cur) => cur.map((p) => (p.identity === id ? { ...p, floor: false } : p))); setGranted((g) => g.filter((x) => x !== id)); },
      join: (count: number) => setPeople((cur) => [...cur, ...Array.from({ length: count }, (_, i) => ({ identity: `n-${cur.length + i}`, name: `Новый ${cur.length + i}`, local: false, mic: false, cam: false, screen: false }))]),
      rows: () => document.querySelectorAll(".aud-row").length,
      total: () => people.length,
    };
  }, [people]);
  return (
    <div style={{ position: "relative", height: "80vh", margin: 16, border: "1px dashed var(--line-strong)" }}>
      <p className="muted small" style={{ margin: 8 }} id="demo-info">Синтетическая комната: {people.length} людей; на сцене {split.stage.length}; у слова: {granted.join(", ") || "никого"}</p>
      <ul id="demo-stage">{split.stage.map((p) => <li key={p.identity} className="tile" data-identity={p.identity}>{p.name}</li>)}</ul>
      <AudiencePanel people={split.audience} canManage onClose={() => undefined}
                     onGrant={(p) => (window as unknown as { __demo: { grant: (id: string) => void } }).__demo.grant(p.identity)} onLowerHand={() => undefined} onCard={() => undefined} />
    </div>
  );
}
