# -*- coding: utf-8 -*-
"""OGQ 릴스 자동 생성기 — Streamlit 웹앱
실행: python3 -m streamlit run web_app.py
"""
import os, io, hashlib, tempfile, zipfile, time
import streamlit as st
from PIL import Image

import reels_gen as rg
import ig_publish as igp

st.set_page_config(page_title="OGQ 릴스 생성기", page_icon="🎬", layout="wide")

BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(BASE, "samples")

st.markdown("""
<style>
.block-container{padding-top:1.6rem;}
.fmt-desc{background:#FFF6EC;border-radius:12px;padding:12px 16px;margin:6px 0 14px 0;font-size:0.92rem;}
.small{color:#777;font-size:0.85rem;}
</style>
""", unsafe_allow_html=True)

st.title("🎬 OGQ 릴스 자동 생성기")
st.caption("스티커팩(zip)을 넣고 콘텐츠 포맷을 고르면 1080×1920 릴스 영상(MP4)을 자동으로 만들어 줍니다.")


# ──────────────────────────────────────────────────────────────
# 팩 로드
# ──────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner="스티커팩 읽는 중…")
def load_pack(data: bytes, name: str, _h: str):
    return rg.StickerPack.from_zip(data, name=name)


@st.cache_data(show_spinner=False)
def contact_sheet_png(pack_hash: str, _pack) -> bytes:
    # pack_hash가 캐시 키(밑줄 없는 인자만 해시됨), _pack은 해시 제외
    buf = io.BytesIO(); _pack.contact_sheet(cols=6, cell=150).save(buf, "PNG"); return buf.getvalue()


