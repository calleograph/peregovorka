"""Проверка на утечку частной конфигурации: находит чужие домены, адреса и IP, пропускает универсальные примеры и документационные адреса."""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_private_data", ROOT / "scripts" / "check_private_data.py")
chk = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
spec.loader.exec_module(chk)                         # type: ignore[union-attr]
ALLOWED, IPS = chk.load_allow()


def scan(text: str, deny: list[str] | None = None) -> list[str]:
    return chk.scan_text("x.txt", text, ALLOWED, IPS, [re.compile(d, re.I) for d in deny or []])


def test_generic_examples_pass():
    ok = "https://api.example.com/v1 llm.example.org admin@example.local ldaps://dc1.example.local:636 host.corp.test 127.0.0.1 192.0.2.10 DOMAIN\\user model-x"
    assert scan(ok) == []


def test_private_domains_hosts_emails_and_ips_are_caught():
    bad = scan("base_url=https://llm.internal-corp.ru/v1 ldaps://ad1.company.lan:636 ivan@company.ru server 192.168.10.5 gateway api.some-llm.ai")
    joined = " ".join(bad)
    for needle in ("llm.internal-corp.ru", "ad1.company.lan", "company.ru", "192.168.10.5", "api.some-llm.ai"):
        assert needle in joined, (needle, bad)


def test_operator_can_add_own_deny_patterns_outside_the_repository():
    assert scan("название организации ACME-Corp", deny=[r"acme-corp"])


def test_current_repository_tree_is_clean():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_private_data.py")], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout[-2000:]
