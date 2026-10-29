"""SMB: замена существующего файла, которую сервер отклоняет (STATUS_ACCESS_DENIED) — копия всё равно ложится на место, а настоящая ошибка не скрывается."""
from __future__ import annotations

import pytest

from app.services.storage import SmbStorage


class FakeSmb:
    def __init__(self, deny_replace: bool):
        self.files: dict[str, bytes] = {}
        self.deny = deny_replace
        self.path = self

    def exists(self, p):
        return p in self.files

    def replace(self, src, dst):
        if self.deny and dst in self.files:
            raise OSError("STATUS_ACCESS_DENIED")
        self.files[dst] = self.files.pop(src)

    def remove(self, p):
        del self.files[p]

    def rename(self, src, dst):
        assert dst not in self.files
        self.files[dst] = self.files.pop(src)


@pytest.mark.parametrize("deny", [False, True])
def test_replace_falls_back_to_remove_and_rename_when_the_server_refuses_to_overwrite(deny):
    smb = FakeSmb(deny)
    smb.files = {"old": b"old", "tmp": b"new"}
    SmbStorage._replace(smb, "tmp", "old")
    assert smb.files == {"old": b"new"}


def test_replace_error_without_an_existing_target_is_not_hidden():
    smb = FakeSmb(True)
    smb.files = {}
    with pytest.raises(KeyError):                       # исходника нет — исключение доходит до вызывающего (он сообщит «не удалось записать»)
        SmbStorage._replace(smb, "tmp", "new")
