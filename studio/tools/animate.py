#!/usr/bin/env python3
"""사진 1장 → 실제로 움직이는 듯한 영상 (무료 · 내 컴퓨터에서 처리)

1) 깊이 추정(Depth Anything V2 small, ONNX)으로 가까운 것과 먼 것을 나눈 뒤
2) 카메라가 천천히 다가가거나 옆으로 흐르는 3D 시차(패럴랙스)를 주고
3) 하늘은 구름이 흐르고, 물은 일렁이고, 밤하늘 별은 반짝이게 합니다.

사용법:
  python3 studio/tools/animate.py 사진.jpg 결과.mp4 [--secs 1.6] [--size 1080x1350] [--move push|left|right|up|orbit|fly] [--fx auto|none|sky,water,stars]
필요: pip install onnxruntime opencv-python-headless  (모델 약 100MB 는 처음 한 번 자동으로 받음)
"""
import argparse, math, os, subprocess, sys, urllib.request
from pathlib import Path

import numpy as np

MODEL_URL = "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx"
MODEL = Path(os.environ.get("STUDIO_CACHE", Path.home() / ".cache" / "studio")) / "depth_small.onnx"
_SESS = None


def available():
    try:
        import cv2, onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def _session():
    global _SESS
    if _SESS is None:
        import onnxruntime as ort
        if not MODEL.exists():
            MODEL.parent.mkdir(parents=True, exist_ok=True)
            print("  · 깊이 모델 내려받는 중 (약 100MB, 처음 한 번)")
            urllib.request.urlretrieve(MODEL_URL, MODEL)
        so = ort.SessionOptions()
        so.intra_op_num_threads = 2
        _SESS = ort.InferenceSession(str(MODEL), so, providers=["CPUExecutionProvider"])
    return _SESS


