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

    extras = {"schedule": [], "memo": []}
    for lines in blocks:
        if lines[0] in ("일정", "메모"):  # 멤버가 아니라 일정·메모 칸
            extras["schedule" if lines[0] == "일정" else "memo"] += lines[1:]
            continue
        name = ""
        if not re.search(r"https?://|youtube\.com|^@", lines[0]):
            name = lines.pop(0)
        elif " " in lines[0]:  # "이름 유튜브링크" 한 줄로 쓴 경우
            first = lines[0].split()
            name, lines[0] = " ".join(first[:-1]), first[-1]
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
    return title, [g for g in groups if g["members"]], extras


def channel_info(path):
    STATS["info_all"] += 1
    page = get(yt_url(path))
    if not page:
        return {}
    STATS["info_ok"] += 1
    return {"autoName": meta(page, "og:title"),
            "avatar": re.sub(r"=s\d+[^/]*$", "=s240-c-k-c0x00ffffff-no-rj", meta(page, "og:image"))}


DIAG = []
STATS = {"info_ok": 0, "info_all": 0, "live_ok": 0, "live_all": 0, "on": 0, "bot": 0, "other": 0}
LIVE_MARKS = ('"style":"LIVE"', "BADGE_STYLE_TYPE_LIVE_NOW", "THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE", '"iconType":"LIVE"')


def live_candidates(page):
    """채널의 '라이브' 탭에서 LIVE 배지가 붙은 영상 ID들 (예정·다시보기에는 이 배지가 없음)"""
    found = []
    # 새 화면 형식: 배지가 먼저 나오고, 그 뒤에 "contentId"(영상 ID)가 나옴
    for mk in re.finditer("THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE", page):
        m = re.compile(r'"contentId":"([\w-]{11})"').search(page, mk.end())
        if m and m.group(1) not in found:
            found.append(m.group(1))
    # 예전 화면 형식: "videoId"가 먼저 나오고, 그 뒤에 LIVE 표시가 나옴
    for mk in re.finditer(r'"style":"LIVE"|BADGE_STYLE_TYPE_LIVE_NOW', page):
        ids = re.findall(r'"videoId":"([\w-]{11})"', page[max(0, mk.start() - 20000):mk.start()])
        if ids and ids[-1] not in found:
            found.append(ids[-1])
    return found[:3]


def own_channel_id(page):
    m = (re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})', page)
         or re.search(r'"externalId":"(UC[\w-]{22})"', page)
         or re.search(r'<meta itemprop="identifier" content="(UC[\w-]{22})"', page))
    return m.group(1) if m else ""


def video_owner(vid):
    """영상을 올린(방송하는) 채널 ID. 실패하면 None"""
    page = get("https://www.youtube.com/watch?v=" + vid)
    if not page:
        return None
    m = (re.search(r'<meta itemprop="channelId" content="(UC[\w-]{22})"', page)
         or re.search(r'"videoDetails":\{"videoId":"%s".{0,3000}?"channelId":"(UC[\w-]{22})"' % re.escape(vid), page, re.S)
         or re.search(r'"externalChannelId":"(UC[\w-]{22})"', page))
    return m.group(1) if m else None


def live_video(path):
    """방송 중이면 영상 ID, 아니면 "", 확인 실패면 None.
    다른 채널의 방송(합방·협업으로 라이브 탭에 같이 뜨는 것)은 제외해요."""
    STATS["live_all"] += 1
    base = yt_url(path)
    page = get(base + "/streams")
    if page is None:
        return None
    STATS["live_ok"] += 1
    if "confirm you" in page and "not a bot" in page:
        STATS["bot"] += 1
        return None
    own = own_channel_id(page)
    cands = live_candidates(page)
    how = "라이브 탭"
    if not cands:  # 보조: /live 주소가 '지금 방송 중'인 화면으로 바로 연결되는지
        lp = get(base + "/live") or ""
        m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', lp)
        if m and '"isLiveNow":true' in lp and '"isUpcoming":true' not in lp:
            cands, how = [m.group(1)], "/live"
    vid, unsure, other = "", False, 0
    for c in cands:
        owner = video_owner(c)
        if owner is None:
            unsure = True
            continue
        if own and owner != own:
            other += 1
            STATS["other"] += 1
            continue
        vid = c
        break
    DIAG.append((path, how if vid else "", [k for k in LIVE_MARKS if k in page], other))
    if vid:
        STATS["on"] += 1
        return vid
    return None if unsure else ""


