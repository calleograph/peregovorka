import type { ReactNode } from "react";
import InfoTip from "./InfoTip";

/** Назначение раздела одной строкой рядом с заголовком; подробности, рекомендации и технические пояснения — под значком ⓘ,
 *  чтобы длинный текст не занимал рабочее пространство (открывается мышью, клавиатурой и касанием). */
export default function Lead({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  return <p className="muted lead-line">{summary}<InfoTip label="Подробнее о разделе">{children}</InfoTip></p>;
}
