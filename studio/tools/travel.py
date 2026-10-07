#!/usr/bin/env python3
"""여행 쇼핑쇼츠 제작기 — 사진(또는 AI 영상 클립) 몇 장 + 짧은 대본 → 세로 쇼츠 + 업로드 키트 + 링크 페이지 등록

사용법:
  python3 studio/tools/travel.py sample                                   # 예시 프로젝트(그림 사진) 만들기
  python3 studio/tools/travel.py lint   studio/projects/<slug>/trip.json  # 규정·수익화 점검
  python3 studio/tools/travel.py render studio/projects/<slug>/trip.json  # output/short.mp4 (+short_ig·short_tt) + cover.jpg
  python3 studio/tools/travel.py carousel studio/projects/<slug>/trip.json  # output/carousel/01~NN.mp4 (인스타 4:5 영상 묶음)
  python3 studio/tools/travel.py kit    studio/projects/<slug>/trip.json  # output/kit.html (제목·설명·캡션 복사)
  python3 studio/tools/travel.py deal   studio/projects/<slug>/trip.json  # deals.json 에 번호·링크 등록
  python3 studio/tools/travel.py all    studio/projects/<slug>/trip.json  # lint → render → kit → deal

trip.json 형식과 운영 방법은 studio/TRAVEL.md 를 보세요.
"""
import argparse, datetime as dt, html, json, math, os, re, subprocess, sys, zlib
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import animate  # noqa: E402  (사진을 실제로 움직이는 듯하게: 깊이 기반 3D 시차 + 구름·물결·별)
import media as studio  # noqa: E402  (음성 합성·폰트·ffmpeg 도우미)
import sound  # noqa: E402

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
REPO = Path(__file__).resolve().parents[2]
DEALS = REPO / "deals.json"  # 링크 페이지(저장소 맨 위 index.html)의 목록
PLATFORMS = {"myrealtrip": "마이리얼트립", "yeogi": "여기어때", "coupang": "쿠팡"}
# 공정위 추천·보증 심사지침: 경제적 이해관계를 소비자가 쉽게 알 수 있게 표시
DISCLOSURE = {
    "coupang": "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.",
    "myrealtrip": "이 영상은 마이리얼트립 파트너 활동의 일환으로, 링크로 예약하시면 일정액의 수수료를 제공받습니다.",
    "yeogi": "이 영상은 여기어때 마케팅 파트너 활동의 일환으로, 링크로 예약하시면 일정액의 수수료를 제공받습니다.",
}
YELLOW, WHITE, BLACK = (255, 214, 64), (255, 255, 255), (0, 0, 0)


# ───────────────────────── 공통 ─────────────────────────
def load(path):
    path = Path(path).resolve()
    return json.loads(path.read_text(encoding="utf-8")), path.parent


def font(size, path=None):
    return ImageFont.truetype(path or studio.find_font(), size)


def cover(img, w, h):
    """img 를 w×h 를 꽉 채우도록 확대 후 가운데 자르기"""
    k = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * k)), max(h, round(img.height * k))), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def ease(t):
    return t * t * (3 - 2 * t)


def stroke_text(d, xy, text, f, fill=WHITE, stroke=10, anchor="mm"):
    d.text(xy, text, font=f, fill=fill, stroke_width=stroke, stroke_fill=BLACK, anchor=anchor)


def rich_line(d, cx, y, text, f, stroke=10):
    """한 줄 가운데 정렬. 숫자가 든 단어(가격·거리)는 노란색"""
    words = text.split(" ")
    space = d.textlength(" ", font=f)
    widths = [d.textlength(wd, font=f) for wd in words]
    x = cx - (sum(widths) + space * (len(words) - 1)) / 2
    for wd, wdt in zip(words, widths):
        col = YELLOW if re.search(r"\d", wd) else WHITE
        d.text((x, y), wd, font=f, fill=col, stroke_width=stroke, stroke_fill=BLACK, anchor="lm")
        x += wdt + space


def fit_size(d, text, path, start, max_w):
    s = start
    while s > 40 and d.textlength(text, font=font(s, path)) > max_w:
        s -= 4
    return s


# ───────────────────────── 오버레이 (자막·후크·표시) ─────────────────────────
_SHADE = None


def shade_layer():
    """위·아래 그늘 (글씨 가독성)"""
    global _SHADE
    if _SHADE is None:
        col = Image.new("L", (1, H))
        for y in range(H):
            a = 0
            if y < 520:
                a = int(150 * (1 - y / 520) ** 1.6)
            elif y > H - 760:
                a = int(170 * ((y - (H - 760)) / 760) ** 1.4)
            col.putpixel((0, y), a)
        _SHADE = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        _SHADE.paste((0, 0, 0, 255), (0, 0, W, H), col.resize((W, H)))
    return _SHADE


