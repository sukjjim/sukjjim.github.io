#!/bin/bash
# 클라우드 세션 시작 시 숙소찜 영상 도구(studio/tools)에 필요한 패키지 설치
set -euo pipefail
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi
python3 -c "import PIL, edge_tts, numpy" 2>/dev/null || pip install -q pillow edge-tts numpy
# 움직이는 사진(animate.py): 깊이 추정 모델 실행용. 실패해도 줌·패닝으로 대체
python3 -c "import onnxruntime, cv2" 2>/dev/null || pip install -q onnxruntime opencv-python-headless || true
need=()
[ -f /usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc ] || need+=(fonts-noto-cjk-extra)
command -v ffmpeg >/dev/null 2>&1 || need+=(ffmpeg)
command -v espeak-ng >/dev/null 2>&1 || need+=(espeak-ng)
if [ ${#need[@]} -gt 0 ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y -q "${need[@]}" >/dev/null 2>&1 || { apt-get update -q >/dev/null 2>&1; apt-get install -y -q "${need[@]}" >/dev/null 2>&1; }
fi
echo "숙소찜 스튜디오 준비 완료"
