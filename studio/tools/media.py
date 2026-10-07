#!/usr/bin/env python3
"""공통 도구: ffmpeg 실행, 한글 폰트 찾기, 성우 음성 합성(Google Cloud TTS → edge-tts → 구글 번역 음성 → espeak-ng), 자막 구절 나누기"""
import asyncio, json, os, re, shutil, subprocess, sys

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "C:/Windows/Fonts/malgunbd.ttf",
]

def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


def dur(path):
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", str(path)]).strip())


def find_font(user_font=None):
    for p in ([user_font] if user_font else []) + FONT_CANDIDATES:
        if p and os.path.exists(p):
            return p
    sys.exit("한글 폰트를 찾지 못했습니다. --font 로 .ttf 경로를 지정하세요 (예: NanumGothic).")

def _trust_extra_ca():
    """프록시/회사망 환경: SSL_CERT_FILE 등에 지정된 CA 를 edge-tts 에도 신뢰시키기"""
    import edge_tts.communicate as ec
    for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        path = os.environ.get(k)
        if path and os.path.exists(path):
            try:
                ec._SSL_CTX.load_verify_locations(cafile=path)
            except Exception:
                pass


async def _tts(text, voice, rate, pitch, out):
    import edge_tts
    _trust_extra_ca()
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(out))


_warned = set()
CHARS_PER_MIN = 430  # 실측(4편): Google 음성 + 대사 간 여백 기준 분당 410~433자 → 짧게 추정되도록 430
FAST = False  # --fast: 가변 프레임레이트(VFR)로 인코딩 2배 빠름 (미리보기용)


def warn_once(msg):
    if msg not in _warned:
        _warned.add(msg)
        print(msg, file=sys.stderr)


def espeak_audio(text, voice, pitch, out):
    """오프라인 대체 음성(espeak-ng, 로봇 느낌). 성공 시 True"""
    if not shutil.which("espeak-ng"):
        return False
    male = any(k in voice for k in ("InJoon", "Hyunsu", "Male"))
    m = re.match(r"([+-]?\d+)Hz", pitch or "")
    p = max(0, min(99, 50 + (int(m.group(1)) * 2 if m else 0) + (-12 if male else 8)))
    wav = out.with_suffix(".wav")
    try:
        sh(["espeak-ng", "-v", "ko+" + ("m3" if male else "f3"), "-s", "150", "-p", str(p), "-w", str(wav), text])
        sh(["ffmpeg", "-y", "-i", str(wav), "-af", "volume=1.3,highpass=f=80", "-ar", "24000", "-ac", "1",
            "-q:a", "4", str(out)])
        return out.exists() and out.stat().st_size > 1000
    except Exception:
        return False
    finally:
        wav.unlink(missing_ok=True)