def depth(rgb):
    """rgb(H,W,3 uint8) → 가까울수록 1, 멀수록 0 인 깊이 지도 (H,W float32)"""
    import cv2
    h, w = rgb.shape[:2]
    k = 518 / min(h, w)
    th, tw = max(14, round(h * k / 14) * 14), max(14, round(w * k / 14) * 14)
    x = cv2.resize(rgb, (tw, th), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
    x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    out = _session().run(None, {"pixel_values": x.transpose(2, 0, 1)[None].astype(np.float32)})[0][0]
    d = cv2.resize(out, (w, h), interpolation=cv2.INTER_CUBIC)
    lo, hi = np.percentile(d, 2), np.percentile(d, 98)
    d = np.clip((d - lo) / max(1e-6, hi - lo), 0, 1)
    return cv2.GaussianBlur(d, (0, 0), max(2, w / 300)).astype(np.float32)  # 경계 찢김 줄이기


def masks(rgb, d):
    """하늘·물·밤하늘 영역 추정 (0~1, 부드러운 경계)"""
    import cv2
    h, w = d.shape
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    hue, sat, val = hsv[..., 0] * 2, hsv[..., 1] / 255, hsv[..., 2] / 255
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None].repeat(w, 1)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    texture = cv2.GaussianBlur(np.abs(cv2.Laplacian(gray, cv2.CV_32F)), (0, 0), 4)
    smooth = np.clip(1 - texture / 12, 0, 1)
    far = np.clip((0.10 - d) / 0.08, 0, 1)
    # 하늘: 멀고, 밝고, 매끈하고, 화면 위쪽과 이어진 영역 (먼 숲·산은 제외)
    cand = (far > 0.5) & (val > 0.42) & (texture < 9) & (yy < 0.8)
    top = np.cumprod(cv2.morphologyEx(cand.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8)), axis=0).astype(np.float32)
    solid = cv2.dilate((~cand).astype(np.uint8), np.ones((31, 31), np.uint8)).astype(np.float32)  # 하늘이 아닌 것 + 여유 15px
    sky = cv2.GaussianBlur(top * (1 - solid), (0, 0), w / 320)  # 경계 안쪽만 (나무·산 끝이 끌려가지 않게)
    bluegreen = ((hue > 160) & (hue < 250)).astype(np.float32) * np.clip((sat - 0.25) / 0.15, 0, 1)
    water = bluegreen * smooth * np.clip((yy - 0.35) / 0.15, 0, 1) * (1 - sky) * (d < 0.85)
    water = cv2.GaussianBlur(cv2.morphologyEx(water, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)), (0, 0), w / 150)
    night = float(np.mean(val[: h // 3]) < 0.28)
    return {"sky": np.clip(sky, 0, 1), "water": np.clip(water, 0, 1) * (water.mean() > 0.02), "night": night}


def cover_crop(rgb, w, h, extra=1.10):
    """출력 비율에 맞게 자르고, 움직일 여유(extra)만큼 크게"""
    import cv2
    W, H = int(w * extra), int(h * extra)
    k = max(W / rgb.shape[1], H / rgb.shape[0])
    img = cv2.resize(rgb, (math.ceil(rgb.shape[1] * k), math.ceil(rgb.shape[0] * k)), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
    y, x = (img.shape[0] - H) // 2, (img.shape[1] - W) // 2
    return img[y:y + H, x:x + W].copy()


# 카메라 움직임 (벤치마킹 영상 실측 기준, 초당 비율): 확대 zoom, 가로 tx·세로 ty(화면 폭 대비), 기울기 roll(도)
MOVES = {
    "push": dict(zoom=.045, tx=0, ty=-.010, roll=.6),    # 천천히 다가가며 살짝 올라감
    "left": dict(zoom=.012, tx=-.045, ty=0, roll=.8),    # 옆으로 흐름 (가까운 것이 더 빨리 → 입체감)
    "right": dict(zoom=.012, tx=.045, ty=0, roll=-.8),
    "up": dict(zoom=.015, tx=0, ty=-.035, roll=.4),      # 위로 올라가며 드러남
    "orbit": dict(zoom=.010, tx=.030, ty=0, roll=2.0),   # 돌아가는 느낌
    "fly": dict(zoom=.20, tx=0, ty=-.010, roll=1.0),     # 드론처럼 쭉 들어감 (천장 창·복도·입구)
}


def frames(rgb, w, h, secs, move="push", fx="auto", fps=30, seed=0):
    """프레임 생성기 (RGB uint8 w×h). 일정한 속도로 '이미 움직이는 중'인 카메라 (긴 영상의 중간을 자른 느낌)"""
    import cv2
    mv = MOVES.get(move, MOVES["push"])
    extra = 1.12 + max(mv["zoom"] * secs * .5 if move != "fly" else 0, abs(mv["tx"]) * secs) + abs(mv["roll"]) * secs * .012
    img = cover_crop(rgb, w, h, extra)
    H0, W0 = img.shape[:2]
    d = depth(img)
    m = masks(img, d) if fx != "none" else {"sky": 0, "water": 0, "night": 0}
    # 가까운 물체 경계가 찢기지 않게: 앞쪽 깊이를 살짝 넓혀 물체가 통째로 움직이고 배경이 늘어나게
    k = max(3, W0 // 70) | 1
    d = cv2.GaussianBlur(cv2.dilate(d, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))), (0, 0), k / 3)
    want = set(fx.split(",")) if fx not in ("auto", "none") else {"sky", "water", "stars"}
    sky = m["sky"] if "sky" in want and not (m["night"] and "stars" in want) else 0
    water = m["water"] if "water" in want else 0
    stars_mask = m["sky"] if "stars" in want and m["night"] else 0
    if isinstance(sky, np.ndarray):  # 구름이 흘러가며 가져오는 자리(왼쪽)도 하늘이어야 함 → 그만큼 안쪽만
        reach = int(secs * W0 * 0.008) + 12
        sky = cv2.erode(sky, np.ones((1, 2 * reach + 1), np.uint8))
    xs, ys = np.meshgrid(np.arange(W0, dtype=np.float32), np.arange(H0, dtype=np.float32))
    cx, cy = W0 / 2, H0 / 2
    px, py = xs - cx, ys - cy
    par = 0.3 + 0.7 * d  # 시차: 먼 것 0.3, 가까운 것 1.0
    n = max(1, round(secs * fps))
    rng = np.random.default_rng(7 + seed)
    sign = 1 if rng.random() < .5 else -1  # 기울기 방향은 컷마다 다르게
    if isinstance(stars_mask, np.ndarray):  # 반짝일 별: 밤하늘의 밝은 점
        g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float32)
        bright = (g - cv2.GaussianBlur(g, (0, 0), 3)) > 25
        star_amp = (bright * stars_mask).astype(np.float32)
        phase = rng.uniform(0, 2 * np.pi, star_amp.shape).astype(np.float32)
    for i in range(n):
        tt = i / fps
        u = tt if move == "fly" else tt - (n - 1) / fps / 2  # 중간을 기준으로 앞뒤 대칭 → 시작부터 움직이는 중
        zf = 1 + mv["zoom"] * u * (0.55 + 0.45 * d)  # 다가갈수록 가까운 것이 더 커짐
        th = math.radians(mv["roll"] * u * sign)
        c, sn = math.cos(th), math.sin(th)
        qx, qy = px / zf, py / zf
        wob = W0 * .0012  # 손에 든 카메라처럼 아주 약한 흔들림
        mx = cx + c * qx + sn * qy - mv["tx"] * u * W0 * par + wob * math.sin(tt * 4.1 + seed)
        my = cy - sn * qx + c * qy - mv["ty"] * u * W0 * par + wob * math.sin(tt * 3.3 + 2 * seed)
        if isinstance(sky, np.ndarray):  # 구름 흐름
            mx = mx - sky * (tt * W0 * 0.008)
        if isinstance(water, np.ndarray):  # 물결 일렁임 (가로 물결 + 느린 흐름)
            ph = ys * 0.11 + tt * 3.0
            mx = mx + water * (np.sin(ph + np.sin(xs * 0.008 + tt) * 2) * (1.2 + 2.2 * d))
            my = my + water * (np.sin(xs * 0.04 + tt * 2.2) * (0.5 + 1.0 * d))
        out = cv2.remap(img, mx.astype(np.float32), my.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        if isinstance(stars_mask, np.ndarray):
            tw = 1 + 0.9 * np.sin(phase + tt * 6) * star_amp
            out = np.clip(out.astype(np.float32) * tw[..., None], 0, 255).astype(np.uint8)
        y0, x0 = (H0 - h) // 2, (W0 - w) // 2
        yield out[y0:y0 + h, x0:x0 + w]


def render(src, out, size=(1080, 1350), secs=1.6, move="push", fx="auto", fps=30):
    from PIL import Image
    rgb = np.asarray(Image.open(src).convert("RGB"))
    w, h = size
    p = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
                          "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(out)],
                         stdin=subprocess.PIPE)
    for fr in frames(rgb, w, h, secs, move, fx, fps):
        p.stdin.write(np.ascontiguousarray(fr).tobytes())
    p.stdin.close()
    if p.wait():
        raise RuntimeError("인코딩 실패")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--secs", type=float, default=1.6)
    ap.add_argument("--size", default="1080x1350")
    ap.add_argument("--move", default="push", choices=list(MOVES))
    ap.add_argument("--fx", default="auto")
    a = ap.parse_args()
    if not available():
        sys.exit("pip install onnxruntime opencv-python-headless 가 필요합니다")
    w, h = map(int, a.size.split("x"))
    render(a.src, a.out, (w, h), a.secs, a.move, a.fx)
    print(f"  ✔ {a.out}")


if __name__ == "__main__":
    main()
