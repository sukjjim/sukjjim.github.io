# Flow 같은 AI 영상을 코드로 만들 수 있나 — 2026-10-08 조사

조사: 3갈래(무료 공개 서비스 · 이 컴퓨터에서 직접 · 저렴한 유료 API) × 각 1명 조사 + 1명 반박 검증. 이 컨테이너: CPU 4개, RAM 15GB, GPU 없음.

## 결론
- **이 컴퓨터에서 직접 생성: 실용적이지 않음.** 가장 가벼운 LTX-Video 2B 도 5초 클립에 7~25분(추정, 공개 CPU 벤치마크 없음), 화질은 Veo 보다 확실히 낮음. Wan 14B·LTX-2.3 은 메모리 부족으로 불가.
- **무료로 가능한 길: Hugging Face 무료 계정 토큰 + Wan 2.2 공개 Space** (`zerogpu-aoti/wan2-2-fp8da-aoti-faster`, HF ZeroGPU 팀 운영, 2026-10-08 작동 확인)
  - 모델 Wan2.2-I2V-A14B: **Apache-2.0** — 상업적 이용 가능, 매출 제한 없음, 생성물 권리 주장 없음
  - 무료 한도: 로그인 없이 하루 2분(이 컨테이너는 IP 공유라 이미 소진됨), **무료 계정 하루 5분 + 하루 3회** → 실제로 **하루 약 3개**. PRO($9/월)는 하루 40분 → 약 30개
  - 출력: 832×480, 16fps, 최대 5초, 소리 없음 → `aivideo.py` 가 30fps 로 보간. 해상도는 Flow(720p)보다 낮음
- **무료 체험이 큰 길: Alibaba Cloud Model Studio (Wan 공식 API)** — 신규 사용자 90일, 싱가포르·International, 모델마다 50초(wan2.2~2.7 i2v 합계 약 300초 ≈ 5초 클립 60개). 계정 정보 입력 필요(결제 수단 등록 가능성 있음)
- **저렴한 유료**: Seedance 1 Pro Fast (Replicate/fal) 5초 720p 약 $0.11~0.13(약 160원) → 8컷 영상 1편 약 1,300원. Veo 3.1 Lite(Gemini API) 6초 $0.30(소리 포함), Vertex AI 소리 없이 $0.18. Gemini API 의 Veo 는 무료 등급 없음
- **피할 것 (한국 사용 금지 라이선스)**: HunyuanVideo-1.5, MiniMax-H3 — 라이선스가 한국을 제외. CogVideoX 는 상업 이용 시 등록 필요

## 도구
- `studio/tools/aivideo.py` — HF_TOKEN 으로 Wan 2.2 Space 호출 → mp4 (30fps)
- `travel.py` — 컷에 `"ai": true` (선택 `"ai_prompt"`, `"ai_secs"`) → 렌더링 전에 `photos/ai_<이름>.mp4` 생성·재사용, 한도 초과면 움직이는 사진으로 대체
