from __future__ import annotations

import numpy as np

from app.audio.segmenter import SegmenterConfig, SpeechSegmenter

from .helpers import SR, EnergyVad, frames_of, silence, tone


def run(audio: np.ndarray, cfg: SegmenterConfig | None = None, t0: float = 1_000.0, frame: int = 160):
    seg = SpeechSegmenter(EnergyVad(), cfg)
    out, t = [], t0
    for f in frames_of(audio, frame):
        t += f.size / SR
        out += seg.feed(f, t)
    out += seg.flush()
    return out


def test_silence_never_produces_segments():
    assert run(silence(5)) == []


def test_single_utterance_has_padding_and_correct_wallclock():
    audio = np.concatenate([silence(1.0), tone(1.5), silence(1.5)])
    [s] = run(audio, t0=1_000.0)
    # речь реально начинается на 1.0 с потока (старт 1000.0 → 1001.0); pad 200 мс до и после
    assert 1000.7 <= s.started_at <= 1001.0
    assert 1002.5 <= s.ended_at <= 1002.9
    assert 1.5 <= s.duration_s <= 2.2


def test_short_blip_is_discarded_but_real_speech_after_it_is_kept():
    audio = np.concatenate([silence(1), tone(0.1), silence(1.2), tone(1.0), silence(1.2)])
    segs = run(audio)
    assert len(segs) == 1 and segs[0].duration_s >= 1.0


def test_two_utterances_separated_by_pause():
    audio = np.concatenate([tone(1.0), silence(1.2), tone(1.0), silence(1.2)])
    segs = run(audio)
    assert len(segs) == 2 and segs[0].ended_at <= segs[1].started_at


def test_short_pause_inside_phrase_does_not_split():
    audio = np.concatenate([tone(1.0), silence(0.3), tone(1.0), silence(1.2)])  # 300 мс < end_silence 700 мс
    assert len(run(audio)) == 1


def test_overlong_speech_is_force_split_under_limit():
    cfg = SegmenterConfig(max_segment_s=5.0)
    segs = run(np.concatenate([tone(13.0), silence(1.2)]), cfg)
    assert len(segs) >= 3
    assert all(s.duration_s <= 5.0 + 0.05 for s in segs)  # GigaAM: не длиннее лимита
    total = sum(s.duration_s for s in segs)
    assert total >= 12.5  # речь не потеряна при нарезке


def test_split_prefers_pause_over_hard_cut():
    cfg = SegmenterConfig(max_segment_s=10.0)
    audio = np.concatenate([tone(7.0), silence(0.3), tone(5.0), silence(1.2)])
    segs = run(audio, cfg)
    assert len(segs) == 2 and segs[0].duration_s < 8.0  # разрезано в паузе, не по таймеру 10 с


def test_stream_end_flushes_open_utterance():
    segs = run(tone(1.0))  # поток оборвался посреди речи
    assert len(segs) == 1


def test_gap_in_frames_flushes_and_keeps_wallclock():
    """Микрофон выключили: кадры не шли 10 с. Реплики не склеиваются, время не «плывёт»."""
    seg = SpeechSegmenter(EnergyVad())
    out = []
    t = 100.0
    for f in frames_of(tone(1.0)):
        t += f.size / SR
        out += seg.feed(f, t)
    t += 10.0  # провал
    for f in frames_of(np.concatenate([tone(1.0), silence(1.2)])):
        t += f.size / SR
        out += seg.feed(f, t)
    out += seg.flush()
    assert len(out) == 2
    assert out[1].started_at - out[0].ended_at > 9.0


def test_odd_frame_sizes_do_not_change_result():
    audio = np.concatenate([silence(0.5), tone(1.0), silence(1.2)])
    ref = run(audio, frame=160)
    for frame in (1, 97, 480, 1000, 4096):
        got = run(audio, frame=frame)
        assert len(got) == len(ref) == 1
        assert abs(got[0].duration_s - ref[0].duration_s) < 0.07