with st.sidebar:
    st.header("1. 스티커팩")
    up = st.file_uploader("스티커팩 zip 업로드", type=["zip"])
    samples = sorted(f for f in os.listdir(SAMPLE_DIR) if f.lower().endswith(".zip")) if os.path.isdir(SAMPLE_DIR) else []
    sample = st.selectbox("또는 샘플 선택", ["(없음)"] + samples, index=1 if samples and up is None else 0)

    data = name = None
    if up is not None:
        data, name = up.getvalue(), os.path.splitext(up.name)[0]
    elif sample != "(없음)":
        with open(os.path.join(SAMPLE_DIR, sample), "rb") as f:
            data = f.read()
        name = os.path.splitext(sample)[0]

    st.header("2. 스타일")
    theme = st.selectbox("배경 색상 테마", ["auto"] + list(rg.THEMES),
                         format_func=lambda x: {"auto": "자동(대표 스티커 색 기반)", "mint": "민트", "pink": "핑크", "yellow": "옐로",
                                                "sky": "스카이", "lavender": "라벤더", "peach": "피치"}[x])
    bg_style = st.selectbox("배경 장식", ["circles", "halftone", "checker", "plain"],
                            format_func=lambda x: {"circles": "큰 원", "halftone": "도트", "checker": "체커 프레임", "plain": "없음"}[x])
    brand = st.text_input("하단 브랜드 문구", "OGQ 마켓")

    st.subheader("음악")
    music_mode = st.radio("BGM", ["기본 음악", "파일 첨부", "음악 없음"], horizontal=True, label_visibility="collapsed")
    bgm = None; bgm_mood = "cute"
    if music_mode == "기본 음악":
        import bgm_gen
        bgm_mood = st.selectbox("분위기", list(bgm_gen.MOODS), format_func=lambda k: bgm_gen.MOODS[k])
        try:
            with open(bgm_gen.get_bgm(bgm_mood), "rb") as _f:
                st.audio(_f.read(), format="audio/wav")
        except Exception as _e:
            st.warning(f"미리듣기 실패: {_e}")
        st.markdown('<div class="small">프로그램이 직접 합성한 음악이라 저작권 걱정 없이 사용 가능합니다.</div>', unsafe_allow_html=True)
    elif music_mode == "파일 첨부":
        bgm = st.file_uploader("BGM 파일 (mp3/m4a/wav)", type=["mp3", "m4a", "wav", "aac"])
        st.markdown('<div class="small">영상 길이에 맞춰 자동 반복/컷되고 끝에 1초 페이드아웃됩니다.</div>', unsafe_allow_html=True)
    music_vol = st.slider("음악 볼륨", 0.0, 1.5, 0.8, 0.05) if music_mode != "음악 없음" else 0.0
    sfx_on = st.checkbox("효과음 (장면에 맞춰 자동: 등장 뿅·카운트다운·알림·달리기·잭팟 등)", True)
    sfx_vol = st.slider("효과음 볼륨", 0.0, 1.5, 0.9, 0.05) if sfx_on else 0.0

    st.header("3. 인스타 연결 (선택)")
    with st.expander("게시 설정", expanded=False):
        def _sec(k, d=""):
            try:
                return str(st.secrets.get(k, d) or d)
            except Exception:
                return d
        hosting_opts = {"github": "GitHub Pages (추가 가입 없음)", "s3": "S3 / Cloudflare R2", "url": "직접 URL 입력(테스트)"}
        hosting = st.selectbox("영상 호스팅", list(hosting_opts), format_func=lambda k: hosting_opts[k],
                               index=list(hosting_opts).index(_sec("HOSTING", "github")) if _sec("HOSTING", "github") in hosting_opts else 0)
        ig_cfg = {"HOSTING": hosting,
                  "IG_USER_ID": st.text_input("인스타 비즈니스 계정 ID", _sec("IG_USER_ID")),
                  "IG_ACCESS_TOKEN": st.text_input("인스타 액세스 토큰", _sec("IG_ACCESS_TOKEN"), type="password")}
        if hosting == "github":
            ig_cfg["GITHUB_TOKEN"] = st.text_input("GitHub 토큰", _sec("GITHUB_TOKEN"), type="password")
            ig_cfg["MEDIA_REPO"] = st.text_input("호스팅 저장소 (owner/repo)", _sec("MEDIA_REPO", "loveet3-hue/ogq-reels-media"))
        elif hosting == "s3":
            for k, lab, pw in [("S3_BUCKET", "버킷", False), ("S3_ACCESS_KEY", "Access Key", True), ("S3_SECRET_KEY", "Secret Key", True),
                               ("S3_ENDPOINT", "Endpoint (R2 등, S3면 비움)", False), ("S3_REGION", "Region", False), ("S3_PUBLIC_BASE", "공개 URL 베이스", False)]:
                ig_cfg[k] = st.text_input(lab, _sec(k), type="password" if pw else "default")
        else:
            ig_cfg["VIDEO_URL"] = st.text_input("공개 영상 URL", "")
        st.markdown('<div class="small">값은 Streamlit Cloud의 Secrets에 넣어 두면 자동으로 채워집니다. 코드에 저장되지 않습니다.</div>', unsafe_allow_html=True)
        if st.button("연결 테스트", use_container_width=True):
            try:
                info = igp.ig_check(ig_cfg["IG_USER_ID"], ig_cfg["IG_ACCESS_TOKEN"])
                st.success(f"인스타 OK: @{info.get('username')} (팔로워 {info.get('followers_count', '?')})")
                if hosting == "github":
                    base = igp.github_ensure_repo(ig_cfg["GITHUB_TOKEN"], ig_cfg["MEDIA_REPO"], st.write)
                    st.success(f"호스팅 OK: {base}")
            except Exception as e:
                st.error(str(e))

if data is None:
    st.info("왼쪽에서 스티커팩 zip을 업로드하거나 샘플을 선택하세요. (main.png / tab.png / 1.png … 구조)")
    st.stop()

h = hashlib.md5(data).hexdigest()
pack = load_pack(data, name, h)
st.session_state["_pack"] = pack

# ──────────────────────────────────────────────────────────────
# 레이아웃
# ──────────────────────────────────────────────────────────────
left, right = st.columns([1.05, 1])

with right:
    st.subheader(f"📦 {pack.name}  ·  스티커 {len(pack)}개")
    st.image(contact_sheet_png(h, pack), caption="번호를 보고 아래 입력칸에 스티커 번호를 지정하세요", use_container_width=True)

