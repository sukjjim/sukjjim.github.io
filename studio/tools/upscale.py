#!/usr/bin/env python3
"""작은 사진 AI 확대 (Real-ESRGAN x4, ONNX, CPU) — 썸네일처럼 작은 사진을 영상에 쓰기 전에 4배로 키움

사용법: python3 studio/tools/upscale.py 입력.jpg 출력.jpg
한계: 없는 디테일을 그럴듯하게 만들어 내는 것이라 원본 큰 사진보다는 못합니다. 원본이 있으면 원본을 쓰세요.
"""
import os, sys, urllib.request
from pathlib import Path

import numpy as np

URL = "https://huggingface.co/imgdesignart/realesrgan-x4-onnx/resolve/main/onnx/model.onnx"
MODEL = Path(os.environ.get("STUDIO_CACHE", Path.home() / ".cache" / "studio")) / "esrgan_x4.onnx"
T, PAD = 64, 8  # 모델 입력은 64×64 고정 → 겹치게 잘라 처리


def session():
    import onnxruntime as ort
    if not MODEL.exists():
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        print("  · 확대 모델 내려받는 중 (약 67MB, 처음 한 번)")
        urllib.request.urlretrieve(URL, MODEL)
    return ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])


def upscale(rgb, sess=None):
    sess = sess or session()
    name = sess.get_inputs()[0].name
    h, w = rgb.shape[:2]
    x = np.pad(rgb.astype(np.float32) / 255, ((PAD, PAD + T), (PAD, PAD + T), (0, 0)), mode="reflect")
    out = np.zeros((h * 4, w * 4, 3), np.float32)
    step = T - 2 * PAD
    for y in range(0, h, step):
        for xx in range(0, w, step):
            tile = x[y:y + T, xx:xx + T].transpose(2, 0, 1)[None]
            r = sess.run(None, {name: tile})[0][0].transpose(1, 2, 0)
            core = r[PAD * 4:(T - PAD) * 4, PAD * 4:(T - PAD) * 4]
            hh, ww = min(step, h - y) * 4, min(step, w - xx) * 4
            out[y * 4:y * 4 + hh, xx * 4:xx * 4 + ww] = core[:hh, :ww]
    return (np.clip(out, 0, 1) * 255).round().astype(np.uint8)


if __name__ == "__main__":
    from PIL import Image
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    im = np.asarray(Image.open(sys.argv[1]).convert("RGB"))
    Image.fromarray(upscale(im)).save(sys.argv[2], quality=95)
    print(f"  ✔ {sys.argv[2]} ({im.shape[1]}×{im.shape[0]} → {im.shape[1] * 4}×{im.shape[0] * 4})")
