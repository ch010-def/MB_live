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
    """멤버마다 빈 줄로 구분. 첫 줄 = 이름, 첫 유튜브 링크 = 대표 채널,
    나머지 유튜브 링크와 "이름 링크" 줄 = 더보기."""
    title, groups = "", [{"title": "", "members": []}]
    blocks, cur = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if not line:
            if cur:
                blocks.append(cur)
            cur = []
            continue
        head, _, rest = line.partition(" ")
        if not cur and head == "제목":
            title = rest.strip()
        elif not cur and head == "묶음":
            groups.append({"title": rest.strip(), "members": []})
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)

    for lines in blocks:
        name = ""
        if not re.search(r"https?://|youtube\.com|^@", lines[0]):
            name = lines.pop(0)
        member = {"name": name, "path": None, "subs": [], "links": []}
        for line in lines:
            parts = line.split()
            value = parts[-1]
            label = " ".join(parts[:-1])
            path = channel_path(value) if re.search(r"youtube\.com|^@", value) else None
            if path:
                if member["path"] is None:
                    member["path"] = path
                    continue
                member["subs"].append(path)
                label = label or ("유튜브 %d" % (len(member["subs"]) + 1))
                value = "youtube.com/" + path
            member["links"].append({"label": label, "value": re.sub(r"^https?://", "", value)})
        groups[-1]["members"].append(member)
    return title, [g for g in groups if g["members"]]


def channel_info(path):
    STATS["info_all"] += 1
    page = get(yt_url(path))
    if not page:
        return {}
    STATS["info_ok"] += 1
    return {"autoName": meta(page, "og:title"),
            "avatar": re.sub(r"=s\d+[^/]*$", "=s240-c-k-c0x00ffffff-no-rj", meta(page, "og:image"))}


STATS = {"info_ok": 0, "info_all": 0, "live_ok": 0, "live_all": 0, "watch": 0, "on": 0, "bot": 0}


def live_video(path):
    """방송 중이면 영상 ID, 아니면 "", 확인 실패면 None"""
    STATS["live_all"] += 1
    page = get(yt_url(path) + "/live")
    if page is None:
        return None
    STATS["live_ok"] += 1
    if "confirm you" in page and "not a bot" in page:
        STATS["bot"] += 1
        return None
    m = (re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', page)
         or re.search(r'<meta property="og:url" content="https://www\.youtube\.com/watch\?v=([\w-]{11})"', page))
    if not m:
        return ""
    STATS["watch"] += 1
    live = ('"isLiveNow":true' in page or '"isLive":true' in page
            or '"isLiveContent":true' in page and '"isUpcoming":true' not in page and '"endTimestamp"' not in page)
    if live:
        STATS["on"] += 1
        return m.group(1)
    return ""


def check(member, prev):
    old = prev.get(member["name"] or member["path"] or "", {})
    out = {"name": member["name"], "channel": "", "avatar": "", "on": False, "video": "",
           "links": member["links"], "autoName": ""}
    if not member["path"]:
        out["name"] = out["name"] or "(이름 없음)"
        return out
    info = channel_info(member["path"])
    out["channel"] = yt_url(member["path"])
    out["autoName"] = info.get("autoName") or old.get("autoName", "")
    out["avatar"] = info.get("avatar") or old.get("avatar", "")
    out["name"] = member["name"] or out["autoName"] or member["path"].lstrip("@")
    failed = False
    for path in [member["path"]] + member["subs"]:  # 대표 채널과 서브 채널 모두 확인
        v = live_video(path)
        if v:
            out["on"], out["video"] = True, v
            break
        if v is None:
            failed = True
    if not out["on"] and failed and old.get("on"):
        out["on"], out["video"] = True, old.get("video", "")  # 확인 실패 시 이전 상태 유지
    return out


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
        return {m["name"]: m for g in data.get("groups", []) for m in g["members"]}
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
    print("채널 페이지 읽기 %(info_ok)d/%(info_all)d, 라이브 페이지 읽기 %(live_ok)d/%(live_all)d, "
          "방송 화면으로 연결 %(watch)d, 방송 중 %(on)d, 로봇 확인 화면 %(bot)d" % STATS)


if __name__ == "__main__":
    main()