with left:
    st.subheader("3. 포맷 선택")
    cats = []
    for f in rg.FORMATS.values():
        if f.category not in cats:
            cats.append(f.category)
    cat = st.radio("유형", ["전체"] + cats, horizontal=True)
    fmts = [f for f in rg.FORMATS.values() if cat == "전체" or f.category == cat]
    spec = st.selectbox("포맷 (주제)", fmts, format_func=lambda f: f"{f.id[1:]}. {f.name}  [{f.category}]")
    st.markdown(f'<div class="fmt-desc"><b>장면 구성</b> · {spec.scene_desc}'
                + (f'<br><span class="small">💡 {spec.tip}</span>' if spec.tip else "") + "</div>", unsafe_allow_html=True)

    st.subheader("4. 내용 입력")
    defaults = spec.defaults(pack)
    params = {}
    n = len(pack)
    for fld in spec.fields:
        key = f"{spec.id}_{fld.key}_{h[:6]}"
        dv = defaults.get(fld.key)
        if fld.type == "text":
            params[fld.key] = st.text_input(fld.label, value=str(dv or ""), key=key, help=fld.help or None)
        elif fld.type in ("textarea", "pairs"):
            txt = dv if isinstance(dv, str) else "\n".join(map(str, dv or []))
            rows = max(3, min(10, txt.count("\n") + 2))
            params[fld.key] = st.text_area(fld.label, value=txt, key=key, height=28 * rows + 20, help=fld.help or None)
        elif fld.type == "sticker":
            params[fld.key] = st.number_input(fld.label, min_value=1, max_value=n, value=int(dv or 1), step=1, key=key)
        elif fld.type == "stickers":
            opts = list(range(1, n + 1))
            dl = [int(x) for x in (dv or []) if 1 <= int(x) <= n]
            params[fld.key] = st.multiselect(fld.label + (f" (권장 {fld.n}개)" if fld.n else ""), opts, default=dl, key=key)
        elif fld.type == "int":
            params[fld.key] = st.number_input(fld.label, value=int(dv or 0), step=1, key=key,
                                              min_value=int(fld.min) if fld.min is not None else None,
                                              max_value=int(fld.max) if fld.max is not None else None)
        elif fld.type == "float":
            params[fld.key] = st.number_input(fld.label, value=float(dv or 0), step=0.1, key=key,
                                              min_value=float(fld.min) if fld.min is not None else None,
                                              max_value=float(fld.max) if fld.max is not None else None)
        elif fld.type == "choice":
            params[fld.key] = st.selectbox(fld.label, fld.options, index=fld.options.index(dv) if dv in fld.options else 0, key=key)
        elif fld.type == "image":
            upimg = st.file_uploader(fld.label, type=["png", "jpg", "jpeg", "webp"], key=key)
            params[fld.key] = upimg.getvalue() if upimg is not None else None

    c1, c2 = st.columns(2)
    do_preview = c1.button("🖼 미리보기 (6프레임)", use_container_width=True)
    do_render = c2.button("🎬 영상 생성", type="primary", use_container_width=True)


def _audio_path():
    if music_mode == "음악 없음":
        return None
    if music_mode == "기본 음악":
        return rg.resolve_audio(bgm_mood)
    if bgm is None:
        return None
    ext = os.path.splitext(bgm.name)[1] or ".mp3"
    p = os.path.join(tempfile.gettempdir(), f"reels_bgm_{h[:6]}{ext}")
    with open(p, "wb") as f:
        f.write(bgm.getvalue())
    return p


def _build(spec_, params_):
    return rg.build_timeline(pack, spec_.id, params_, theme=theme, bg_style=bg_style, brand=brand)


def _render_bytes(tl, label=""):
    out = os.path.join(tempfile.gettempdir(), f"reels_{h[:6]}_{int(time.time() * 1000)}.mp4")
    bar = st.progress(0, text=f"렌더링 중… {label}")
    def prog(i, nn):
        if i % 10 == 0 or i == nn:
            bar.progress(i / nn, text=f"렌더링 중… {label} {i}/{nn} 프레임")
    rg.render_video(tl, out, audio_path=_audio_path(), progress=prog, volume=music_vol, sfx=sfx_on, sfx_volume=sfx_vol)
    bar.empty()
    with open(out, "rb") as f:
        b = f.read()
    os.remove(out)
    return b


