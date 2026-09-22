# -*- coding: utf-8 -*-
"""
인스타그램 릴스 게시 모듈 (공식 Instagram Graph API)

흐름:
  1) 영상을 공개 URL에 올림 (GitHub Pages 또는 S3 호환 스토리지)
  2) POST /{ig_user_id}/media  (media_type=REELS, video_url, caption) → 컨테이너 ID
  3) 컨테이너 status_code 가 FINISHED 될 때까지 폴링
  4) POST /{ig_user_id}/media_publish → 게시물 ID → permalink
  5) (선택) 호스팅한 영상 파일 삭제

필요 설정(st.secrets 또는 환경변수):
  IG_USER_ID, IG_ACCESS_TOKEN
  호스팅 = github : GITHUB_TOKEN, MEDIA_REPO("owner/repo")
  호스팅 = s3     : S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, S3_ENDPOINT(옵션, R2 등), S3_REGION(옵션), S3_PUBLIC_BASE(옵션)
"""
import base64, json, os, re, time
from typing import Callable, Optional

import requests

GRAPH = "https://graph.facebook.com/v21.0"
Log = Optional[Callable[[str], None]]


def _log(log: Log, msg: str):
    if log:
        log(msg)


class PublishError(RuntimeError):
    pass


# ──────────────────────────────────────────────────────────────
# 호스팅 1: GitHub Pages
# ──────────────────────────────────────────────────────────────
def _gh(method, url, token, **kw):
    h = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    r = requests.request(method, url, headers=h, timeout=60, **kw)
    return r


def github_ensure_repo(token: str, repo: str, log: Log = None) -> str:
    """repo(owner/name)가 없으면 공개 저장소로 생성하고 Pages를 켠다. Pages 기본 URL 반환."""
    owner, name = repo.split("/", 1)
    r = _gh("GET", f"https://api.github.com/repos/{repo}", token)
    if r.status_code == 404:
        _log(log, f"저장소 {repo} 가 없어 새로 만듭니다…")
        me = _gh("GET", "https://api.github.com/user", token).json().get("login")
        url = "https://api.github.com/user/repos" if me == owner else f"https://api.github.com/orgs/{owner}/repos"
        r = _gh("POST", url, token, json={"name": name, "private": False, "auto_init": True,
                                          "description": "OGQ 릴스 생성기 - 인스타 게시용 임시 영상 호스팅"})
        if r.status_code not in (200, 201):
            raise PublishError(f"저장소 생성 실패: {r.status_code} {r.text[:200]}")
        time.sleep(3)
        _gh("PUT", f"https://api.github.com/repos/{repo}/contents/.nojekyll", token,
            json={"message": "chore: disable jekyll", "content": ""})
    elif r.status_code != 200:
        raise PublishError(f"저장소 확인 실패: {r.status_code} {r.text[:200]}")
    elif r.json().get("private"):
        raise PublishError(f"{repo} 는 비공개 저장소입니다. Pages 호스팅은 공개 저장소여야 합니다.")

    default_url = f"https://{owner}.github.io/{name}/"
    p = _gh("GET", f"https://api.github.com/repos/{repo}/pages", token)
    if p.status_code != 200:
        p2 = _gh("POST", f"https://api.github.com/repos/{repo}/pages", token,
                 json={"source": {"branch": "main", "path": "/"}})
        if p2.status_code in (200, 201):
            _log(log, "GitHub Pages를 켰습니다.")
            p = p2
        else:
            # 토큰에 Pages 권한이 없어도, 저장소 설정에서 이미 켜 두었다면 기본 URL로 진행
            _log(log, "Pages 상태를 확인할 권한이 없어 기본 주소로 진행합니다 (저장소 Settings → Pages 에서 main 브랜치가 켜져 있어야 함).")
            return default_url
    html = p.json().get("html_url") or default_url
    return html.rstrip("/") + "/"


def github_upload(token: str, repo: str, filename: str, data: bytes, log: Log = None) -> str:
    """파일을 media/ 아래 커밋하고 Pages URL 반환."""
    base = github_ensure_repo(token, repo, log)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    path = f"media/{int(time.time())}_{safe}"
    _log(log, f"GitHub에 업로드 중… ({len(data) / 1e6:.1f}MB)")
    r = _gh("PUT", f"https://api.github.com/repos/{repo}/contents/{path}", token,
            json={"message": f"media: {safe}", "content": base64.b64encode(data).decode()})
    if r.status_code not in (200, 201):
        raise PublishError(f"업로드 실패: {r.status_code} {r.text[:200]}")
    url = base + path
    _log(log, f"Pages 배포 대기 중… {url}")
    wait_public(url, timeout=420, log=log)
    return url


