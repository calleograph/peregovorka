"""Пути к файлам: ни один пользовательский компонент не выводит за каталог хранилища ни на Linux, ни на SMB (Windows); документация API по умолчанию закрыта."""
from __future__ import annotations

import pytest

from app.config import Settings
from app.services.storage import LocalStorage, StorageError, _check_rel, meeting_relpath, safe_component
from datetime import datetime

BAD = ["../x", "a/../../x", "/etc/passwd", "a\\..\\..\\x", "..\\x", "C:/Windows/x", "a/b:stream", "a/\x00b", ""]


@pytest.mark.parametrize("rel", BAD)
def test_check_rel_rejects_traversal_absolute_backslash_colon_and_nul(rel):
    with pytest.raises(StorageError):
        _check_rel(rel)


def test_normal_paths_produced_by_the_application_pass():
    rel = meeting_relpath('Комната: "Север" / 1\\2', datetime(2026, 10, 9, 14, 30))
    assert _check_rel(rel)
    assert ":" not in rel and "\\" not in rel
    assert safe_component("..\\..\\x") not in ("..", "")
    _check_rel("Протоколы/2026-10-09, Пт/14-30/protocol.docx")


def test_local_storage_cannot_escape_root_even_through_a_symlink(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("символические ссылки недоступны на этой системе")
    st = LocalStorage(str(root))
    with pytest.raises(StorageError):
        st.read_bytes("link/secret.txt")
    with pytest.raises(StorageError):
        st.write_bytes("link/new.txt", b"x")
    assert not (outside / "new.txt").exists()


def test_internal_api_documentation_is_closed_by_default():
    assert Settings.model_fields["docs_enabled"].default is False
