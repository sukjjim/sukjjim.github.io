#!/usr/bin/env python3
"""사진 1장 → AI 영상 클립 (Flow 처럼 실제로 움직이는 영상)

무료: Hugging Face 무료 계정 토큰(HF_TOKEN)으로 Wan 2.2 이미지→영상 공개 Space 호출
  - 모델 Wan2.2-I2V-A14B: Apache-2.0 (상업적 이용 가능, 생성물 권리 주장 없음)
  - 무료 계정 하루 약 3개 (ZeroGPU 하루 5분 + 하루 3회 제한), 832×480 · 5초 · 16fps → 30fps 로 보간
  - 2026-10-08 조사: studio/research/ai-video/insights.md

사용법:
  python3 studio/tools/aivideo.py 사진.jpg 결과.mp4 ["프롬프트"]
travel.py 에서는 컷에 "ai": true (선택: "ai_prompt") 를 주면 렌더링 전에 자동으로 만들어 photos/ai_<이름>.mp4 로 저장
"""
import os, shutil, subprocess, sys
from pathlib import Path

SPACE = os.environ.get("AIVIDEO_SPACE", "zerogpu-aoti/wan2-2-fp8da-aoti-faster")
TAIL = "photorealistic, keep the original details, no new objects, no people, no text, no watermark"
DEFAULT_PROMPT = "slow cinematic camera glides forward, gentle natural motion, soft light, " + TAIL
NEGATIVE = "blurry, distorted, warped buildings, low quality, text, watermark, logo, people, extra objects"


class QuotaError(RuntimeError):
    pass


def available():
    try:
        import gradio_client  # noqa: F401
        return bool(os.environ.get("HF_TOKEN"))
    except ImportError:
        return False


def generate(img, out, prompt=None, seconds=5.0, steps=4):
    """img → out(mp4, 30fps). 한도 초과면 QuotaError"""
    from gradio_client import Client, handle_file
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise QuotaError("HF_TOKEN 이 없습니다 (huggingface.co → Settings → Access Tokens → Read 토큰)")
    prompt = prompt or DEFAULT_PROMPT
    if TAIL not in prompt:
        prompt = prompt.rstrip(", ") + ", " + TAIL
    c = Client(SPACE, token=token, verbose=False)
    try:
        res = c.predict(input_image=handle_file(str(img)), prompt=prompt, steps=steps, negative_prompt=NEGATIVE,
                        duration_seconds=float(seconds), guidance_scale=1, guidance_scale_2=1, seed=42,
                        randomize_seed=True, api_name="/generate_video")
    except Exception as e:  # 한도 초과·대기열·Space 고장
        msg = str(e)
        if "quota" in msg.lower() or "limit" in msg.lower():
            raise QuotaError(msg[:300])
        raise
    vid = res[0] if isinstance(res, (list, tuple)) else res
    path = vid.get("video") if isinstance(vid, dict) else vid
    tmp = Path(out).with_suffix(".raw.mp4")
    shutil.copy(path, tmp)
    # 16fps → 30fps (움직임 보간) — 화면이 끊겨 보이지 않게
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-an", "-vf", "minterpolate=fps=30:mi_mode=mci:mc_mode=aobmc",
                    "-c:v", "libx264", "-crf", "16", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)], check=True)
    tmp.unlink(missing_ok=True)
    return str(out)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    try:
        print("  ✔", generate(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    except QuotaError as e:
        sys.exit(f"  ! 무료 한도 초과 또는 토큰 없음: {e}")