SOOP_RE = re.compile(r"(?:sooplive\.(?:co\.kr|com)/station/|ch\.sooplive\.(?:co\.kr|com)/|play\.sooplive\.(?:co\.kr|com)/"
                     r"|bj\.afreecatv\.com/|play\.afreecatv\.com/|afreecatv\.com/station/)([A-Za-z0-9_]+)", re.I)
SOOP_STATS = {"all": 0, "ok": 0, "on": 0}


def post_json(url, form):
    try:
        data = urllib.parse.urlencode(form).encode()
        req = urllib.request.Request(url, data=data, headers={"User-Agent": HEADERS["User-Agent"],
                                                              "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def soop_live(bid):
    """숲(SOOP) 방송 중이면 방송 주소, 아니면 "", 확인 실패면 None"""
    SOOP_STATS["all"] += 1
    for host in ("live.sooplive.com", "live.sooplive.co.kr"):
        j = post_json("https://%s/afreeca/player_live_api.php?bjid=%s" % (host, bid),
                      {"bid": bid, "type": "live", "player_type": "html5"})
        ch = (j or {}).get("CHANNEL") if isinstance(j, dict) else None
        if isinstance(ch, dict) and "RESULT" in ch:
            SOOP_STATS["ok"] += 1
            if str(ch.get("RESULT")) == "1":
                SOOP_STATS["on"] += 1
                return "https://play.sooplive.co.kr/%s/%s" % (bid, ch.get("BNO", "")) if ch.get("BNO") else "https://play.sooplive.co.kr/" + bid
            return ""
    page = get("https://chapi.sooplive.co.kr/api/%s/station" % bid)  # 보조 확인
    if page:
        try:
            j = json.loads(page)
            SOOP_STATS["ok"] += 1
            if j.get("broad"):
                SOOP_STATS["on"] += 1
                no = j["broad"].get("broad_no", "")
                return "https://play.sooplive.co.kr/%s/%s" % (bid, no) if no else "https://play.sooplive.co.kr/" + bid
            return ""
        except Exception:
            pass
    return None


def check(member, prev):
    old = prev.get(member["name"] or member["path"] or "", {})
    out = {"name": member["name"], "channel": "", "avatar": "", "on": False, "video": "",
           "soop": "", "links": member["links"], "autoName": ""}
    # 숲 링크가 있으면 숲 방송도 확인
    for l in member["links"]:
        m = SOOP_RE.search(l["value"])
        if m:
            v = soop_live(m.group(1))
            if v is None and old.get("soop"):
                v = old["soop"]  # 확인 실패 시 이전 상태 유지
            if v:
                out["soop"] = v
                break
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
    title, groups, extras = parse_list(text)
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
        "schedule": extras["schedule"],
        "memo": extras["memo"],
    }
    site = os.path.join(ROOT, "site")
    os.makedirs(site, exist_ok=True)
    with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
        page = f.read()
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")  # 페이지 안에 넣기 위한 처리
    with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as f:
        f.write(page.replace("__DATA__", blob))
    print("완료: %d명 중 %d명 라이브 (유튜브 %d, 숲 %d)" % (len(results), sum(bool(r["on"] or r["soop"]) for r in results),
                                                     sum(bool(r["on"]) for r in results), sum(bool(r["soop"]) for r in results)))
    print("채널 페이지 읽기 %(info_ok)d/%(info_all)d, 라이브 페이지 읽기 %(live_ok)d/%(live_all)d, "
          "방송 중 %(on)d, 다른 채널 방송 제외 %(other)d, 로봇 확인 화면 %(bot)d" % STATS)
    print("숲 확인 %(ok)d/%(all)d, 숲 방송 중 %(on)d" % SOOP_STATS)
    # 진단용: 이름 대신 목록 순서 번호만 출력 (로그가 공개라서)
    order, n = {}, 0
    for g in groups:
        for m in g["members"]:
            n += 1
            order.setdefault(m["path"], n)
            for sp in m["subs"]:
                order.setdefault(sp, "%d-서브" % n)
    for path, how, found, other in DIAG:
        if how or found or other:
            print("  %s번 멤버 → %s / LIVE표시: %s / 다른 채널 방송 제외: %d" % (order.get(path, "?"), ("방송 중(" + how + ")") if how else "꺼짐",
                                                      ", ".join(f.replace('"', "") for f in found) or "없음", other))


if __name__ == "__main__":
    main()
