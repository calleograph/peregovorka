"""Включает/выключает защитный слой на стенде перед живыми сценариями (чаты, комната, гости, LiveKit): `security_stand.py on|off`.
on: WAF в режиме «Защита» со всеми правилами в «Блок» (худший случай для ложных срабатываний) + ограничение по подсетям (разрешён адрес стенда) с подтверждением."""
import http.cookiejar, json, os, sys, urllib.request

A = os.environ.get("PEREGOVORKA_URL", "http://127.0.0.1:5180")
HDR = {"Content-Type": "application/json", "Origin": A}


def main(mode):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    ch = json.load(op.open(urllib.request.Request(A + "/api/v1/captcha/challenge", headers=HDR)))
    cap = {"X-Captcha-Id": ch["id"], "X-Captcha-Answer": "AAAAA"} if ch.get("kind") == "image" else {}
    csrf = json.load(op.open(urllib.request.Request(A + "/api/v1/auth/login", data=json.dumps({"login": "root", "password": "root-pass"}).encode(), headers={**HDR, **cap})))["csrf_token"]

    def call(method, path, body=None):
        r = op.open(urllib.request.Request(A + "/api/v1" + path, method=method, data=None if body is None else json.dumps(body).encode(), headers={**HDR, "X-CSRF-Token": csrf}))
        return json.load(r) if r.status != 204 else None
    if mode == "on":
        rules = {k: "block" for k in ("path_traversal", "bad_method", "scanner_path", "sqli_query", "xss_query", "cmd_query", "bot_ua", "api_probe")}
        call("PUT", "/admin/security/waf", {"mode": "protect", "rules": rules})
        st = call("GET", "/admin/security/ip-access")
        have = {e["cidr"] for e in st["entries"]}
        for c in ("127.0.0.1/32", "::1/128"):
            if c not in have:
                call("POST", "/admin/security/ip-access/entries", {"cidr": c, "comment": "стенд"})
        call("PUT", "/admin/security/ip-access/settings", {"enabled": True, "exempt_guests": False})
        call("POST", "/admin/security/ip-access/confirm")
    else:
        call("PUT", "/admin/security/waf", {"mode": "off"})
        call("PUT", "/admin/security/ip-access/settings", {"enabled": False})
    st = call("GET", "/admin/security/ip-access")
    print(json.dumps({"ip_access_active": st["active"], "entries": len(st["entries"]), "my_ip": st["my_ip"]}))
    try:
        call("POST", "/auth/logout")
    except Exception:
        pass


main(sys.argv[1] if len(sys.argv) > 1 else "on")
