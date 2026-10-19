import { ROOM_ICONS, type RoomIconDef } from "./icons";

const norm = (s: string) => s.toLowerCase().replace(/ё/g, "е");

/** Поиск по названию и по словам-подсказкам; категория — необязательный фильтр. */
export function findIcons(q: string, category = "", all: RoomIconDef[] = ROOM_ICONS): RoomIconDef[] {
  const words = norm(q).split(/\s+/).filter(Boolean);
  return all.filter((i) => (!category || i.category === category) && words.every((w) => norm(`${i.name} ${i.words} ${i.key}`).includes(w)));
}

export const iconByKey = (key: string | null | undefined): RoomIconDef | undefined => (key ? ROOM_ICONS.find((i) => i.key === key) : undefined);
