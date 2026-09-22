# OGQ 릴스 자동 생성기

스티커팩(zip: `main.png`, `tab.png`, `1.png`…)을 넣고 콘텐츠 포맷을 고르면
1080×1920 릴스 영상(MP4, H.264)을 자동으로 렌더링합니다.

## 실행

```bash
pip install -r requirements.txt
python3 -m streamlit run web_app.py
```

CLI:

```bash
python3 reels_gen.py --list                                  # 포맷 목록
python3 reels_gen.py 팩.zip -f F04 -o out.mp4                # 단일 포맷
python3 reels_gen.py 팩.zip -f F04 -p '{"question":"..."}'   # 파라미터 지정
python3 reels_gen.py 팩.zip --demo                           # 전체 포맷 → output/
python3 reels_gen.py 팩.zip --demo --preview                 # 6프레임 미리보기 PNG
python3 reels_gen.py 팩.zip -f F16 --audio bgm.mp3           # BGM 합성(자동 루프)
```

## 구현된 포맷 (콘텐츠 포맷 리스트 시트 번호 기준)

| ID | 유형 | 포맷 |
|---|---|---|
| F01 | 참여형 | 화면 인터랙션 유도 |
| F02 | 참여형 | 떨어지는 스티커 받기 (슬라이딩 스티커는 인스타에서 추가) |
| F03 | 참여형 | 그림자 퀴즈 |
| F04 | 참여형 | 이지선다 |
| F05 | 참여형 | 멈춰서 뽑기 |
| F06 | 참여형 | 캐릭터 경주 예측 |
| F07 | 참여형 | 사다리 타기 |
| F08 | 참여형 | 유형 그리드 고르기 |
| F09 | 참여형 | 우리 사이 유형 고르기 |
| F10 | 참여형 | 옆집/자리 고르기 (아파트) |
| F11 | 서비스 연결형 | 인기 스티커 TOP 5 |
| F12 | 서비스 연결형 | 캐릭터 소개 |
| F13 | 서비스 연결형 | 스티커로만 대화하기 |
| F14 | 서비스 연결형 | 시즌/기념일 |
| F15 | 서비스 연결형 | 출시 카운트다운 티저 (로고 업로드 가능) |
| F16 | 귀여움/중독형 | 귀여움 5초 충전 |
| F17 | 귀여움/중독형 | 트렌드 오디오 챌린지 (BPM 맞춤, 오디오는 인스타에서) |
| F18 | 귀여움/중독형 | 주파수 |
| F19 | 공감형 | 상황별 표정 |
| F20 | 공감형 | 요일별 내 상태 |
| F21 | 공감형 | 직장인 하루 |
| F22 | 공감형 | 캐릭터 일상 (모션) |
| F23 | 공감형 | MBTI별 반응 |
| F24 | 공감형 | 감정 시각화 단일 컷 루프 |
| F25 | 공감형 | 날씨 맞춤 |
| F26 | 참여형 | 틀린 하나 찾기 (좌우반전/색/기울기/흑백) |
| F27 | 참여형 | 숨은 캐릭터 찾기 |
| F28 | 참여형 | 슬롯머신 뽑기 (잭팟 + 오늘의 문구) |
| F29 | 공감형 | 티어표 (S/A/B/C 배치 애니) |
| F30 | 공감형 | 알림 폭탄 (잠금화면 알림 스택) |

시트의 25개 주제 전부 + 트렌드 포맷 5종(26~30) 대응. 2번의 슬라이딩 스티커 인터랙션과 17번의 트렌드 오디오는
저작권/플랫폼 제약으로 인스타그램 업로드 단계에서 추가해야 함(영상은 그 배경용).

## 구조
- `reels_gen.py` — 엔진 + 포맷 정의(`FORMATS`). 새 포맷은 `build_fXX` 함수 + `register(FormatSpec(...))`.
- `web_app.py` — Streamlit UI.
- `fonts/` — Jua(제목), Pretendard(본문). 모두 OFL.
- 렌더링: Pillow로 프레임 생성 → `imageio-ffmpeg` 내장 ffmpeg로 H.264 인코딩(별도 ffmpeg 설치 불필요).

## 인스타그램 바로 게시 (선택)

공식 Instagram Graph API로 릴스를 게시합니다. 영상 생성 후 "📤 인스타그램에 바로 게시"에서 캡션을 넣고 게시.

- 인스타는 **공개 URL**에서 영상을 가져가므로 호스팅이 필요합니다. 기본은 GitHub Pages(공개 저장소, 추가 가입 없음),
  S3/Cloudflare R2도 지원. 게시가 끝나면 호스팅 파일은 자동 삭제됩니다.
- 설정값은 `.streamlit/secrets.toml.example` 참고. Streamlit Cloud에서는 앱 설정 → Secrets에 붙여넣기.
- 필요한 것
  1. 인스타 프로페셔널 계정 + 연결된 페이스북 페이지
  2. `instagram_content_publish` 권한이 포함된 장기 액세스 토큰 (Meta for Developers → 앱 → Graph API 탐색기)
  3. GitHub Fine-grained 토큰: 호스팅 저장소에 Contents(write), Pages(write), Administration(write)
- 한도: 계정당 24시간에 25개. 트렌드 오디오·슬라이딩 스티커는 API로 넣을 수 없음(인스타 앱에서 추가).

CLI:

```bash
IG_USER_ID=... IG_ACCESS_TOKEN=... GITHUB_TOKEN=... MEDIA_REPO=loveet3-hue/ogq-reels-media \
python3 ig_publish.py output/영상.mp4 --caption "캡션 #해시태그"
```
