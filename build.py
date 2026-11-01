"""저장소 비밀 설정(MEMBER_LIST)에 넣은 비밀 Gist 주소에서 목록을 읽어 각 유튜브 채널의 이름·프로필 사진·라이브 여부를 확인하고
site/index.html 하나에 담아 만듭니다. GitHub Actions가 10분마다 실행합니다.
저장소가 공개라서 실행 기록(로그)도 공개되므로, 목록 내용은 절대 로그에 출력하지 않습니다."""
import json, os, re, html, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Cookie": "SOCS=CAI; CONSENT=YES+",
}


def get(url):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None  # 로그가 공개라서 주소를 출력하지 않음


def channel_path(link):
    s = urllib.parse.unquote(link.strip())
    m = re.search(r"youtube\.com/(@[^/?#\s]+|channel/UC[\w-]{22}|c/[^/?#\s]+|user/[^/?#\s]+)", s, re.I)
    if m:
        return m.group(1)
    if re.fullmatch(r"@\S+", s):
        return s
    m = re.search(r"youtube\.com/([^/?#\s]+)/?$", s, re.I)
    if m and m.group(1).lower() not in ("watch", "live", "shorts", "playlist", "results", "feed"):
        return "c/" + m.group(1)
    return None


def yt_url(path):
    return "https://www.youtube.com/" + urllib.parse.quote(path, safe="/@")


def meta(page, prop):
    m = re.search(r'<meta property="%s" content="([^"]*)"' % re.escape(prop), page)
    return html.unescape(m.group(1)) if m else ""


def parse_list(text):
    title, groups = "", [{"title": "", "members": []}]
    cur = None
    skipped = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            cur = None
            continue
        if line.startswith("#"):
            continue
        head, _, rest = line.partition(" ")
        rest = rest.strip()
        if head == "제목":
            title = rest
            cur = None
        elif head == "묶음":
            groups.append({"title": rest, "members": []})
            cur = None
        elif cur is None:
            path = channel_path(line)
            if not path:
                skipped.append(1)
                continue
            cur = {"path": path, "name": "", "links": []}
            groups[-1]["members"].append(cur)
        elif head == "이름":
            cur["name"] = rest
        else:
            parts = line.split()
            value = parts[-1]
            label = " ".join(parts[:-1])
            cur["links"].append({"label": label, "value": value})
    groups = [g for g in groups if g["members"]]
    if skipped:
        print("유튜브 채널 링크로 알아보지 못한 줄: %d개" % len(skipped))
    return title, groups


def check(member, prev):
    path = member["path"]
    url = yt_url(path)
    info = {"name": "", "avatar": ""}
    page = get(url)
    if page:
        info["name"] = meta(page, "og:title")
        info["avatar"] = re.sub(r"=s\d+[^/]*$", "=s240-c-k-c0x00ffffff-no-rj", meta(page, "og:image"))
    old = prev.get(path, {})
    live_page = get(url + "/live")
    on, video = False, ""
    if live_page:
        m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', live_page)
        if m and '"isLiveNow":true' in live_page:
            on, video = True, m.group(1)
    elif old:
        on, video = old.get("on", False), old.get("video", "")  # 확인 실패 시 이전 상태 유지
    return {
        "path": path,
        "channel": url,
        "name": member["name"] or info["name"] or old.get("autoName") or path.lstrip("@"),
        "autoName": info["name"] or old.get("autoName", ""),
        "avatar": info["avatar"] or old.get("avatar", ""),
        "on": on,
        "video": video,
        "links": member["links"],
    }


def read_gist(url):
    m = re.search(r"gist\.github(?:usercontent)?\.com/([^/\s]+)/([0-9a-f]+)", url)
    if not m:
        print("MEMBER_LIST가 Gist 주소 모양이 아니에요.")
        return ""
    raw = "https://gist.githubusercontent.com/%s/%s/raw?t=%d" % (m.group(1), m.group(2), datetime.now().timestamp())
    try:
        with urllib.request.urlopen(raw, timeout=20) as r:
            return r.read().decode("utf-8-sig")
    except Exception:
        print("Gist 목록을 읽지 못했어요. 주소가 맞는지 확인해 주세요.")
        return ""


def load_prev():
    url = os.environ.get("PREV_DATA_URL")
    if not url:
        return {}
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            page = r.read().decode("utf-8")
        m = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S)
        data = json.loads(m.group(1))
        return {m["path"]: m for g in data.get("groups", []) for m in g["members"]}
    except Exception:
        return {}


def main():
    text = os.environ.get("MEMBER_LIST", "").strip()
    if text.startswith("http"):  # 비밀 Gist 주소면 그 안의 목록을 가져옴
        text = read_gist(text)
    if not text.strip() and os.path.exists(os.path.join(ROOT, "목록.txt")):  # 내 컴퓨터에서 시험할 때만
        with open(os.path.join(ROOT, "목록.txt"), encoding="utf-8-sig") as f:
            text = f.read()
    if not text.strip():
        print("MEMBER_LIST 설정이 비어 있어요. Settings → Secrets and variables → Actions → Secrets에 넣어 주세요.")
    title, groups = parse_list(text)
    prev = load_prev()
    members = [m for g in groups for m in g["members"]]
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda m: check(m, prev), members))
    i = 0
    out_groups = []
    for g in groups:
        n = len(g["members"])
        out_groups.append({"title": g["title"], "members": results[i:i + n]})
        i += n
    data = {
        "title": title or "라이브 보드",
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "groups": out_groups,
    }
    site = os.path.join(ROOT, "site")
    os.makedirs(site, exist_ok=True)
    with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
        page = f.read()
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")  # 페이지 안에 넣기 위한 처리
    with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as f:
        f.write(page.replace("__DATA__", blob))
    print("완료: %d명 중 %d명 라이브" % (len(results), sum(r["on"] for r in results)))


if __name__ == "__main__":
    main()
