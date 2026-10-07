"""Логин принимается только по белому списку символов: никаких кавычек, скобок, невидимых и управляющих символов."""
from __future__ import annotations

import pytest

from app.auth.directory import DirectoryError, parse_login


@pytest.mark.parametrize("raw", [
    "al'ice", 'al"ice', "a!b", "a#b", "a$b", "a%b", "a&b", "a`b", "a~b", "a{b}", "a b", "a\tb", "a\nb", "a\x00b",
    "a​b",          # невидимый пробел нулевой ширины
    "a‮b",          # переключение направления текста
    "a b",          # неразрывный пробел
    "CORP$\\alice", "alice@corp test", "alice@corp/test", "..\\..", "a\\b\\c", "alice@", "@corp.test", "a*", "(a)", "a|b", "a=b",
])
def test_unusual_characters_in_login_are_rejected(raw):
    with pytest.raises(DirectoryError):
        parse_login(raw)


@pytest.mark.parametrize("raw", ["иванов.и", "i.ivanov", "ivan_petrov-2", "CORP\\i.ivanov", "ivan@corp-test.local", "Alice"])
def test_normal_logins_are_accepted(raw):
    assert parse_login(raw)[0]


def test_too_long_login_is_rejected():
    with pytest.raises(DirectoryError):
        parse_login("a" * 65)
