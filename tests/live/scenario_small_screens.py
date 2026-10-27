"""Сценарий (см. README.md): маленькие экраны. Обходит страницы и разделы администрирования на ширинах 360/390/768/1280 и проверяет, что нет горизонтальной прокрутки страницы,
нет внутренних блоков с горизонтальным «ползунком» (таблицы должны превращаться в карточки/сжиматься), ничего не вылезает за правый край. Снимки — в SMOKE_OUT.
Календарь проверяет scenario_calendar_visual.py. Запуск: PEREGOVORKA_URL=… python scenario_small_screens.py [быстро]. Не проверяется: реальные телефоны (эмуляция размера окна)."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

PAGES = ["/", "/calendar", "/history", "/profile"]
ADMIN = ["system", "system_tech", "performance", "clients", "meetings", "recordings", "protocol", "templates", "rooms", "users", "login_access", "access", "profile_policy", "ldap",
         "ca", "mail", "asr", "llm", "anon", "storages", "outlook", "sip", "sip_policy", "bitrix24", "public_api", "desktop_signing", "auth_security", "login_protection", "captcha",
         "net_trust", "crypto", "audit", "storage", "audio_storage", "chats", "chat_files", "storage_sync", "restore", "journal", "journal_settings", "mail_log", "network", "updates",
         "config_backup", "general", "site", "privacy", "screen", "mail_policy", "mail_templates"]
WIDTHS = [(360, 740), (390, 844), (768, 1024), (1280, 800)]

PROBE = r"""(() => {
  const W = innerWidth, out = {page: document.documentElement.scrollWidth - W, inner: [], beyond: []};
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  for (const e of document.querySelectorAll('body *')) {
    if (!vis(e)) continue;
    const cs = getComputedStyle(e);
    if ((cs.overflowX === 'auto' || cs.overflowX === 'scroll') && e.scrollWidth > e.clientWidth + 2 && e.clientWidth > 40) {
      out.inner.push((e.className && String(e.className).slice(0, 40)) || e.tagName.toLowerCase());
    }
    const r = e.getBoundingClientRect();
    if (r.right > W + 2 && cs.position !== 'fixed' && !e.closest('[aria-hidden=true]') && out.beyond.length < 5) {
      let p = e.parentElement, clipped = false;
      while (p && p !== document.body) { const o = getComputedStyle(p).overflowX; if (o !== 'visible') { clipped = true; break; } p = p.parentElement; }
      if (!clipped) out.beyond.push((e.className && String(e.className).slice(0, 40)) || e.tagName.toLowerCase());
    }
  }
  out.inner = [...new Set(out.inner)].slice(0, 6); out.beyond = [...new Set(out.beyond)];
  return out;
})()"""


async def main():
    quick = "быстро" in sys.argv
    A = S.Client("A", "root", "root-pass", 9831)
    bad = []
    try:
        await A.start()
        only = [a[4:] for a in sys.argv[1:] if a.startswith("tab=")]          # python scenario_small_screens.py tab=journal tab=users — только эти разделы
        widths = WIDTHS[:2] if quick else WIDTHS
        for w, h in widths:
            await A.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=(w < 500))
            for path in ([] if only else PAGES) + [f"/admin?tab={t}" for t in (only or (ADMIN[:6] if quick else ADMIN))]:
                await A.goto(path, 1.0)
                await A.wait_for("document.body.innerText.length > 40", 8)
                r = await A.js(PROBE)
                ok = r["page"] <= 1 and not r["inner"] and not r["beyond"]
                S.check(f"{w}px {path}: без горизонтальной прокрутки", ok, "" if ok else json.dumps(r, ensure_ascii=False))
                if not ok:
                    bad.append((w, path))
                    await A.shot(f"{w}-{path.strip('/').replace('/', '_').replace('?tab=', '_') or 'home'}")
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:3]))
    finally:
        await A.close()
    bads = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bads)} из {len(S.results)}; не прошли: {len(bads)}")


asyncio.run(main())
