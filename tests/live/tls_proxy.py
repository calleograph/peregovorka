"""TLS-прокси для испытаний установленных приложений (они ходят только по HTTPS/WSS): python3 tls_proxy.py <сертификат> <ключ> 8443=5183 7443=7880 …
Каждая пара «порт TLS = порт стенда» на 127.0.0.1: TLS снимается, байты идут как есть (HTTP, WebSocket, сигнальный канал LiveKit).
Сертификат — от тестового центра сертификации стенда (его доверяет только испытательная система)."""
import asyncio, ssl, sys


async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data); await w.drain()
    except Exception:
        pass
    finally:
        try: w.close()
        except Exception: pass


def handler(target):
    async def h(cr, cw):
        try:
            tr, tw = await asyncio.open_connection("127.0.0.1", target)
        except OSError:
            cw.close(); return
        await asyncio.gather(pipe(cr, tw), pipe(tr, cw))
    return h


async def main():
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(sys.argv[1], sys.argv[2])
    servers = []
    for pair in sys.argv[3:]:
        src, dst = map(int, pair.split("="))
        servers.append(await asyncio.start_server(handler(dst), "127.0.0.1", src, ssl=ctx))
        print(f"TLS 127.0.0.1:{src} → 127.0.0.1:{dst}", flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servers))

asyncio.run(main())
