"""Качество камеры по умолчанию в комнате и дорожка «низкое качество»: значения, список полей руководителя, 360p не предлагает своё же перекодирование."""
import pytest
from pydantic import ValidationError

from app.api.clientcfg import build_client_config  # noqa: F401  (поле попадает в конфиг клиента)
from app.api.room_manage import LEADER_FIELDS, RoomManagePatch
from app.api.schemas import ClientConfig, RoomCreateIn
from app.models import Room
from app.services.live import LIVE_QUALITY, low_useful


def test_default_is_minimum_for_new_rooms_and_for_the_client_config():
    assert Room.__table__.c.camera_quality.default.arg == "min" and Room.__table__.c.camera_quality.server_default.arg == "min"
    assert ClientConfig.model_fields["camera_quality"].default == "min"
    assert RoomCreateIn(slug="a-b", name="x").camera_quality == "min"


@pytest.mark.parametrize("q", ["auto", "high", "medium", "low", "min", "off"])
def test_every_existing_mode_can_be_chosen_by_the_leader(q):
    assert RoomManagePatch(camera_quality=q).camera_quality == q


def test_unknown_mode_is_refused_and_the_field_is_in_the_leader_list():
    with pytest.raises(ValidationError):
        RoomManagePatch(camera_quality="ultra")
    assert "camera_quality" in LEADER_FIELDS, "без этого руководитель сохраняет настройку впустую"


def test_transcoded_low_track_is_offered_only_when_the_broadcast_is_above_360p():
    assert [q for q in LIVE_QUALITY if low_useful(q)] == ["720p", "1080p", "1440p"]
    assert low_useful("unknown") is True, "неизвестное качество считается 720p"
