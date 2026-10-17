"""Сценарий (см. README.md): шумоподавление (выкл / стандартное / усиленное RNNoise) и адаптивное качество видео на настоящем LiveKit.

Проверяется: режимы переключаются из меню «Ещё»; в «усиленном» режиме в WebRTC уходит обработанный трек (MediaStreamAudioDestinationNode), звук у собеседника продолжает идти;
возврат на стандартный; ручной уровень видео меняет параметры кодировщика (битрейт верхнего слоя); «Только звук» выключает камеру, звук идёт, а при «автоматически» индикатор обновляется.
Не проверяется: качество подавления на настоящей речи (на слух), автоматическое снижение при реально плохой сети.
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/sales"

AUDIO_LABELS = """(() => window.__pcs.flatMap(pc => pc.getSenders()).filter(s => s.track && s.track.kind === 'audio').map(s => s.track.label))()"""
VIDEO_TOP = """(() => { const out = []; for (const pc of window.__pcs) for (const s of pc.getSenders()) { if (s.track && s.track.kind === 'video') { const p = s.getParameters(); out.push((p.encodings || []).map(e => ({ rid: e.rid, br: e.maxBitrate, fps: e.maxFramerate, sc: e.scaleResolutionDownBy }))); } } return out; })()"""


async def close_menu(c):
    for t in ("keyDown", "keyUp"):
        await c.send("Input.dispatchKeyEvent", type=t, key="Escape", code="Escape", windowsVirtualKeyCode=27)
    await asyncio.sleep(0.3)


async def menu_pick(c, text_part):
    """«Ещё» → пункт меню по части названия."""
    await close_menu(c)
    ok = await c.click_btn("Ещё")
    await asyncio.sleep(0.4)
    clicked = await c.js(f"(() => {{ const b = [...document.querySelectorAll('[role=menuitem]')].find(x => x.textContent.includes({text_part!r})); if (!b || b.disabled) return false; b.click(); return true; }})()")
    await asyncio.sleep(0.6)
    return bool(ok and clicked)


async def menu_labels(c):
    await close_menu(c)
    await c.click_btn("Ещё")
    await asyncio.sleep(0.4)
    labels = await c.js("[...document.querySelectorAll('[role=menuitem]')].map(x => x.textContent.trim())")
    await close_menu(c)
    return labels or []


async def main():
    A = S.Client("alice", "alice", "alice-pass", 9495)
    B = S.Client("bob", "bob", "bob-pass", 9496)
    try:
        for c in (A, B):
            await c.start(); S.check(f"{c.label}: вошёл", await c.join())
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=2", 40) for c in (A, B)])
        await asyncio.sleep(3)

        labels = await menu_labels(A)
        S.check("в меню есть «Шумоподавление: стандартное» (режим по умолчанию)", any("Шумоподавление: стандартное" in x for x in labels), str([x for x in labels if "Шум" in x]))
        S.check("в меню есть пункты «Качество видео»", sum("Качество видео" in x for x in labels) >= 6)

        # --- усиленное шумоподавление
        before = (await B.js(S.STATS))["ain"]
        S.check("Алиса: переключила на «усиленное»", await menu_pick(A, "Шумоподавление: стандартное"))
        await asyncio.sleep(4)
        labs = await menu_labels(A)
        S.check("режим теперь «усиленное» (или откат на стандартное с уведомлением)", any("Шумоподавление: усиленное" in x for x in labs) or any("Шумоподавление: стандартное" in x for x in labs), str([x for x in labs if "Шум" in x]))
        enhanced = any("Шумоподавление: усиленное" in x for x in labs)
        names = await A.js(AUDIO_LABELS)
        S.check("в «усиленном» режиме в WebRTC уходит обработанный трек RNNoise", (not enhanced) or any("MediaStreamAudioDestinationNode" in (n or "") for n in names), f"enhanced={enhanced} labels={names}")
        await asyncio.sleep(3)
        after = (await B.js(S.STATS))["ain"]
        S.check("у Боба входящее аудио от Алисы продолжает идти после переключения", after > before + 20, f"{before}->{after}")
        await A.shot("noise-enhanced")

        # --- выкл → стандартное
        S.check("Алиса: следующее нажатие переключает на «выключено»", await menu_pick(A, "Шумоподавление:"))
        await asyncio.sleep(2)
        labs = await menu_labels(A)
        S.check("режим «выключено», обработчик снят", any("Шумоподавление: выключено" in x for x in labs) and not any("MediaStreamAudioDestinationNode" in (n or "") for n in await A.js(AUDIO_LABELS)))
        S.check("Алиса: снова «стандартное»", await menu_pick(A, "Шумоподавление:"))
        await asyncio.sleep(2)
        labs = await menu_labels(A)
        S.check("режим «стандартное»", any("Шумоподавление: стандартное" in x for x in labs), str([x for x in labs if "Шум" in x]))

        # --- качество видео
        S.check("Алиса: включила камеру", await A.click_btn("Камера выкл."))
        ALICE_VIDEO = "(()=>{const t=[...document.querySelectorAll('.tile')].find(x=>(x.getAttribute('title')||'').includes('Алиса'));return !!t&&!t.querySelector('.avatar')&&!!t.querySelector('video')})()"
        ALICE_AVATAR = "(()=>{const t=[...document.querySelectorAll('.tile')].find(x=>(x.getAttribute('title')||'').includes('Алиса'));return !!t&&!!t.querySelector('.avatar')})()"
        S.check("у Боба видео Алисы", await B.wait_for(ALICE_VIDEO, 25))
        await asyncio.sleep(3)
        enc0 = await A.js(VIDEO_TOP)
        S.check("до выбора уровня у кодировщика камеры есть слои", bool(enc0) and len(enc0[0]) >= 1, str(enc0))
        S.check("Алиса: уровень «Качество видео: низкое»", await menu_pick(A, "Качество видео: низкое"))
        await asyncio.sleep(1.5)
        enc = await A.js(VIDEO_TOP)
        top = max(enc[0], key=lambda e: e.get("br") or 0) if enc and enc[0] else {}
        S.check("битрейт верхнего слоя камеры ограничен уровнем «низкое» (≤ 250 кбит/с)", 0 < (top.get("br") or 0) <= 250_000, str(enc))
        badge = await A.js("[...document.querySelectorAll('.room-head .badge')].map(b => b.textContent.trim())")
        S.check("в шапке индикатор «Видео: низкое»", any("Видео: низкое" in b for b in badge), str(badge))

        before = (await B.js(S.STATS))["ain"]
        S.check("Алиса: уровень «Только звук»", await menu_pick(A, "Качество видео: только звук"))
        await asyncio.sleep(3)
        S.check("камера Алисы выключена (у Боба вместо видео аватар)", await B.wait_for(ALICE_AVATAR, 15))
        await asyncio.sleep(2)
        after = (await B.js(S.STATS))["ain"]
        S.check("звук при «Только звук» продолжает идти", after > before + 20, f"{before}->{after}")
        badge = await A.js("[...document.querySelectorAll('.room-head .badge')].map(b => b.textContent.trim())")
        S.check("индикатор «Видео: только звук»", any("только звук" in b.lower() for b in badge), str(badge))
        await A.shot("quality-audio-only")

        S.check("Алиса: «Качество видео: автоматически»", await menu_pick(A, "Качество видео: автоматически"))
        labs = await menu_labels(A)
        S.check("автоматический режим отмечен", any("✓ Качество видео: автоматически" in x for x in labs), str([x for x in labs if "Качество" in x]))
        print("errs:", (await A.state())["errs"][:3], (await B.state())["errs"][:3])
        S.check("ошибок страницы нет", not (await A.state())["errs"] and not (await B.state())["errs"])
    finally:
        for c in (A, B): await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