def github_delete(token: str, repo: str, url: str, log: Log = None):
    m = re.search(r"/(media/[^?]+)$", url)
    if not m:
        return
    path = m.group(1)
    r = _gh("GET", f"https://api.github.com/repos/{repo}/contents/{path}", token)
    if r.status_code != 200:
        return
    sha = r.json().get("sha")
    _gh("DELETE", f"https://api.github.com/repos/{repo}/contents/{path}", token,
        json={"message": f"cleanup: {path}", "sha": sha})
    _log(log, "호스팅 파일 삭제 완료")


def wait_public(url: str, timeout: int = 300, log: Log = None):
    """URL이 200으로 응답할 때까지 대기(GitHub Pages 빌드 지연 대응)."""
    t0 = time.time(); n = 0
    while time.time() - t0 < timeout:
        try:
            r = requests.head(url, timeout=20, allow_redirects=True)
            if r.status_code == 200:
                return
        except requests.RequestException:
            pass
        n += 1
        if n % 4 == 0:
            _log(log, f"아직 공개되지 않음… ({int(time.time() - t0)}초)")
        time.sleep(8)
    raise PublishError(f"영상 URL이 시간 안에 공개되지 않았습니다: {url}")


# ──────────────────────────────────────────────────────────────
# 호스팅 2: S3 호환 (AWS S3 / Cloudflare R2 / Backblaze B2 …)
# ──────────────────────────────────────────────────────────────
def s3_upload(cfg: dict, filename: str, data: bytes, log: Log = None) -> str:
    try:
        import boto3
    except ImportError:
        raise PublishError("boto3 가 설치되어 있지 않습니다 (requirements.txt 참고).")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    key = f"reels/{int(time.time())}_{safe}"
    _log(log, f"S3에 업로드 중… ({len(data) / 1e6:.1f}MB)")
    s3 = boto3.client("s3", aws_access_key_id=cfg["S3_ACCESS_KEY"], aws_secret_access_key=cfg["S3_SECRET_KEY"],
                      endpoint_url=cfg.get("S3_ENDPOINT") or None, region_name=cfg.get("S3_REGION") or None)
    extra = {"ContentType": "video/mp4"}
    if not cfg.get("S3_ENDPOINT"):
        extra["ACL"] = "public-read"
    s3.put_object(Bucket=cfg["S3_BUCKET"], Key=key, Body=data, **extra)
    base = cfg.get("S3_PUBLIC_BASE")
    if base:
        return base.rstrip("/") + "/" + key
    if cfg.get("S3_ENDPOINT"):
        return cfg["S3_ENDPOINT"].rstrip("/") + f"/{cfg['S3_BUCKET']}/{key}"
    return f"https://{cfg['S3_BUCKET']}.s3.amazonaws.com/{key}"


def s3_delete(cfg: dict, url: str, log: Log = None):
    try:
        import boto3
        key = url.split("/reels/", 1)[1]
        s3 = boto3.client("s3", aws_access_key_id=cfg["S3_ACCESS_KEY"], aws_secret_access_key=cfg["S3_SECRET_KEY"],
                          endpoint_url=cfg.get("S3_ENDPOINT") or None, region_name=cfg.get("S3_REGION") or None)
        s3.delete_object(Bucket=cfg["S3_BUCKET"], Key="reels/" + key)
        _log(log, "호스팅 파일 삭제 완료")
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
# Instagram Graph API
# ──────────────────────────────────────────────────────────────
def ig_check(ig_user_id: str, token: str) -> dict:
    r = requests.get(f"{GRAPH}/{ig_user_id}", params={"fields": "username,name,followers_count", "access_token": token}, timeout=30)
    j = r.json()
    if "error" in j:
        raise PublishError("인스타 연결 실패: " + j["error"].get("message", str(j)))
    return j


