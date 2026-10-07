"""배경음악·효과음 합성기 (numpy) — 외부 음원 없이 코드로 작곡하므로 저작권 문제가 없습니다.

분위기(mood): warm(따뜻함) / sad(슬픔) / tense(긴장) / calm(잔잔함) / hope(희망)
효과음(sfx): whoosh(장면 전환) / ding(반전·깨달음) / thud(충격)
"""
import math
import wave
import numpy as np

SR = 32000
MOODS = ("warm", "sad", "tense", "calm", "hope")
# (코드 진행[MIDI 화음], 화음 길이 초, 아르페지오 간격 초)
PROG = {
    "warm": ([(60, 64, 67), (55, 59, 62), (57, 60, 64), (53, 57, 60)], 4.0, .5),     # C G Am F
    "sad": ([(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)], 5.0, 1.0),     # Am F C G
    "tense": ([(50, 53, 57), (50, 53, 56), (49, 52, 56), (50, 53, 57)], 4.0, 2.0),   # Dm 계열 불협
    "calm": ([(53, 57, 60, 64), (48, 52, 55, 59), (50, 53, 57, 60), (55, 59, 62, 65)], 5.0, .75),  # Fmaj7 Cmaj7 Dm7 G7
    "hope": ([(53, 57, 60), (55, 59, 62), (52, 55, 60), (57, 60, 64)], 4.0, .5),     # F G Em Am
}


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def piano(f, dur, vel=1.0):
    t = np.arange(int(dur * SR)) / SR
    env = np.exp(-t * 2.2) * (1 - np.exp(-t * 300))
    w = np.sin(2 * np.pi * f * t) + .45 * np.sin(4 * np.pi * f * t) * np.exp(-t * 1.5) + .2 * np.sin(6 * np.pi * f * t) * np.exp(-t * 3)
    return (w * env * vel).astype(np.float32)


def pad(freqs, dur):
    t = np.arange(int(dur * SR)) / SR
    a = np.minimum(1, t / 1.2) * np.minimum(1, (dur - t) / 1.2).clip(0)
    w = sum(np.sin(2 * np.pi * (f + d) * t) for f in freqs for d in (-.15, .15)) / (2 * len(freqs))
    return (w * a).astype(np.float32)


def reverb(x, secs=1.4, mix=.28):
    rng = np.random.default_rng(3)
    n = int(secs * SR)
    ir = rng.standard_normal(n).astype(np.float32) * np.exp(-np.arange(n) / SR * 4.5).astype(np.float32)
    ir /= np.abs(ir).sum() / 6
    m = len(x) + n
    size = 1 << (m - 1).bit_length()
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[:len(x)]
    return ((1 - mix) * x + mix * wet).astype(np.float32)


def section(mood, start, dur):
    """절대 시각 start 부터 dur 초 분량의 음악 (코드 진행은 절대 시각 기준이라 장면이 바뀌어도 자연스럽게 이어짐)"""
    chords, clen, step = PROG.get(mood, PROG["calm"])
    out = np.zeros(int((dur + 3) * SR), np.float32)
    end_abs = start + dur

    def add(x, at_abs):
        q = int((at_abs - start) * SR)
        if 0 <= q < len(out):
            out[q:q + len(x)] += x[:len(out) - q]

    tb = math.floor(start / clen) * clen  # 화음 경계: 패드 + 베이스
    while tb < end_abs:
        s_ = max(tb, start)
        ch = chords[int(round(tb / clen)) % len(chords)]
        add(pad([hz(n) for n in ch], tb + clen - s_ + 1.0) * (.55 if mood != "tense" else .8), s_)
        add(piano(hz(ch[0] - 12), min(tb + clen - s_ + .5, 4), .5 if mood != "tense" else .7), s_)
        tb += clen
    k = math.ceil(start / step - 1e-9)  # 음표 격자
    while k * step < end_abs:
        ta = k * step
        ch = chords[int(ta // clen) % len(chords)]
        if mood == "tense":
            if k % 2 == 0:  # 심장 박동처럼 낮은 두 번 울림
                for off in (0, .28):
                    add(piano(hz(ch[0] - 24), .6, .9), ta + off)
        else:
            pattern = [0, 1, 2, 1, 2, 1] if len(ch) == 3 else [0, 2, 1, 3, 2, 1]
            n = ch[pattern[k % len(pattern)]] + (12 if mood in ("warm", "hope") and k % 4 == 3 else 0)
            add(piano(hz(n + 12), 2.5, .35 if mood == "sad" else .3), ta)
        k += 1
    # 섹션 경계는 부드럽게
    fade_in, fade_out = int(1.0 * SR), int(1.8 * SR)
    end = int(dur * SR)
    out[:fade_in] *= np.linspace(0, 1, fade_in, dtype=np.float32)
    out[end:end + fade_out] *= np.linspace(1, 0, min(fade_out, len(out) - end), dtype=np.float32)
    out[end + fade_out:] = 0
    return reverb(out)


def sfx(kind):
    rng = np.random.default_rng({"whoosh": 1, "ding": 2, "thud": 3}.get(kind, 4))
    if kind == "whoosh":
        n = int(.55 * SR)
        noise = rng.standard_normal(n).astype(np.float32)
        k = np.ones(18, np.float32) / 18
        x = np.convolve(noise, k, "same")
        env = np.sin(np.linspace(0, np.pi, n)) ** 2
        return (x * env * .5).astype(np.float32)
    if kind == "ding":
        t = np.arange(int(2.2 * SR)) / SR
        f = 1046.5
        w = sum(a * np.sin(2 * np.pi * f * r * t) * np.exp(-t * d) for r, a, d in ((1, 1, 1.6), (2.76, .45, 3), (5.4, .2, 5)))
        return (w * .35).astype(np.float32)
    if kind == "thud":
        t = np.arange(int(.7 * SR)) / SR
        w = np.sin(2 * np.pi * (70 - 30 * t) * t) * np.exp(-t * 6)
        return (w * .9).astype(np.float32)
    return np.zeros(1, np.float32)


def build(total, sections, events, music_gain=1.0):
    """total 초 길이의 (음악, 효과음) 트랙. sections=[(시작, 끝, mood)], events=[(시각, kind)]"""
    n = int((total + .5) * SR)
    music = np.zeros(n, np.float32)
    for s, e, mood in sections:
        if e - s < .3:
            continue
        seg = section(mood, s, e - s)
        a = int(s * SR)
        music[a:a + len(seg)] += seg[:max(0, n - a)]
    peak = np.abs(music).max() or 1
    music = music / peak * .2 * music_gain  # 배경음악은 작게 (대사 시 추가로 자동 감쇄)
    fx = np.zeros(n, np.float32)
    for at, kind in events:
        x = sfx(kind) * (.35 if kind == "whoosh" else .6)
        a = int(at * SR)
        fx[a:a + len(x)] += x[:max(0, n - a)]
    return music, fx


def write_wav(path, x):
    x = np.clip(x, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def auto_mood(lines):
    """장면 대사들의 감정으로 분위기 추정"""
    emo = [ln.get("emotion", "normal") for ln in lines if ln.get("who") != "narrator"]
    c = {k: emo.count(k) for k in set(emo)}
    if c.get("sad", 0) >= max(2, len(emo) * .4):
        return "sad"
    if c.get("angry", 0) + c.get("surprised", 0) >= max(2, len(emo) * .4):
        return "tense"
    if c.get("happy", 0) >= max(2, len(emo) * .4):
        return "warm"
    return "calm"
