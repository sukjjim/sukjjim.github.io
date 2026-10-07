# 숙소찜 하루

하루가 찜해 둔 국내 숙소를 쇼츠·릴스·틱톡으로 소개하고, 제휴 링크(마이리얼트립 · 여기어때 · 쿠팡 파트너스)로 수익을 냅니다.

- **링크 페이지**: https://sukjjim.github.io — 영상 속 번호로 숙소 찾기 (`index.html` + `deals.json`)
- **영상 도구**: `studio/tools/travel.py` — 구글 Flow AI 영상 클립(또는 사진) + 성우 내레이션 + 자막 + 음악 → 유튜브·인스타·틱톡용 3종 + 업로드 키트
- **운영 가이드**: `studio/TRAVEL.md` (벤치마킹, 제휴 규정, 광고 표시, 사진 규칙, AI 클립 프롬프트, 수익 계산)
- **로고**: `brand/` (원본 `brand/logo-source.html`)

```
python3 studio/tools/travel.py all studio/projects/<숙소>/trip.json
git add deals.json img studio/projects/<숙소> && git commit -m "<숙소> 추가" && git push
```

준비: `pip install pillow edge-tts numpy onnxruntime opencv-python-headless`, `ffmpeg`, 한글 폰트(Noto Sans CJK). 성우 목소리는 환경 변수 `GOOGLE_TTS_API_KEY`(Google Cloud Text-to-Speech).
