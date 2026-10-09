"""Сценарий (см. README.md): чат: вложения (картинка, документ), просмотрщик изображений, ограничения. Два браузера, настоящий LiveKit."""
import asyncio, os, struct, sys, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/pm"
tmp = os.path.join(os.environ.get("SMOKE_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")), "files")
os.makedirs(tmp, exist_ok=True)


def png(path, w=320, h=200):
    raw = b"".join(b"\x00" + bytes(((x * 255 // w), (y * 255 // h), 150)[i % 3] for x in range(w) for i in range(3)) for y in range(h))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


async def attach(c, path):
    doc = await c.send("DOM.getDocument", depth=-1)
    node = await c.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="input[type=file]")
    if not node.get("nodeId"):
        return False
    await c.send("DOM.setFileInputFiles", files=[path], nodeId=node["nodeId"])
    return True


async def main():
    A = S.Client("alice", "alice", "alice-pass", 9451)
    B = S.Client("bob", "bob", "bob-pass", 9452)
    img = os.path.join(tmp, "схема сети.png"); png(img)
    txt = os.path.join(tmp, "заметки.txt"); open(txt, "w", encoding="utf-8").write("Заметки по встрече\n")
    exe = os.path.join(tmp, "вредонос.exe"); open(exe, "wb").write(b"MZ" + b"\0" * 100)
    try:
        for c in (A, B):
            await c.start(); S.check(f"{c.label}: вошёл", await c.join())
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=2", 40) for c in (A, B)])
        await A.click_btn("Чат"); await asyncio.sleep(0.8)
        S.check("Алиса: поле выбора файла есть и принимает изображение", await attach(A, img))
        await asyncio.sleep(1.2)
        await A.js("document.querySelector('textarea[aria-label=\"Текст сообщения\"]').focus()")
        await A.send("Input.insertText", text="Схема во вложении")
        await A.send("Input.dispatchKeyEvent", type="rawKeyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
        await A.send("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
        ok = await B.wait_for("!!document.querySelector('.chat-image img')", 20)
        S.check("Боб: картинка из чата отображается миниатюрой", ok)
        loaded = await B.js("(()=>{const i=document.querySelector('.chat-image img');return !!i&&i.naturalWidth>0})()")
        S.check("миниатюра действительно загрузилась (naturalWidth > 0)", loaded)
        await B.shot("chat-image")
        await B.js("document.querySelector('.chat-image')?.click()")
        S.check("просмотрщик изображений открылся", await B.wait_for("!!document.querySelector('.iv, [class*=iv-]')", 8))
        await B.shot("image-viewer")
        await B.send("Input.dispatchKeyEvent", type="rawKeyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27, nativeVirtualKeyCode=27)
        await B.send("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape", windowsVirtualKeyCode=27, nativeVirtualKeyCode=27)
        S.check("Esc закрывает просмотрщик", await B.wait_for("!document.querySelector('.iv-win, .iv')", 6))
        # документ
        S.check("Алиса: прикрепила текстовый документ", await attach(A, txt)); await asyncio.sleep(3.5)
        await A.js("document.querySelector('textarea[aria-label=\"Текст сообщения\"]').focus()")
        await A.send("Input.insertText", text="Заметки")
        await A.send("Input.dispatchKeyEvent", type="rawKeyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
        await A.send("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
        await asyncio.sleep(1.5); print("ALICE chat text:", (await A.js("document.querySelector('.chat-history, .chat-list, .chat')?.innerText || document.body.innerText.slice(-400)"))[-300:].replace(chr(10), " | "))
        S.check("Боб видит файл-вложение «заметки.txt»", await B.wait_for("document.body.innerText.includes('заметки.txt')", 15))
        # запрещённый тип
        await attach(A, exe); await asyncio.sleep(2.0)
        txtA = await A.js("document.body.innerText")
        S.check("запрещённый тип (.exe) отклонён с понятным сообщением", ("нельзя" in txtA or "не разрешён" in txtA or "не поддерж" in txtA or "тип" in txtA.lower()) and "вредонос.exe" not in (await B.js("document.body.innerText")), "")
        for e in (await A.state())["errs"][:2]:
            print("Алиса ERR:", e)
        print("Боб errs:", (await B.state())["errs"][:3])
    finally:
        for c in (A, B): await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"\nИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
