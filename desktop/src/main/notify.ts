// Уведомления Windows (центр уведомлений; системный режим «Не беспокоить»/фокусировки Windows применяет сама). Одно событие — одно уведомление
// (ключ: организация + метка), нажатие открывает нужную организацию и раздел. Настройки: выключить, без звука, скрыть содержимое, «Не беспокоить».
import { Notification, nativeImage } from "electron";
import { join } from "node:path";
import { Dedupe, shapeNotification } from "../common/dedupe";
import type { Ctx } from "./ctx";
import type { Org } from "./orgs";

export class Notifier {
  private dedupe = new Dedupe();
  private live = new Set<Notification>();         // держим ссылки: иначе сборщик мусора теряет обработчик нажатия
  constructor(private ctx: Ctx, private onClick: (org: Org, route?: string) => void) {}

  /** `multi` — подключено несколько организаций: название организации добавляется к тексту, чтобы было понятно, откуда событие. */
  show(org: Org, n: { tag: string; title: string; body?: string; route?: string; kind: string }, multi: boolean): boolean {
    if (!this.dedupe.fresh(`${org.rec.id}:${n.tag}`)) return false;
    this.ctx.log.info("notify.accepted", { org: org.rec.id, kind: n.kind });
    const shaped = shapeNotification(n, this.ctx.eff().notifications);
    if (!shaped) { this.ctx.log.info("notify.suppressed", { org: org.rec.id }); return false; }
    if (!Notification.isSupported()) { this.ctx.log.warn("notify.unsupported"); return false; }
    const icon = org.rec.icon ? nativeImage.createFromDataURL(org.rec.icon) : nativeImage.createFromPath(join(__dirname, "assets", "icon.png"));
    const note = new Notification({ title: shaped.title, body: [shaped.body, multi ? org.rec.name : ""].filter(Boolean).join("\n"), silent: shaped.silent,
      icon: icon.isEmpty() ? undefined : icon, timeoutType: "default" });
    note.on("click", () => { this.live.delete(note); this.onClick(org, n.route); });
    note.on("close", () => this.live.delete(note));
    this.live.add(note);
    if (this.live.size > 50) for (const x of [...this.live].slice(0, this.live.size - 50)) this.live.delete(x);
    note.show();
    this.ctx.log.info("notify.shown", { org: org.rec.id, kind: n.kind });
    return true;
  }
}