with right:
    if do_preview:
        tl = _build(spec, params)
        frames = tl.preview(6)
        st.markdown(f"**미리보기** · 총 {tl.total:.1f}초")
        cols = st.columns(6)
        for c, fr in zip(cols, frames):
            c.image(fr.resize((270, 480)), use_container_width=True)

    if do_render:
        tl = _build(spec, params)
        t0 = time.time()
        vid = _render_bytes(tl, spec.name)
        st.session_state["last_video"] = {"bytes": vid, "name": f"{pack.name}_{spec.id}_{spec.name}.mp4".replace("/", "·"),
                                          "info": f"{tl.total:.1f}초 영상 · {len(vid) / 1e6:.1f}MB · 렌더 {time.time() - t0:.0f}초",
                                          "fmt": spec.name, "meta": dict(tl.ctx.meta)}

    lv = st.session_state.get("last_video")
    if lv:
        st.success("완료! " + lv["info"])
        if lv.get("meta"):
            st.info("🎲 이번 영상의 랜덤 결과 → " + " / ".join(f"{k}: {v}" for k, v in lv["meta"].items()) + "  (생성할 때마다 바뀜)")
        st.video(lv["bytes"])
        st.download_button("⬇️ MP4 다운로드", lv["bytes"], file_name=lv["name"], mime="video/mp4", use_container_width=True)

        with st.expander("📤 인스타그램에 바로 게시", expanded=False):
            caption = st.text_area("캡션 (해시태그 포함)", f"{pack.name} 🎬 {lv['fmt']}\n#OGQ #OGQ마켓 #스티커 #이모티콘", height=120, key="ig_caption")
            c1, c2 = st.columns(2)
            share_feed = c1.checkbox("피드에도 표시", True)
            cleanup = c2.checkbox("게시 후 호스팅 파일 삭제", True)
            st.caption("공식 Instagram Graph API를 사용합니다. 하루 25개 한도, 처리에 1~3분 걸립니다.")
            if st.button("🚀 지금 게시", type="primary", use_container_width=True):
                if not ig_cfg.get("IG_USER_ID") or not ig_cfg.get("IG_ACCESS_TOKEN"):
                    st.error("왼쪽 '인스타 연결' 설정을 먼저 채워 주세요.")
                else:
                    box = st.status("게시 진행 중…", expanded=True)
                    try:
                        res = igp.publish(ig_cfg, lv["bytes"], lv["name"], caption, share_feed, cleanup, log=box.write)
                        box.update(label="게시 완료!", state="complete")
                        st.success(f"게시됨 → {res.get('permalink') or res.get('media_id')}")
                        if res.get("permalink"):
                            st.link_button("인스타에서 보기", res["permalink"], use_container_width=True)
                    except Exception as e:
                        box.update(label="게시 실패", state="error")
                        st.error(str(e))

# ──────────────────────────────────────────────────────────────
# 일괄 생성
# ──────────────────────────────────────────────────────────────
st.divider()
st.subheader("5. 일괄 생성 (기본값으로 여러 포맷 한 번에)")
st.caption("텍스트는 각 포맷 기본 문구, 스티커는 팩에서 고르게 자동 선택됩니다. 세부 편집은 위에서 포맷별로.")
all_ids = list(rg.FORMATS)
chosen = st.multiselect("생성할 포맷", all_ids, default=["F05", "F08", "F12", "F16", "F20"],
                        format_func=lambda i: f"{i} {rg.FORMATS[i].name}")
if st.button("📦 선택한 포맷 전부 생성 → zip", use_container_width=True) and chosen:
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_STORED) as zf:
        for fid in chosen:
            sp = rg.FORMATS[fid]
            tl = _build(sp, sp.defaults(pack))
            vid = _render_bytes(tl, f"{fid} {sp.name}")
            zf.writestr(f"{pack.name}_{fid}_{sp.name}.mp4".replace("/", "·"), vid)
            st.write(f"✅ {fid} {sp.name} · {tl.total:.1f}초")
    st.download_button("⬇️ zip 다운로드", zbuf.getvalue(), file_name=f"{pack.name}_릴스.zip", mime="application/zip",
                       use_container_width=True)
