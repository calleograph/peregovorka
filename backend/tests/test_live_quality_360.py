"""Качество эфира 360p: принимается всеми проверками и уходит в Egress именно 640×360 (а не 720p по умолчанию)."""
import pytest
from pydantic import ValidationError

from app.api.live import LivePatch
from app.services.live import LIVE_QUALITY, build_live_request
from app.services.settings import LiveSettings


@pytest.mark.parametrize("q", ["360p", "720p", "1080p", "1440p"])
def test_every_broadcast_quality_is_accepted_by_patch_and_settings(q):
    assert LivePatch(live_quality=q).live_quality == q
    assert LiveSettings(default_quality=q).default_quality == q


def test_unknown_quality_is_still_refused():
    with pytest.raises(ValidationError):
        LivePatch(live_quality="480p")
    with pytest.raises(ValidationError):
        LiveSettings(default_quality="240p")


def test_360p_encodes_640x360_with_its_own_bitrate_not_the_720p_default():
    w, h, vbr = LIVE_QUALITY["360p"]
    assert (w, h) == (640, 360) and 0 < vbr < LIVE_QUALITY["720p"][2]
    req = build_live_request("room", "360p", mp4_path=None, hls_prefix=None, urls=["rtmp://example.org/live/x"])
    assert req.advanced.width == 640 and req.advanced.height == 360 and req.advanced.video_bitrate == vbr