def ig_publish_reel(ig_user_id: str, token: str, video_url: str, caption: str = "",
                    share_to_feed: bool = True, cover_url: Optional[str] = None,
                    log: Log = None, timeout: int = 600) -> dict:
    """릴스 컨테이너 생성 → 처리 대기 → 게시. {media_id, permalink} 반환."""
    _log(log, "인스타 컨테이너 생성 중…")
    params = {"media_type": "REELS", "video_url": video_url, "caption": caption,
              "share_to_feed": "true" if share_to_feed else "false", "access_token": token}
    if cover_url:
        params["cover_url"] = cover_url
    r = requests.post(f"{GRAPH}/{ig_user_id}/media", data=params, timeout=60)
    j = r.json()
    if "error" in j:
        raise PublishError("컨테이너 생성 실패: " + j["error"].get("message", str(j)))
    container = j["id"]

    t0 = time.time(); n = 0
    while True:
        s = requests.get(f"{GRAPH}/{container}", params={"fields": "status_code,status", "access_token": token}, timeout=30).json()
        code = s.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise PublishError(f"인스타 처리 실패: {s.get('status')}")
        if time.time() - t0 > timeout:
            raise PublishError("인스타 처리 대기 시간 초과")
        n += 1
        if n % 3 == 0:
            _log(log, f"인스타가 영상 처리 중… ({int(time.time() - t0)}초)")
        time.sleep(5)

    _log(log, "게시 중…")
    p = requests.post(f"{GRAPH}/{ig_user_id}/media_publish", data={"creation_id": container, "access_token": token}, timeout=60).json()
    if "error" in p:
        raise PublishError("게시 실패: " + p["error"].get("message", str(p)))
    media_id = p["id"]
    link = requests.get(f"{GRAPH}/{media_id}", params={"fields": "permalink", "access_token": token}, timeout=30).json().get("permalink")
    return {"media_id": media_id, "permalink": link}


# ──────────────────────────────────────────────────────────────
# 통합
# ──────────────────────────────────────────────────────────────
def publish(cfg: dict, video_bytes: bytes, filename: str, caption: str, share_to_feed: bool = True,
            cleanup: bool = True, log: Log = None) -> dict:
    """cfg: 설정 dict. HOSTING = 'github' | 's3'."""
    hosting = (cfg.get("HOSTING") or "github").lower()
    if not cfg.get("IG_USER_ID") or not cfg.get("IG_ACCESS_TOKEN"):
        raise PublishError("IG_USER_ID / IG_ACCESS_TOKEN 이 설정되지 않았습니다.")
    if hosting == "github":
        if not cfg.get("GITHUB_TOKEN") or not cfg.get("MEDIA_REPO"):
            raise PublishError("GITHUB_TOKEN / MEDIA_REPO 가 설정되지 않았습니다.")
        url = github_upload(cfg["GITHUB_TOKEN"], cfg["MEDIA_REPO"], filename, video_bytes, log)
    elif hosting == "s3":
        for k in ("S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
            if not cfg.get(k):
                raise PublishError(f"{k} 가 설정되지 않았습니다.")
        url = s3_upload(cfg, filename, video_bytes, log)
    elif hosting == "url":
        url = cfg.get("VIDEO_URL")
        if not url:
            raise PublishError("VIDEO_URL 이 비어 있습니다.")
    else:
        raise PublishError(f"알 수 없는 호스팅: {hosting}")
    _log(log, f"영상 URL: {url}")
    try:
        result = ig_publish_reel(cfg["IG_USER_ID"], cfg["IG_ACCESS_TOKEN"], url, caption, share_to_feed, log=log)
    finally:
        if cleanup and hosting == "github":
            try:
                github_delete(cfg["GITHUB_TOKEN"], cfg["MEDIA_REPO"], url, log)
            except Exception:
                pass
        elif cleanup and hosting == "s3":
            s3_delete(cfg, url, log)
    result["video_url"] = url
    return result


def load_cfg_from_env() -> dict:
    keys = ["HOSTING", "IG_USER_ID", "IG_ACCESS_TOKEN", "GITHUB_TOKEN", "MEDIA_REPO",
            "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_ENDPOINT", "S3_REGION", "S3_PUBLIC_BASE"]
    return {k: os.environ.get(k, "") for k in keys}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="릴스 MP4를 인스타그램에 게시 (설정은 환경변수)")
    ap.add_argument("video"); ap.add_argument("--caption", default=""); ap.add_argument("--no-feed", action="store_true")
    ap.add_argument("--keep", action="store_true", help="호스팅 파일 삭제 안 함")
    a = ap.parse_args()
    cfg = load_cfg_from_env()
    with open(a.video, "rb") as f:
        data = f.read()
    res = publish(cfg, data, os.path.basename(a.video), a.caption, not a.no_feed, not a.keep, log=print)
    print(json.dumps(res, ensure_ascii=False, indent=2))