def google_audio(text, voice, pitch, out):
    """구글 번역 음성(네트워크 필요, 자연스러운 한국어 1종). 남성/피치는 음높이 변환으로 구분"""
    import urllib.parse, urllib.request
    chunks, cur = [], ""
    for part in re.split(r"(?<=[.!?,])\s+", text):
        if len(cur) + len(part) > 180 and cur:
            chunks.append(cur)
            cur = part
        else:
            cur = (cur + " " + part).strip()
    if cur:
        chunks.append(cur)
    raw = out.with_suffix(".raw.mp3")
    try:
        with open(raw, "wb") as fh:
            for i, c in enumerate(chunks):
                q = urllib.parse.urlencode({"client": "gtx", "ie": "UTF-8", "tl": "ko", "q": c,
                                            "total": len(chunks), "idx": i, "textlen": len(c)})
                req = urllib.request.Request("https://translate.googleapis.com/translate_tts?" + q,
                                             headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    fh.write(r.read())
        male = any(k in voice for k in ("InJoon", "Hyunsu", "Male"))
        m = re.match(r"([+-]?\d+)Hz", pitch or "")
        semi = (-4.0 if male else 0.0) + (int(m.group(1)) / 6 if m else 0)
        k = 2 ** (semi / 12)
        af = f"asetrate=24000*{k:.4f},aresample=24000,atempo={1.12 / k:.4f}"  # 음높이만 변경 + 약간 빠르게
        sh(["ffmpeg", "-y", "-i", str(raw), "-af", af, "-ac", "1", "-q:a", "3", str(out)])
        return out.exists() and out.stat().st_size > 1000
    except Exception:
        return False
    finally:
        raw.unlink(missing_ok=True)


GCLOUD_VOICES = {"male": "ko-KR-Neural2-C", "female": "ko-KR-Neural2-A"}


def gcloud_audio(text, voice, rate, pitch, out, gvoice=None):
    """Google Cloud Text-to-Speech (환경변수 GOOGLE_TTS_API_KEY 필요). 성공 시 True"""
    import base64, urllib.request
    key = os.environ.get("GOOGLE_TTS_API_KEY")
    if not key:
        return False
    male = any(k in voice for k in ("InJoon", "Hyunsu", "Male"))
    name = gvoice or GCLOUD_VOICES["male" if male else "female"]
    m = re.match(r"([+-]?\d+)Hz", pitch or "")
    r = re.match(r"([+-]?\d+)%", rate or "")
    body = {"input": {"text": text},
            "voice": {"languageCode": "ko-KR", "name": name},
            "audioConfig": {"audioEncoding": "MP3", "sampleRateHertz": 24000,
                            "speakingRate": round(1 + (int(r.group(1)) / 100 if r else 0), 2),
                            "pitch": max(-20, min(20, int(m.group(1)) / 6 if m else 0))}}
    if "Chirp" in name:  # Chirp3-HD 음성은 pitch 미지원
        body["audioConfig"].pop("pitch")
    req = urllib.request.Request("https://texttospeech.googleapis.com/v1/text:synthesize?key=" + key,
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out.write_bytes(base64.b64decode(json.loads(resp.read())["audioContent"]))
        return out.stat().st_size > 1000
    except urllib.error.HTTPError as e:
        warn_once(f"  ! Google Cloud TTS 오류 {e.code}: {e.read()[:200].decode(errors='ignore')}")
    except Exception as e:
        warn_once(f"  ! Google Cloud TTS 실패({type(e).__name__})")
    return False


def make_audio(text, voice, rate, pitch, out, gvoice=None):
    """True=고품질 음성(Google Cloud/edge-tts). False=대체본(구글 번역/espeak-ng/무음, 다음 실행에 재시도)"""
    if gcloud_audio(text, voice, rate, pitch, out, gvoice):
        warn_once("  · 음성: Google Cloud TTS")
        return True
    try:
        asyncio.run(_tts(text, voice, rate, pitch, out))
        if out.exists() and out.stat().st_size > 1000:
            return True
    except Exception as e:  # 네트워크/모듈 문제
        warn_once(f"  ! edge-tts 사용 불가({type(e).__name__}) → 대체 음성 사용")
    if google_audio(text, voice, pitch, out):
        warn_once("  · 대체 음성: 구글 번역 음성")
        return False
    if espeak_audio(text, voice, pitch, out):
        warn_once("  · 대체 음성: espeak-ng (로봇 음성)")
        return False
    warn_once("  ! 사용 가능한 음성 없음 → 무음 (sudo apt install espeak-ng)")
    secs = max(1.6, len(text) / 5.5)
    sh(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{secs:.2f}",
        "-q:a", "9", str(out)])
    return False

def phrases(text, max_chars=12):
    """쇼츠 자막: 문장을 짧은 구절로 나눔 (공백 기준, 구절당 max_chars 내외)"""
    words, out, cur = text.split(), [], ""
    for wd in words:
        if cur and len(cur) + 1 + len(wd) > max_chars:
            out.append(cur)
            cur = wd
        else:
            cur = (cur + " " + wd).strip()
    if cur:
        out.append(cur)
    if len(out) > 1 and len(out[-1]) <= 3:  # 꼬리 한두 글자는 앞 구절에 붙임
        tail = out.pop()  # (out[-2] += out.pop() 은 pop 전에 위치를 잡아 구절이 2개일 때 IndexError)
        out[-1] += " " + tail
    return out or [text]