def overlay(trip, shot, k, n, phrase, t_in_shot, t_global, fpath, end_card):
    """한 프레임 위에 얹을 RGBA 레이어"""
    ov = shade_layer().copy()
    d = ImageDraw.Draw(ov)

    # 광고 표시 (영상 내내 — 쇼츠는 설명란을 거의 안 보므로 화면에 표시)
    tag = f"광고 · {PLATFORMS.get(trip.get('platform'), '제휴')} 제휴 링크 포함"
    ft = font(30, fpath)
    tw = d.textlength(tag, font=ft)
    d.rounded_rectangle((40, 70, 40 + tw + 36, 122), 14, fill=(0, 0, 0, 150))
    d.text((58, 96), tag, font=ft, fill=(255, 255, 255, 235), anchor="lm")

    # 후크(상단 큰 2줄): 첫 컷은 크게, 이후는 작게 고정
    hook = trip.get("hook") or []
    if hook:
        big = k == 0 and not end_card
        size = 104 if big else 60
        if big and t_in_shot < .25:  # 펀치 줌
            size = int(size * (1.18 - .72 * t_in_shot))
        y0 = 260 if big else 200
        for i, line in enumerate(hook[:2]):
            s = fit_size(d, line, fpath, size, W - 100)
            col = YELLOW if i == len(hook[:2]) - 1 else WHITE
            stroke_text(d, (W // 2, y0 + i * (s + 22)), line, font(s, fpath), fill=col, stroke=12 if big else 8)

    if end_card:
        return end_overlay(trip, ov, fpath, t_in_shot)

    # 컷 꼬리표 (예: 인피니티풀)
    if shot.get("tag"):
        f2 = font(44, fpath)
        tw = d.textlength(shot["tag"], font=f2)
        y = H - 690
        d.rounded_rectangle((W / 2 - tw / 2 - 30, y - 38, W / 2 + tw / 2 + 30, y + 38), 38, fill=(255, 214, 64, 240))
        d.text((W / 2, y), shot["tag"], font=f2, fill=BLACK, anchor="mm")

    # 구절 자막
    if phrase:
        f3 = font(fit_size(d, phrase, fpath, 88, W - 120), fpath)
        rich_line(d, W // 2, H - 540, phrase, f3)

    # 진행 막대 (끝까지 보게)
    total = trip["_total"]
    d.rectangle((0, H - 10, int(W * min(1, t_global / total)), H), fill=(255, 214, 64, 230))
    return ov


def end_overlay(trip, ov, fpath, t):
    """마지막 안내: 숙소명 · 가격 · '프로필 링크 → N번' (쇼츠 하단 제목·버튼 영역을 피해 위로)"""
    d = ImageDraw.Draw(ov)
    a = min(1, t / .25)
    box = (60, H - 1070, W - 60, H - 470)
    d.rounded_rectangle(box, 40, fill=(10, 14, 24, int(215 * a)))
    name = trip.get("name", "")
    stroke_text(d, (W // 2, H - 990), name, font(fit_size(d, name, fpath, 64, W - 200), fpath), stroke=0)
    if trip.get("price"):
        stroke_text(d, (W // 2, H - 890), trip["price"], font(58, fpath), fill=YELLOW, stroke=0)
    cta = trip.get("cta") or f"프로필 링크에서 {trip.get('deal_id', '?')}번"
    s = fit_size(d, cta, fpath, 76, W - 200)
    pulse = 1 + .04 * math.sin(t * 9)
    f = font(int(s * pulse), fpath)
    tw = d.textlength(cta, font=f)
    d.rounded_rectangle((W / 2 - tw / 2 - 40, H - 780, W / 2 + tw / 2 + 40, H - 650), 60, fill=(255, 214, 64, 255))
    d.text((W / 2, H - 715), cta, font=f, fill=BLACK, anchor="mm")
    note = f"가격은 {trip.get('price_checked', '확인일')} 기준 · 날짜·객실에 따라 달라요"
    d.text((W / 2, H - 570), note, font=font(30, fpath), fill=(220, 220, 220, 255), anchor="mm")
    if trip.get("credit"):
        d.text((W / 2, H - 522), trip["credit"], font=font(26, fpath), fill=(180, 180, 180, 255), anchor="mm")
    return ov


# ───────────────────────── 화면 (사진 움직임 / 영상 클립) ─────────────────────────
def motion_of(shot, img):
    m = shot.get("motion")
    if m:
        return m
    return "in" if img.height >= img.width * 1.2 else ("right" if zlib.crc32(shot.get("img", "").encode()) % 2 else "left")


def photo_frames(shot, root, secs):
    """사진 한 장 → 매 프레임 이미지 생성기 (부드러운 줌·패닝, 하위 픽셀 단위)"""
    src = Image.open(root / shot["img"]).convert("RGB")
    mo = motion_of(shot, src)
    # 기준: 화면을 꽉 채우는 배율 (패닝은 가로로 여유를 둠)
    base = max(W / src.width, H / src.height)
    if mo in ("left", "right"):
        base = max(base, W * 1.35 / src.width)
    over = 1.0 + 0.12  # 줌 여유
    k0 = base * over
    big = src.resize((math.ceil(src.width * k0), math.ceil(src.height * k0)), Image.LANCZOS)
    n = max(1, round(secs * FPS))
    for i in range(n):
        t = ease(i / max(1, n - 1))
        z = {"in": 1 + .11 * t, "out": 1.11 - .11 * t}.get(mo, 1.04)  # 1 = 화면을 막 채우는 배율
        scale = z / over  # 화면 픽셀 = big 픽셀 × scale
        vw, vh = W / scale, H / scale
        cx, cy = big.width / 2, big.height / 2
        if mo in ("left", "right"):
            span = (big.width - vw) / 2 * .9
            cx += (-span + 2 * span * t) * (1 if mo == "right" else -1)
        if mo == "up" and big.height > vh:
            span = (big.height - vh) / 2 * .9
            cy += span - 2 * span * t
        x0, y0 = cx - vw / 2, cy - vh / 2
        yield big.transform((W, H), Image.AFFINE, (1 / scale, 0, x0, 0, 1 / scale, y0), Image.BICUBIC)


LIVE_MOVE = {"in": "push", "out": "push", "push": "push", "left": "left", "right": "right", "up": "up", "orbit": "orbit", "fly": "fly"}


def live_frames(trip, shot, root, secs, w, h, seed=0):
    """기본: 깊이 기반 '살아 있는 사진'. live: false 이거나 모듈이 없으면 줌·패닝"""
    if shot.get("live", trip.get("live", True)) and animate.available():
        src = Image.open(root / shot["img"]).convert("RGB")
        mo = LIVE_MOVE.get(motion_of(shot, src), "push")
        for fr in animate.frames(np.asarray(src), w, h, secs, mo, shot.get("fx", "auto"), seed=seed):
            yield Image.fromarray(fr)
        return
    if (w, h) != (W, H):  # 줌·패닝 엔진은 9:16 전용 → 잘라서 사용
        for fr in photo_frames(shot, root, secs):
            yield cover(fr, w, h)
        return
    yield from photo_frames(shot, root, secs)


def clip_filter(shot, w, h):
    """zoom(예: 1.4) + focus([가로, 세로] 0~1) 로 같은 AI 영상에서 다른 구도(가까이)를 잘라 씀"""
    z = float(shot.get("zoom", 1.0))
    fx, fy = shot.get("focus", [.5, .5])
    zw, zh = int(w * z) // 2 * 2, int(h * z) // 2 * 2
    return (f"fps={FPS},scale={zw}:{zh}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:'(iw-{w})*{fx}':'(ih-{h})*{fy}'")


def clip_frames(shot, root, secs, w=W, h=H):
    """AI 영상 클립(구글 Flow·Veo·Kling 등) → w×h 로 맞춘 프레임.
    AI 영상은 처음 0.5초쯤 사진처럼 멈춰 있다가 움직이므로 start(기본 0.8초)부터 사용. 짧으면 반복"""
    start = float(shot.get("start", 0.8))
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-stream_loop", "-1", "-i", str(root / shot["clip"]),
                          "-t", f"{secs:.3f}", "-an",
                          "-vf", clip_filter(shot, w, h),
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    n, size = max(1, round(secs * FPS)), w * h * 3
    last = None
    for _ in range(n):
        buf = p.stdout.read(size)
        if len(buf) == size:
            last = Image.frombytes("RGB", (w, h), buf)
        yield last if last is not None else Image.new("RGB", (w, h))
    p.stdout.close()
    p.wait()


def render_segment(job):
    trip, shot, k, n, secs, start, phrases, out, fpath, end_card = job
    root = Path(trip["_root"])
    frames = clip_frames(shot, root, secs) if shot.get("clip") else live_frames(trip, shot, root, secs, W, H)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                            "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    cache = {}
    for i, fr in enumerate(frames):
        t = i / FPS
        ph = next((p for a, b, p in phrases if a <= t < b), phrases[-1][2] if phrases else "")
        # 오버레이는 바뀔 때만 다시 그림 (후크 펀치 줌·마지막 안내 깜빡임 구간은 매 프레임)
        key = (ph, round(t, 2) if (k == 0 and t < .25) or end_card else 0, int((start + t) * 4))
        if key not in cache:
            cache.clear()
            cache[key] = overlay(trip, shot, k, n, ph, t, start + t, fpath, end_card)
        fr = fr.convert("RGBA")
        fr.alpha_composite(cache[key])
        enc.stdin.write(fr.convert("RGB").tobytes())
    enc.stdin.close()
    if enc.wait():
        raise RuntimeError(f"인코딩 실패: {out}")
    return str(out)


# ───────────────────────── 음성 ─────────────────────────
def voice_cfg(trip):
    v = trip.get("voice", {})
    return (v.get("edge", "ko-KR-SunHiNeural"), v.get("rate", "+8%"), v.get("pitch", "+0Hz"),
            v.get("gvoice", "ko-KR-Chirp3-HD-Aoede"))


def tts(trip, root, text, idx):
    import hashlib
    edge, rate, pitch, gvoice = voice_cfg(trip)
    h = hashlib.md5(f"{gvoice}|{edge}|{rate}|{text}".encode()).hexdigest()[:10]
    out = root / "work" / f"v{idx:02d}_{h}.mp3"
    if not out.exists():
        studio.make_audio(text, edge, rate, pitch, out, gvoice)
    return out


def split_phrases(text, secs, lead=.05):
    ps = studio.phrases(text, 13)
    total = sum(len(p) for p in ps) or 1
    out, t = [], lead
    for p in ps:
        d = secs * len(p) / total
        out.append((t, t + d, p))
        t += d
    return out


# ───────────────────────── 명령 ─────────────────────────
def cmd_render(trip, root, fpath=None):
    style = trip.get("style", "voice")
    if style == "voice":  # 기본: 처음 벤치마킹(뉴머니) 방식 — AI 영상 클립 + AI 대본 성우 내레이션 + 자막
        return cmd_voice(trip, root, fpath)
    if style == "insta":  # 음성 없이 움직이는 사진 + 글씨 + 음악 (인스타 허지니 묶음 스타일)
        return cmd_reel(trip, root, fpath)
    return cmd_render_classic(trip, root, fpath)


def cmd_render_classic(trip, root, fpath=None):
    """예전 방식: 음성 해설 + 구절 자막 + 후크 고정 + 끝 안내 상자 ("style": "classic")"""
    fpath = studio.find_font(fpath)
    work, outd = root / "work", root / "output"
    work.mkdir(exist_ok=True)
    outd.mkdir(exist_ok=True)
    shots = trip["shots"]
    print(f"[음성] {len(shots)}개")
    with ThreadPoolExecutor(8) as ex:
        audios = list(ex.map(lambda a: tts(trip, root, a[1].get("line", ""), a[0]) if a[1].get("line") else None,
                             enumerate(shots)))
    gap = .12
    lens = [(studio.dur(a) if a else float(s.get("secs", 1.6))) for a, s in zip(audios, shots)]
    secs = [max(1.4, L + gap) for L in lens]
    end_secs = float(trip.get("end_secs", 2.6))
    trip["_total"] = sum(secs) + end_secs
    trip["_root"] = str(root)

    jobs, t = [], 0.0
    for k, (s, sc) in enumerate(zip(shots, secs)):
        jobs.append((trip, s, k, len(shots), sc, t, split_phrases(s.get("caption", s.get("line", "")), sc - gap),
                     work / f"seg{k:02d}.mp4", fpath, False))
        t += sc
    last = dict(shots[trip.get("end_shot", len(shots) - 1)])
    last["motion"] = "out" if not last.get("clip") else None
    jobs.append((trip, last, len(shots), len(shots), end_secs, t, [], work / "seg_end.mp4", fpath, True))
    extra = variants(trip)  # 끝 안내만 바꾼 플랫폼별 판 (인스타·틱톡)
    for name, cta in extra:
        jobs.append((dict(trip, cta=cta), last, len(shots), len(shots), end_secs, t, [], work / f"seg_end_{name}.mp4", fpath, True))
    print(f"[화면] 컷 {len(jobs)}개 · {trip['_total']:.1f}초")
    with ProcessPoolExecutor(max(1, min(len(jobs), os.cpu_count() or 2))) as ex:
        segs = list(ex.map(render_segment, jobs))
    ends = segs[len(segs) - len(extra):] if extra else []
    segs = segs[:len(segs) - len(extra)]

    # 대사 이어 붙이기 (컷 시작 시각에 맞춰)
    narr = work / "narr.wav"
    inputs, flt, t = [], [], 0.0
    for i, (a, sc) in enumerate(zip(audios, secs)):
        if a:
            inputs += ["-i", str(a)]
            j = len(inputs) // 2 - 1
            flt.append(f"[{j}:a]aresample=44100,adelay={int(t * 1000)}:all=1[a{j}]")
        t += sc
    nin = len(inputs) // 2
    if nin:
        fc = ";".join(flt) + ";" + "".join(f"[a{j}]" for j in range(nin)) + \
             f"amix=inputs={nin}:normalize=0,apad=whole_dur={trip['_total']:.2f}[o]"
        studio.sh(["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[o]", "-t", f"{trip['_total']:.2f}", str(narr)])
    else:
        studio.sh(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{trip['_total']:.2f}", str(narr)])

    # 배경음악(코드로 작곡 — 저작권 없음) + 컷 전환 효과음
    bounds = [sum(secs[:i]) for i in range(1, len(secs) + 1)]
    music, fx = sound.build(trip["_total"], [(0, trip["_total"], trip.get("music", "hope"))],
                            [(b - .2, "whoosh") for b in bounds[:-1]] + [(bounds[-1], "ding")],
                            float(trip.get("music_volume", 1.3)))
    sound.write_wav(work / "music.wav", music)
    sound.write_wav(work / "fx.wav", fx)

    def mux(seg_list, out):
        lst = work / f"{out.stem}.txt"
        lst.write_text("".join(f"file '{Path(s).resolve()}'\n" for s in seg_list))
        studio.sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(narr),
                   "-i", str(work / "music.wav"), "-i", str(work / "fx.wav"), "-filter_complex", fc,
                   "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                   "-t", f"{trip['_total']:.2f}", "-movflags", "+faststart", str(out)])
        print(f"  ✔ {out} ({trip['_total']:.1f}초)")

    out = outd / "short.mp4"
    fc = ("[1:a]aresample=44100,asplit=2[va][vb];[2:a]aresample=44100[m];"
          "[m][va]sidechaincompress=threshold=0.015:ratio=8:attack=15:release=400[md];[3:a]aresample=44100[fx];"
          "[vb][md][fx]amix=inputs=3:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100[aout]")
    mux(segs, out)  # 유튜브 쇼츠·네이버 클립: 프로필 링크 N번
    for (name, _), seg in zip(extra, ends):
        mux(segs[:-1] + [seg], outd / f"short_{name}.mp4")
    segs += ends
    studio.sh(["ffmpeg", "-y", "-ss", "0.5", "-i", str(out), "-frames:v", "1", "-q:v", "3", str(outd / "cover.jpg")])
    if not os.environ.get("STUDIO_KEEP_WORK"):
        for s in segs:
            Path(s).unlink(missing_ok=True)


def site_label(trip):
    """화면에 적을 링크 페이지 주소 (틱톡 팔로워 1천 명 전에는 프로필 링크가 없어서 주소를 직접 보여 줌)"""
    return trip.get("site_label") or re.sub(r"^https?://|/$", "", trip.get("site") or "sukjjim.github.io")


def tiktok_how(trip):
    did = trip.get("deal_id", "?")
    if trip.get("tiktok_dm_keyword"):
        return f"DM으로 '{trip['tiktok_dm_keyword']}' 보내면 링크", f"💬 DM으로 '{trip['tiktok_dm_keyword']}' 보내 주시면 예약 링크를 바로 보내드려요"
    if trip.get("tiktok_bio"):
        return f"프로필 링크에서 {did}번", f"👉 프로필 링크에서 {did}번 검색"
    return f"{site_label(trip)} 에서 {did}번", f"👉 {site_label(trip)} 에서 {did}번 검색"


def variants(trip):
    """[(이름, 끝 안내 문구)] — short.mp4(유튜브·네이버) 외에 추가로 만들 판"""
    out = []
    if trip.get("dm_keyword"):
        kw = trip["dm_keyword"]
        out.append(("ig", "댓글 남기면 숙소 링크 DM" if kw == "*" else f"댓글에 '{kw}' 남기면 링크 DM"))
    if trip.get("tiktok", True):
        out.append(("tt", tiktok_how(trip)[0]))
    return out


def hashtags(trip):
    tags = trip.get("publish", {}).get("hashtags") or [f"#{trip.get('region', '여행')}여행", "#국내여행"]
    return [t if t.startswith("#") else "#" + t for t in tags]


def ig_how(trip, dm=True):
    did = trip.get("deal_id", "?")
    kw = trip.get("dm_keyword") if dm else None
    if kw == "*":  # 아무 댓글이나 → 자동 DM (벤치마킹: 댓글 장벽을 없애 댓글 수를 늘림)
        return ("📌 숙소 정보가 궁금하신 분들은 아무 댓글이나 남겨주세요☺️\n"
                "⚠️ 팔로우하시면 DM이 메시지 요청함에 묻히지 않아요\n"
                f"❤️ 프로필링크 {did}번에서도 확인할 수 있어요!")
    if kw:
        return f"💬 댓글에 '{kw}' 남기면 예약 링크를 DM으로 보내드려요\n❤️ 프로필링크 {did}번에서도 확인할 수 있어요!"
    return f"👉 프로필 링크에서 {did}번 검색"


def ga(word):
    """받침 있으면 '이가', 없으면 '가' (하루가 / 찜이가)"""
    c = ord(word[-1]) - 0xAC00 if word else -1
    return word + ("이가" if 0 <= c < 11172 and c % 28 else "가")


def ig_caption(trip, how, disc, title, body, tags):
    """인스타 캡션. points 가 있으면 '후크 → 본문 → ✔️ 포인트 → 안내' 틀"""
    pts = trip.get("points") or []
    head = trip.get("publish", {}).get("ig_hook") or title
    mid = ("\n".join(f"✔️ {p}" for p in pts) + "\n\n") if pts else ""
    sign = f"🏠 {ga(trip.get('persona', '하루'))} 찜해 둔 숙소예요\n\n"
    return (f"[광고] {head}\n\n{body}\n\n{mid}{sign}{how}\n(가격 {trip.get('price_checked', '')} 기준)\n\n{disc}\n\n"
            f"{' '.join(tags + ['#숙소추천'])}")


def texts(trip):
    """플랫폼별 제목·설명·캡션"""
    pub = trip.get("publish", {})
    pf = trip.get("platform")
    disc = DISCLOSURE.get(pf, "이 영상에는 제휴 링크가 포함되어 있으며, 구매·예약 시 일정액의 수수료를 받습니다.")
    title = pub.get("title") or " ".join(trip.get("hook", []))
    tags = hashtags(trip)
    did = trip.get("deal_id", "?")
    site = trip.get("site") or "https://sukjjim.github.io/"
    body = pub.get("description") or f"{trip.get('name', '')} · {trip.get('price', '')}"
    yt = (f"[광고] {disc}\n\n{body}\n\n📍 예약 링크: 채널 프로필 링크 → {did}번\n{site}#{did}\n"
          f"💰 가격은 {trip.get('price_checked', '')} 기준이며 날짜·객실에 따라 달라집니다.\n"
          f"{trip.get('credit', '')}\n\n{' '.join(tags)}")
    ig = ig_caption(trip, ig_how(trip), disc, title, body, tags)
    pin = f"📍 {trip.get('name', '')} 예약 링크는 채널 프로필 링크 → {did}번이에요! (광고·제휴 링크)"
    tt = (f"[광고] {title}\n{tiktok_how(trip)[1]}\n(가격 {trip.get('price_checked', '')} 기준)\n{disc}\n"
          f"{' '.join(tags[:3] + ['#여행', '#숙소추천'])}")
    return title, yt, ig, pin, tt


def cmd_kit(trip, root, fpath=None):
    title, yt, ig, pin, tt = texts(trip)
    kw = trip.get("dm_keyword")
    blocks = [("유튜브 쇼츠 제목", title), ("유튜브 설명", yt), ("고정 댓글", pin),
              ("인스타 캡션 (영상 묶음 carousel/ 또는 릴스 short_ig.mp4)" if kw else "인스타 · 네이버 클립 캡션", ig)]
    if kw:
        disc = DISCLOSURE.get(trip.get("platform"), "")
        title_, _, _, _, _ = texts(trip)
        disc_ = DISCLOSURE.get(trip.get("platform"), "")
        body_ = trip.get("publish", {}).get("description") or f"{trip.get('name', '')} · {trip.get('price', '')}"
        blocks += [("네이버 클립 캡션 (short.mp4)", ig_caption(trip, ig_how(trip, dm=False), disc_, title_, body_, hashtags(trip))),
                   ("자동 DM 설정 · 트리거", "모든 댓글 (키워드 없이)" if kw == "*" else f"키워드: {kw}"),
                   ("자동 DM 설정 · 보낼 메시지",
                    f"요청하신 {trip.get('name', '')} 예약 링크예요 🙌\n{trip.get('link', '')}\n\n"
                    f"가격은 {trip.get('price_checked', '')} 기준이라 날짜에 따라 달라질 수 있어요.\n[광고] {disc}"),
                   ("자동 DM 설정 · 공개 답글", "DM으로 링크 보내드렸어요! 📩 안 보이면 메시지 요청함을 확인해 주세요")]
    if trip.get("tiktok", True):
        blocks.append(("틱톡 캡션 (short_tt.mp4)", tt))
        if trip.get("tiktok_dm_keyword"):
            blocks.append(("틱톡 키워드 자동 답장 · 키워드 / 답장", f"{trip['tiktok_dm_keyword']}\n\n"
                           f"{trip.get('name', '')} 예약 링크예요 🙌 {trip.get('link', '')} [광고] {DISCLOSURE.get(trip.get('platform'), '')}"))
    items = "".join(
        f'<section><h2>{html.escape(h)}</h2><textarea readonly rows="{min(14, t.count(chr(10)) + 2)}">{html.escape(t)}</textarea>'
        f'<button onclick="c(this)">복사</button></section>' for h, t in blocks)
    checks = ("유튜브: 세부정보 → '유료 프로모션 포함' 체크", "유튜브: '변경되었거나 합성된 콘텐츠' — AI 영상 클립을 썼으면 '예'",
              "인스타: '유료 파트너십' 라벨 또는 캡션 첫 줄 [광고]",
              *([("인스타 자동 DM 도구에 '모든 댓글' 규칙 추가 (업로드 전에)" if kw == "*" else f"인스타 자동 DM 도구에 키워드 '{kw}' 규칙 추가 (업로드 전에)"), "인스타에는 short_ig.mp4, 유튜브·네이버에는 short.mp4"] if kw else []),
              *(["틱톡: short_tt.mp4 · 더보기 → '콘텐츠 공개'(브랜드 콘텐츠) 켜기 · AI 영상 클립을 썼으면 'AI 생성 콘텐츠' 라벨"] if trip.get("tiktok", True) else []), f"링크 페이지에 {trip.get('deal_id', '?')}번이 보이는지 확인 (travel.py deal 후 push)",
              "올리기 직전 가격·재고 다시 확인 (화면 속 가격 날짜와 다르면 수정)")
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>업로드 키트 · {html.escape(trip.get('name', ''))}</title><style>
body{{font-family:system-ui,sans-serif;max-width:640px;margin:0 auto;padding:16px;background:#0f1218;color:#eee}}
h1{{font-size:20px}}h2{{font-size:15px;color:#ffd640;margin:18px 0 6px}}textarea{{width:100%;box-sizing:border-box;background:#1b2030;color:#eee;border:1px solid #333;border-radius:8px;padding:10px;font-size:14px}}
button{{margin-top:6px;padding:10px 16px;border:0;border-radius:8px;background:#ffd640;font-weight:700}}li{{margin:6px 0}}video{{width:100%;border-radius:12px}}
</style></head><body><h1>{html.escape(trip.get('name', ''))} · {trip.get('deal_id', '?')}번</h1>
<video src="short.mp4" controls playsinline></video>{items}<h2>올리기 전 확인</h2><ul>{''.join(f'<li>☐ {html.escape(c)}</li>' for c in checks)}</ul>
<script>function c(b){{const t=b.previousElementSibling;t.select();navigator.clipboard.writeText(t.value);b.textContent='복사됨';setTimeout(()=>b.textContent='복사',1200)}}</script></body></html>"""
    out = root / "output" / "kit.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"  ✔ {out}")


def cmd_deal(trip, root, fpath=None, path=None):
    """링크 페이지(deals.json)에 등록. deal_id 가 없으면 새 번호를 매기고 trip.json 에도 기록"""
    if trip.get("sample"):
        print("  · 예시 프로젝트는 링크 페이지에 등록하지 않습니다")
        return
    data = json.loads(DEALS.read_text(encoding="utf-8")) if DEALS.exists() else {"deals": []}
    deals = data["deals"]
    if not trip.get("deal_id"):
        trip["deal_id"] = max([d["id"] for d in deals] + [0]) + 1
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        raw["deal_id"] = trip["deal_id"]
        Path(path).write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    img = None  # 목록 사진: 첫 컷 사진(글씨 없는 원본) → 없으면 영상 표지
    first = (trip.get("shots") or [{}])[0].get("img")
    src = root / first if first and (root / first).exists() else root / "output" / "cover.jpg"
    if src.exists():
        (REPO / "img").mkdir(parents=True, exist_ok=True)
        dst = REPO / "img" / f"{trip['slug']}.jpg"
        cover(Image.open(src).convert("RGB"), 360, 480).save(dst, quality=80)
        img = f"img/{dst.name}"
    entry = {"id": trip["deal_id"], "slug": trip["slug"], "name": trip.get("name", ""), "region": trip.get("region", ""),
             "platform": trip.get("platform", ""), "url": trip.get("link", ""), "price": trip.get("price", ""),
             "price_checked": trip.get("price_checked", ""), "tags": trip.get("tags", []), "img": img,
             "video": trip.get("video_url", ""), "added": dt.date.today().isoformat()}
    deals[:] = [d for d in deals if d["id"] != entry["id"]] + [entry]
    deals.sort(key=lambda d: -d["id"])
    data["updated"] = dt.date.today().isoformat()
    DEALS.parent.mkdir(exist_ok=True)
    DEALS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  ✔ {DEALS} 에 {entry['id']}번 등록 — git push 하면 링크 페이지에 반영")


# ───────────────────────── 인스타 스타일 (벤치마킹: 움직이는 사진 + 깔끔한 글씨) ─────────────────────────
CW, CH = 1080, 1350  # 인스타 영상 묶음 4:5
AUTO_MOVES = ["push", "right", "up", "left", "push", "right"]  # 움직임을 지정하지 않은 컷은 돌아가며


BADGE = REPO / "brand" / "badge.png"  # 숙소찜 로고 배지 (brand/logo-source.html 에서 만듦)


def brand_badge(ov, trip, x, y, fpath, h=84):
    """왼쪽 위 로고: 배지 그림이 있으면 그림, 없거나 brand 를 따로 지정했으면 글자 배지"""
    if BADGE.exists() and trip.get("brand", "숙소찜") == "숙소찜":
        b = Image.open(BADGE).convert("RGBA")
        b = b.resize((round(b.width * h / b.height), h), Image.LANCZOS)
        ov.alpha_composite(b, (x, y))
        return
    d = ImageDraw.Draw(ov)
    brand = trip.get("brand", "숙소찜")
    f = font(32, fpath)
    tw = d.textlength(brand, font=f)
    d.rounded_rectangle((x, y, x + tw + 40, y + 58), 29, fill=(255, 255, 255, 235))
    d.text((x + 20, y + 29), brand, font=f, fill=(25, 25, 25), anchor="lm")


def soft_text(ov, xy, text, f, fill=WHITE):
    """벤치마킹 자막: 흰 글씨 + 퍼진 그림자 (두꺼운 테두리 없음)"""
    sh = Image.new("RGBA", ov.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).text(xy, text, font=f, fill=(0, 0, 0, 200), anchor="mm")
    sh = sh.filter(ImageFilter.GaussianBlur(f.size / 9))
    ov.alpha_composite(sh)
    ov.alpha_composite(sh)
    ImageDraw.Draw(ov).text(xy, text, font=f, fill=fill, anchor="mm")


def insta_overlay(trip, k, text, fpath, size, note=None):
    w, h = size
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    top = 36 if h <= CH else 150  # 9:16 은 상단 앱 버튼을 피해 아래로
    brand_badge(ov, trip, 30, top - 10, fpath)
    d = ImageDraw.Draw(ov)
    if k == 0:  # 광고 표시 (작게, 첫 장)
        soft_text(ov, (w - 80, top + 29), "광고", font(28, fpath))
    if text:
        lines = text.split("\n")[:2]
        big = k == 0
        size_ = 76 if big else 50
        base = (h * .70 if h > w * 1.5 else h * .76) if big else (h * .74 if h > w * 1.5 else h * .80)
        if big:
            soft_text(ov, (w // 2, int(base - size_ * 1.15)), "*", font(44, fpath))
        for i, ln in enumerate(lines):
            s = fit_size(d, ln, fpath, size_, w - 140)
            soft_text(ov, (w // 2, int(base + i * (s + 18))), ln, font(s, fpath))
    if note:
        soft_text(ov, (w // 2, int(h * (.88 if h > w * 1.5 else .93))), note, font(30, fpath), fill=(235, 235, 235))
    return ov


def insta_job(job):
    trip, shot, k, text, secs, out, fpath, size, note = job
    root = Path(trip["_root"])
    w, h = size
    ov = insta_overlay(trip, k, text, fpath, size, note)
    if not shot.get("motion") and not shot.get("clip"):
        shot = dict(shot, motion=AUTO_MOVES[k % len(AUTO_MOVES)])
    frames = clip_frames(shot, root, secs, w, h) if shot.get("clip") else live_frames(trip, shot, root, secs, w, h, seed=k)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                            "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    for fr in frames:
        fr = (cover(fr, w, h) if fr.size != (w, h) else fr).convert("RGBA")
        fr.alpha_composite(ov)
        enc.stdin.write(fr.convert("RGB").tobytes())
    enc.stdin.close()
    if enc.wait():
        raise RuntimeError(f"인코딩 실패: {out}")
    return str(out)


def insta_plan(trip):
    """[(컷, 글씨, 초)] — 1장 후크, card 가 있는 장은 글을 읽을 만큼 길게"""
    shots = trip.get("carousel_shots") or trip["shots"]
    secs = float(trip.get("carousel_secs", 1.6))
    plan = []
    for k, sh in enumerate(shots[:20]):
        text = "\n".join(trip.get("hook", [])) if k == 0 else (sh.get("card") or "")
        plan.append((sh, text, float(sh.get("secs", max(secs, len(text.replace(chr(10), "")) / 9 + .6 if text else secs)))))
    return plan


def cmd_carousel(trip, root, fpath=None):
    """인스타 영상 묶음: 사진마다 4:5 움직이는 영상(기본 1.6초, 무음). 첫 장 = 후크 2줄, 2~3장 = 짧은 설명"""
    fpath = studio.find_font(fpath)
    trip["_root"] = str(root)
    outd = root / "output" / "carousel"
    outd.mkdir(parents=True, exist_ok=True)
    for old in outd.glob("*.mp4"):
        old.unlink()
    plan = insta_plan(trip)
    jobs = [(trip, sh, k, text, sc, outd / f"{k + 1:02d}.mp4", fpath, (CW, CH), None) for k, (sh, text, sc) in enumerate(plan)]
    print(f"[인스타 묶음] {len(jobs)}장 · 4:5")
    with ProcessPoolExecutor(max(1, min(len(jobs), (os.cpu_count() or 2)))) as ex:
        outs = list(ex.map(insta_job, jobs))
    print(f"  ✔ {outd} ({len(outs)}개) — 인스타에서 순서대로 여러 개 선택해 올리기")


def voice_overlay(trip, k, hook, phrase, end_text, note, fpath):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    brand_badge(ov, trip, 30, 140, fpath)
    d = ImageDraw.Draw(ov)
    soft_text(ov, (W - 80, 179), "광고", font(28, fpath))  # 영상 내내 작게 (쇼츠는 설명란을 잘 안 봄)
    if hook:
        for i, ln in enumerate(hook[:2]):
            s = fit_size(d, ln, fpath, 92, W - 120)
            soft_text(ov, (W // 2, 470 + i * (s + 24)), ln, font(s, fpath), fill=WHITE if i == 0 else YELLOW)
    if end_text:
        for i, ln in enumerate(end_text.split("\n")[:2]):
            s = fit_size(d, ln, fpath, 70 if i else 56, W - 120)
            soft_text(ov, (W // 2, int(H * .40) + i * (s + 26)), ln, font(s, fpath), fill=YELLOW if i else WHITE)
    if phrase:
        s = fit_size(d, phrase, fpath, 66, W - 120)
        soft_text(ov, (W // 2, int(H * .68)), phrase, font(s, fpath))
    if note:
        soft_text(ov, (W // 2, int(H * .76)), note, font(30, fpath), fill=(235, 235, 235))
    return ov


def voice_job(job):
    trip, shot, k, secs, phrases, hook, end_text, note, out, fpath = job
    root = Path(trip["_root"])
    if not shot.get("motion") and not shot.get("clip"):
        shot = dict(shot, motion=AUTO_MOVES[k % len(AUTO_MOVES)])
    frames = clip_frames(shot, root, secs) if shot.get("clip") else live_frames(trip, shot, root, secs, W, H, seed=k)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                            "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    cache = {}
    for i, fr in enumerate(frames):
        t = i / FPS
        ph = next((p for a, b, p in phrases if a <= t < b), "")
        if ph not in cache:
            cache[ph] = voice_overlay(trip, k, hook, ph, end_text, note, fpath)
        fr = (cover(fr, W, H) if fr.size != (W, H) else fr).convert("RGBA")
        fr.alpha_composite(cache[ph])
        enc.stdin.write(fr.convert("RGB").tobytes())
    enc.stdin.close()
    if enc.wait():
        raise RuntimeError(f"인코딩 실패: {out}")
    return str(out)


def cmd_voice(trip, root, fpath=None):
    """처음 벤치마킹(뉴머니 '사진 1장으로 여행쇼츠') 방식: AI 영상 클립(또는 움직이는 사진) + 성우 내레이션 + 자막 + 음악"""
    fpath = studio.find_font(fpath)
    trip["_root"] = str(root)
    work, outd = root / "work", root / "output"
    work.mkdir(exist_ok=True)
    outd.mkdir(exist_ok=True)
    shots = trip["shots"]
    print(f"[음성] {sum(1 for s in shots if s.get('line'))}개")
    with ThreadPoolExecutor(8) as ex:
        audios = list(ex.map(lambda a: tts(trip, root, a[1]["line"], a[0]) if a[1].get("line") else None, enumerate(shots)))
    gap = .15
    secs = [max(1.6, studio.dur(a) + gap) if a else float(s.get("secs", 1.6)) for a, s in zip(audios, shots)]
    did = trip.get("deal_id", "?")
    note = f"가격 {trip.get('price_checked', '')} 기준 · 날짜에 따라 달라요" if trip.get("price") else None
    name = trip.get("name", "")
    ends = [("short", f"{name}\n프로필 링크 {did}번")] + [(f"short_{n}", f"{name}\n{cta}") for n, cta in variants(trip)]
    jobs, last = [], len(shots) - 1
    for k, (sh, sc) in enumerate(zip(shots, secs)):
        phr = split_phrases(sh.get("caption", sh.get("line", "")), sc - gap) if sh.get("line") else []
        hook = trip.get("hook") if k == 0 else None
        if k < last:
            jobs.append((trip, sh, k, sc, phr, hook, None, None, work / f"v{k:02d}.mp4", fpath))
        else:
            for nm, end in ends:
                jobs.append((trip, sh, k, sc, phr, hook, end, note, work / f"v_end_{nm}.mp4", fpath))
    total = sum(secs)
    print(f"[화면] 컷 {len(shots)}개 · {total:.1f}초 · 판 {len(ends)}개")
    with ProcessPoolExecutor(max(1, min(len(jobs), os.cpu_count() or 2))) as ex:
        segs = list(ex.map(voice_job, jobs))
    body, endsegs = segs[:last], segs[last:]
    # 내레이션: 컷 시작 시각에 맞춰 배치
    narr, inputs, flt, t = work / "narr.wav", [], [], 0.0
    for a, sc in zip(audios, secs):
        if a:
            inputs += ["-i", str(a)]
            j = len(inputs) // 2 - 1
            flt.append(f"[{j}:a]aresample=44100,adelay={int((t + .05) * 1000)}:all=1[a{j}]")
        t += sc
    nin = len(inputs) // 2
    if nin:
        fc = ";".join(flt) + ";" + "".join(f"[a{j}]" for j in range(nin)) + f"amix=inputs={nin}:normalize=0,apad=whole_dur={total:.2f}[o]"
        studio.sh(["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[o]", "-t", f"{total:.2f}", str(narr)])
    else:
        studio.sh(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{total:.2f}", str(narr)])
    m, _ = sound.build(total, [(0, total, trip.get("music", "hope"))], [], float(trip.get("music_volume", 1.4)))
    sound.write_wav(work / "music.wav", m)
    fc = ("[1:a]aresample=44100,asplit=2[va][vb];[2:a]aresample=44100[m];"
          "[m][va]sidechaincompress=threshold=0.015:ratio=8:attack=15:release=400[md];"
          f"[vb][md]amix=inputs=2:duration=first:normalize=0,afade=t=out:st={max(0, total - .6):.2f}:d=0.6,"
          "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100[aout]")
    for (nm, _), endseg in zip(ends, endsegs):
        lst = work / f"{nm}.txt"
        lst.write_text("".join(f"file '{Path(x).resolve()}'\n" for x in body + [endseg]))
        out = outd / f"{nm}.mp4"
        studio.sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(narr), "-i", str(work / "music.wav"),
                   "-filter_complex", fc, "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                   "-t", f"{total:.2f}", "-movflags", "+faststart", str(out)])
        print(f"  ✔ {out} ({total:.1f}초)")
    studio.sh(["ffmpeg", "-y", "-ss", "0.3", "-i", str(outd / "short.mp4"), "-frames:v", "1", "-q:v", "3", str(outd / "cover.jpg")])
    if not os.environ.get("STUDIO_KEEP_WORK"):
        for x in segs:
            Path(x).unlink(missing_ok=True)


def cmd_reel(trip, root, fpath=None):
    """세로 쇼츠·릴스·틱톡 (인스타 스타일): 움직이는 사진을 끊김 없이 이어 붙이고 마지막 컷에만 안내 문구"""
    fpath = studio.find_font(fpath)
    trip["_root"] = str(root)
    work, outd = root / "work", root / "output"
    work.mkdir(exist_ok=True)
    outd.mkdir(exist_ok=True)
    plan = insta_plan(trip)
    if len(plan) < 2:
        sys.exit("인스타 스타일은 사진 2장 이상이 필요합니다")
    did = trip.get("deal_id", "?")
    note = f"가격 {trip.get('price_checked', '')} 기준 · 광고" if trip.get("price") else "광고"
    ends = [("short", f"{trip.get('name', '')}\n프로필 링크 {did}번")] + \
           [(f"short_{n}", f"{trip.get('name', '')}\n{cta}") for n, cta in variants(trip)]
    end_secs = max(2.4, plan[-1][2])
    jobs = [(trip, sh, k, text, sc, work / f"r{k:02d}.mp4", fpath, (W, H), None) for k, (sh, text, sc) in enumerate(plan[:-1])]
    for name, cta in ends:
        jobs.append((trip, plan[-1][0], len(plan) - 1, cta, end_secs, work / f"r_end_{name}.mp4", fpath, (W, H), note))
    total = sum(sc for _, _, sc in plan[:-1]) + end_secs
    print(f"[세로 영상] 컷 {len(plan)}개 · {total:.1f}초 · 판 {len(ends)}개")
    with ProcessPoolExecutor(max(1, min(len(jobs), os.cpu_count() or 2))) as ex:
        segs = list(ex.map(insta_job, jobs))
    body, endsegs = segs[:len(plan) - 1], segs[len(plan) - 1:]
    music = trip.get("music", "hope")
    m, _ = sound.build(total, [(0, total, music)], [], float(trip.get("music_volume", 2.2)))
    sound.write_wav(work / "music.wav", m)
    for (name, _), endseg in zip(ends, endsegs):
        lst = work / f"{name}.txt"
        lst.write_text("".join(f"file '{Path(x).resolve()}'\n" for x in body + [endseg]))
        out = outd / f"{name}.mp4"
        studio.sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(work / "music.wav"),
                   "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                   "-af", f"afade=t=in:d=0.3,afade=t=out:st={max(0, total - .8):.2f}:d=0.8,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100",
                   "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(out)])
        print(f"  ✔ {out} ({total:.1f}초)")
    studio.sh(["ffmpeg", "-y", "-ss", "0.3", "-i", str(outd / "short.mp4"), "-frames:v", "1", "-q:v", "3", str(outd / "cover.jpg")])
    if not os.environ.get("STUDIO_KEEP_WORK"):
        for x in segs:
            Path(x).unlink(missing_ok=True)


def cmd_lint(trip, root, fpath=None):
    errs, warns = [], []
    pf = trip.get("platform")
    if pf not in PLATFORMS:
        errs.append(f"platform 은 {list(PLATFORMS)} 중 하나")
    link = trip.get("link", "")
    if not link and not trip.get("sample"):
        errs.append("link(파트너 링크)가 비어 있음 — 파트너 대시보드에서 만든 링크를 넣으세요")
    if link and pf == "coupang" and not re.search(r"link\.coupang\.com|coupa\.ng", link):
        warns.append("쿠팡 링크가 파트너스 단축 링크(link.coupang.com) 형식이 아님 — 수익이 안 잡힐 수 있음")
    if not trip.get("credit"):
        errs.append("credit(사진·영상 출처)가 없음 — 직접 촬영 / 공공누리 1유형 출처 / 사용 허락 받은 사진만 사용")
    for i, s in enumerate(trip.get("shots", [])):
        src = s.get("clip") or s.get("img")
        if not src or not (root / src).exists():
            errs.append(f"shots[{i}] 파일 없음: {src}")
    try:
        age = (dt.date.today() - dt.date.fromisoformat(trip.get("price_checked", ""))).days
        if trip.get("price") and age > 14:
            warns.append(f"가격 확인일이 {age}일 지남 — 다시 확인하세요 (허위·과장 광고 위험)")
    except ValueError:
        if trip.get("price"):
            errs.append("price 를 쓰면 price_checked(YYYY-MM-DD) 도 필요")
    kw = trip.get("dm_keyword")
    if kw is not None and (not kw or " " in kw or len(kw) > 6):
        warns.append(f"dm_keyword '{kw}' — 띄어쓰기 없이 2~6자 (예: 부산, 다낭1) 가 오타 없이 따라 쓰기 쉬움")
    for ln in trip.get("hook", []):
        if len(ln) > 13:
            warns.append(f"후크 '{ln}' {len(ln)}자 — 줄당 13자 이하 권장")
    chars = sum(len(s.get("line", "")) for s in trip.get("shots", []))
    est = chars / 7.5 + len(trip.get("shots", [])) * .15 + float(trip.get("end_secs", 2.6))
    if not 12 <= est <= 40:
        warns.append(f"예상 길이 {est:.0f}초 — 여행 쇼츠는 15~35초 권장 (대사 {chars}자)")
    if len(trip.get("shots", [])) < 4:
        warns.append("컷 4개 이상 권장 — 2~3초마다 화면이 바뀌어야 이탈이 적음")
    first = (trip.get("shots") or [{}])[0].get("line", "")
    if first and len(first) > 28:
        warns.append("첫 대사가 김 — 첫 2초 안에 핵심(가격·장소·혜택)이 들려야 함")
    words = " ".join(s.get("line", "") for s in trip.get("shots", []))
    for bad in ("최저가 보장", "무조건 최저", "100%", "역대급 최저"):
        if bad in words or bad in " ".join(trip.get("hook", [])):
            warns.append(f"'{bad}' — 근거 없는 최상급 표현은 표시광고법 위반 소지")
    for e in errs:
        print("  ✘", e)
    for w in warns:
        print("  !", w)
    if not errs and not warns:
        print("  ✔ 점검 통과")
    return not errs


# ───────────────────────── 예시 ─────────────────────────
def paint_sample(kind, path):
    """저작권 걱정 없는 예시용 그림 사진 (실제 업로드에는 직접 찍은 사진·허락받은 사진을 쓰세요)"""
    import random
    rnd = random.Random(kind)
    w, h = 1440, 2160
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    pal = {"sunset": ((255, 150, 90), (255, 214, 160), (30, 90, 150)), "night": ((14, 20, 54), (60, 40, 110), (10, 30, 60)),
           "day": ((90, 170, 240), (200, 230, 255), (20, 120, 170)), "forest": ((150, 200, 230), (230, 240, 230), (40, 110, 70))}[kind]
    hor = int(h * .52)
    for y in range(hor):
        t = y / hor
        d.line([(0, y), (w, y)], fill=tuple(int(pal[0][i] * (1 - t) + pal[1][i] * t) for i in range(3)))
    for y in range(hor, h):
        t = (y - hor) / (h - hor)
        d.line([(0, y), (w, y)], fill=tuple(int(pal[2][i] * (1 - .5 * t)) for i in range(3)))
    if kind in ("sunset", "day"):
        sx, sy, r = w * .66, hor - 120, 150
        d.ellipse((sx - r, sy - r, sx + r, sy + r), fill=(255, 240, 200) if kind == "sunset" else (255, 255, 230))
        for i in range(60):  # 물결 반사
            y = hor + 20 + i * 18
            x = sx + rnd.uniform(-80, 80)
            d.line([(x - 120 + i, y), (x + 120 - i, y)], fill=(255, 220, 170) if kind == "sunset" else (220, 240, 255), width=4)
    if kind == "night":
        for _ in range(140):
            x, y = rnd.uniform(0, w), rnd.uniform(0, hor * .6)
            d.ellipse((x, y, x + 3, y + 3), fill=(255, 255, 230))
        for i in range(14):  # 도시 불빛
            bx, bw, bh = i * 110 - 20, rnd.randint(80, 140), rnd.randint(200, 620)
            d.rectangle((bx, hor - bh, bx + bw, hor), fill=(20, 22, 40))
            for wy in range(hor - bh + 20, hor - 10, 40):
                for wx in range(bx + 12, bx + bw - 12, 28):
                    if rnd.random() < .55:
                        d.rectangle((wx, wy, wx + 12, wy + 18), fill=(255, 210, 120))
                        d.line([(wx + 6, hor + (hor - wy) * .5), (wx + 6, hor + (hor - wy) * .5 + 30)], fill=(200, 160, 90), width=3)
    if kind == "forest":
        for i in range(5):
            base = hor + 40 - i * 60
            col = (40 + i * 20, 100 + i * 15, 70 + i * 10)
            d.polygon([(0, base)] + [(x, base - 200 - 140 * math.sin(x / 300 + i)) for x in range(0, w + 60, 60)] + [(w, base), (w, h), (0, h)], fill=col)
    if kind in ("sunset", "day"):  # 인피니티풀 + 선베드
        py = int(h * .74)
        d.rectangle((0, py, w, py + 260), fill=(70, 190, 220) if kind == "day" else (90, 150, 190))
        d.rectangle((0, py - 14, w, py), fill=(240, 235, 225))
        for i in range(3):
            x = 220 + i * 380
            d.polygon([(x, h - 260), (x + 230, h - 260), (x + 290, h - 380), (x + 250, h - 380)], fill=(250, 250, 245))
            d.rectangle((x + 20, h - 260, x + 30, h - 200), fill=(120, 110, 100))
            d.rectangle((x + 200, h - 260, x + 210, h - 200), fill=(120, 110, 100))
        d.rectangle((0, py + 260, w, h), fill=(205, 190, 170))
        for i in range(3):
            x = 220 + i * 380
            d.polygon([(x, h - 230), (x + 230, h - 230), (x + 290, h - 350), (x + 250, h - 350)], fill=(250, 250, 245))
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    im.save(path, quality=90)


def cmd_sample():
    root = REPO / "studio" / "projects" / "trip-sample"
    (root / "photos").mkdir(parents=True, exist_ok=True)
    for kind in ("sunset", "day", "night", "forest"):
        paint_sample(kind, root / "photos" / f"{kind}.jpg")
    print(f"  ✔ {root}/photos — 예시 그림 4장. trip.json 은 저장소의 예시를 그대로 씁니다")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["sample", "lint", "render", "carousel", "kit", "deal", "all"])
    ap.add_argument("trip", nargs="?")
    ap.add_argument("--font")
    a = ap.parse_args()
    if a.cmd == "sample":
        return cmd_sample()
    if not a.trip:
        sys.exit("trip.json 경로가 필요합니다")
    trip, root = load(a.trip)
    if a.cmd == "lint":
        sys.exit(0 if cmd_lint(trip, root) else 1)
    if a.cmd == "all":
        print("[점검]")
        if not cmd_lint(trip, root):
            sys.exit("점검 오류를 먼저 고치세요")
        cmd_deal(trip, root, path=a.trip) if not trip.get("deal_id") and not trip.get("sample") else None  # 번호 먼저 확정 (영상에 표시)
        cmd_render(trip, root, a.font)
        if trip.get("carousel", trip.get("style", "voice") == "insta"):
            cmd_carousel(trip, root, a.font)
        cmd_kit(trip, root)
        cmd_deal(trip, root, path=a.trip)
        return
    if a.cmd == "deal":
        return cmd_deal(trip, root, path=a.trip)
    {"render": cmd_render, "carousel": cmd_carousel, "kit": cmd_kit}[a.cmd](trip, root, a.font)


if __name__ == "__main__":
    main()
