"""MP4 видеозаписи: индекс (moov) переносится в начало без перекодирования; при любом сбое исходный файл остаётся нетронутым."""
import shutil
import subprocess

import pytest

from app.services import video_rec as vr

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="нужен ffmpeg")


def make(path, faststart: bool):
    args = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", *(["-movflags", "+faststart"] if faststart else []), str(path)]
    subprocess.run(args, check=True)


def test_moov_position_is_detected(tmp_path):
    a, b = tmp_path / "tail.mp4", tmp_path / "head.mp4"
    make(a, False); make(b, True)
    assert vr.moov_first(a) is False and vr.moov_first(b) is True
    junk = tmp_path / "x.mp4"; junk.write_bytes(b"not an mp4 at all")
    assert vr.moov_first(junk) is None


def test_faststart_moves_the_index_and_keeps_duration_and_streams(tmp_path):
    a = tmp_path / "rec.mp4"; make(a, False)
    d0 = vr.readable_duration(a)
    assert vr.faststart(a) is True
    assert vr.moov_first(a) is True and vr.readable_duration(a) == d0
    assert not list(tmp_path.glob("*.tmp")), "временный файл убран"
    codecs = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(a)], capture_output=True, text=True).stdout.split()
    assert sorted(codecs) == ["aac", "h264"], "потоки скопированы, не перекодированы"
    assert vr.faststart(a) is False, "уже готовый файл не трогается"


def test_broken_file_is_left_alone(tmp_path):
    a = tmp_path / "broken.mp4"; make(a, False)
    data = a.read_bytes(); a.write_bytes(data[: len(data) // 2])            # оборванная запись: индекс не записан
    before = a.read_bytes()
    assert vr.faststart(a) is False and a.read_bytes() == before and not list(tmp_path.glob("*.tmp"))
