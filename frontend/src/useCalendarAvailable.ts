import { useEffect, useState } from "react";
import { calendarApi } from "./calendarApi";

let cached: boolean | null = null;

/** Показывать ли пункт «Календарь»: один лёгкий запрос на сеанс (раздел включает администратор, сотрудник должен входить в разрешённую группу). Ошибка — пункта нет. */
export function useCalendarAvailable(): boolean {
  const [on, setOn] = useState<boolean>(cached ?? false);
  useEffect(() => {
    if (cached !== null) return;
    let alive = true;
    calendarApi.status().then((s) => { cached = s.available; if (alive) setOn(s.available); }).catch(() => { cached = false; });
    return () => { alive = false; };
  }, []);
  return on;
}
