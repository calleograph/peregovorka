"""Запросы к каталогу (LDAP) без настоящего AD: проверяется, ЧТО именно спрашивает сервер — отключённые учётные записи не предлагаются, состав группы считается с вложенными группами."""
from __future__ import annotations

import pytest

from app.auth.directory import LDAP_MATCHING_RULE_IN_CHAIN, LdapDirectory

DISABLED = "(!(userAccountControl:1.2.840.113556.1.4.803:=2))"


class FakeConn:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.response: list = []

    def search(self, base, flt, **kw):
        self.calls.append((flt, kw))

    def unbind(self):
        pass


@pytest.fixture
def ldap(monkeypatch):
    from app.auth.directory import LdapConfig
    d = LdapDirectory(LdapConfig(ldap_uri_list=["ldaps://dc.example.org"], ldap_base_dn="DC=example,DC=org", ldap_bind_dn="CN=svc,DC=example,DC=org", ldap_ca_file="/nonexistent/ca.pem"))
    conn = FakeConn()
    monkeypatch.setattr(d, "_service_connection", lambda: conn)
    return d, conn


def test_user_search_hides_disabled_accounts_and_escapes_the_query(ldap):
    d, conn = ldap
    d.search("user", "dave*)(uid=*", 20)
    flt = conn.calls[0][0]
    assert DISABLED in flt and "objectCategory=person" in flt
    assert "dave*)(uid=*" not in flt, "ввод пользователя экранируется: инъекция в фильтр невозможна"
    assert conn.calls[0][1]["size_limit"] == 20


def test_group_members_include_nested_groups_and_exclude_disabled(ldap):
    d, conn = ldap
    d.members("CN=Staff,OU=Groups,DC=example,DC=org", 301)
    flt = conn.calls[0][0]
    assert f"memberOf:{LDAP_MATCHING_RULE_IN_CHAIN}:=" in flt and DISABLED in flt, "вложенные группы — по правилу IN_CHAIN, отключённые не входят"
    assert conn.calls[0][1]["size_limit"] == 301
