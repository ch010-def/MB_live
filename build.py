"""저장소 비밀 설정(MEMBER_LIST)에 넣은 비밀 Gist 주소에서 목록을 읽어 각 유튜브 채널의 이름·프로필 사진·라이브 여부를 확인하고
site/index.html 하나에 담아 만듭니다. GitHub Actions가 10분마다 실행합니다.
저장소가 공개라서 실행 기록(로그)도 공개되므로, 목록 내용은 절대 로그에 출력하지 않습니다."""
import json, os, re, html, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Cookie": "SOCS=CAI; CONSENT=YES+",
}


def get(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
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


def looks_member(lines):
    """일정·메모 뒤에 오는 칸이 멤버인지: 첫 줄은 이름(주소·날짜 아님), 둘째 줄부터 채널 주소(유튜브 채널, 숲 방송국, 펀네이션)가 있음.
    게시글 주소(/post/)나 날짜로 시작하는 칸은 메모로 봐요."""
    if not lines:
        return False
    first = lines[0]
    if re.search(r"https?://|youtube\.com|sooplive|afreecatv|funnation|funn\.at", first) and " " not in first.strip():
        return True  # 주소만 한 줄로 시작하는 멤버
    if re.match(r"^\d{1,2}\s*[/.월]", first):
        return False
    for l in lines[1:]:
        v = l.split()[-1] if l.split() else ""
        if "/post/" in v:
            continue
        if channel_path(v) and re.search(r"youtube\.com|^@", v):
            return True
        if re.search(r"(sooplive\.(co\.kr|com)/station/|afreecatv\.com/station/|bj\.afreecatv\.com/)[A-Za-z0-9_]+/?$", v, re.I):
            return True
        if re.search(r"(funnation\.co\.kr|funn\.at)/(store/)?[A-Za-z0-9_-]+/?$|[A-Za-z0-9_-]+\.funn\.at/?$", v, re.I):
            return True
    return False


def parse_list(text):
    """멤버마다 빈 줄로 구분. 첫 줄 = 이름, 첫 유튜브 링크 = 대표 채널,
    나머지 유튜브 링크와 "이름 링크" 줄 = 더보기."""
    title, groups = "", [{"title": "", "members": []}]
    # 1단계: 빈 줄로 칸 나누기 (줄은 원래 모양 그대로 둠)
    raw_blocks, cur = [], []
    for raw in text.splitlines():
        if not raw.strip():
            if cur:
                raw_blocks.append(cur)
            cur = []
            continue
        cur.append(raw.rstrip())
    if cur:
        raw_blocks.append(cur)

    # 2단계: 칸마다 멤버 / 일정 / 메모 구분
    # 일정·메모 칸 뒤에 링크 없는 칸이 이어지면 같은 일정·메모로 봄 (메모 안에 빈 줄을 넣어도 되게)
    LINK = re.compile(r"https?://|youtube\.com|sooplive|afreecatv|funnation|funn\.at|^@")
    HEADS = ("제목", "묶음", "일정", "메모")
    extras = {"schedule": [], "memo": []}
    blocks, extra = [], None
    for blk in raw_blocks:
        real = [l.strip() for l in blk if not l.strip().startswith("#")]
        if extra and (not real or (real[0].partition(" ")[0] not in HEADS and not looks_member(real))):
            if extra == "memo":
                extras["memo"] += [""] + blk  # 빈 줄 = 주제 사이 간격, '#' 줄도 메모로 남김
            else:
                extras["schedule"] += real
            continue
        extra = None
        lines = real
        while lines and lines[0].partition(" ")[0] in ("제목", "묶음"):
            head, _, rest = lines.pop(0).partition(" ")
            if head == "제목":
                title = rest.strip()
            else:
                groups.append({"title": rest.strip(), "members": []})
        if not lines:
            continue
        if lines[0] in ("일정", "메모"):  # 멤버가 아니라 일정·메모 칸
            extra = "schedule" if lines[0] == "일정" else "memo"
            if extra == "memo":  # 메모는 '#' 줄과 들여쓰기까지 원래대로
                start = next(i for i, l in enumerate(blk) if l.strip() == "메모")
                extras["memo"] += blk[start + 1:]
            else:
                extras["schedule"] += lines[1:]
            continue
        blocks.append(lines)

    for lines in blocks:
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


def split_pages(text):
    """'페이지 이름' 줄로 목록을 나눔. 첫 부분 = 기본 페이지(엠비크루), 그 뒤 = 계열사 페이지(MBU 등)"""
    secs = [["", []]]
    for raw in text.splitlines():
        m = re.match(r"^\s*페이지\s+(\S.*?)\s*$", raw)
        if m:
            secs.append([m.group(1), []])
            continue
        secs[-1][1].append(raw)
    pages, used = [], set()
    for i, (label, lines) in enumerate(secs):
        if i and not any(l.strip() and not l.strip().startswith("#") for l in lines):
            continue
        title, groups, extras = parse_list("\n".join(lines))
        key = "" if i == 0 else (re.sub(r"[^a-z0-9]", "", label.lower()) or "p%d" % i)
        while key in used:
            key += "2"
        used.add(key)
        pages.append({"key": key, "label": label, "title": title, "groups": groups, "extras": extras})
    return share_extras(pages)


def share_extras(pages):
    """일정·메모를 한 곳에 적고 줄 앞 표시로 보일 페이지를 정함.
    표시 없음 = 기본 페이지(엠비크루), [공]/[공통] = 모든 페이지, [마]/[MBU]/[마범대] = 그 페이지, [엠]/[엠비크루] = 기본 페이지.
    메모는 주제(빈 줄로 나뉜 덩어리)의 첫 줄 표시로 정해요. 일정은 페이지마다 날짜순 정렬."""
    names = {}
    for p in pages:
        for n in (p["label"], p["title"]):
            if n:
                names[n.strip().lower()] = p["key"]
    names.update({"엠비": "", "엠비크루": names.get("엠비크루", "")})
    if pages:
        names.setdefault((pages[0]["title"] or "").lower(), "")
    # 한 글자 줄임 표시: 페이지 제목 첫 글자 (엠비크루 → [엠], 마범대 → [마]). 겹치면 쓰지 않음
    firsts = {}
    for p in pages:
        t = (p["title"] or p["label"] or "").strip()
        if t:
            firsts.setdefault(t[0].lower(), []).append(p["key"])
    for ch, ks in firsts.items():
        if len(ks) == 1:
            names.setdefault(ch, ks[0])
    allkeys = [p["key"] for p in pages]
    tag_re = re.compile(r"^\s*\[([^\]]{1,15})\]\s*")

    def targets(line, own):
        own = allkeys[0] if allkeys else own  # 표시 없음 = 기본 페이지(엠비크루). 일정·메모를 목록 맨 아래에 모아 적을 수 있게
        m = tag_re.match(line)
        if not m:
            return [own], line
        t = m.group(1).strip().lower()
        if t in ("공", "공통", "전체", "모두", "all"):
            return allkeys, line[m.end():]
        if t in names:
            return [names[t]], line[m.end():]
        return [own], line  # 모르는 표시는 글자 그대로 둠

    sched = {k: [] for k in allkeys}  # 페이지마다 [날짜 줄 + 그 아래 날짜 없는 줄들] 묶음 목록
    memo = {k: [] for k in allkeys}
    date_re = re.compile(r"^\s*(?:\d{4}\s*[/.년-]\s*)?\d{1,2}\s*[/.월]\s*\d{1,2}")
    year_re = re.compile(r"^\s*(\d{4})\s*년?\s*$")  # '2027년' 줄: 그 아래 날짜는 그 해
    for p in pages:
        cur = None  # 지금 만들고 있는 묶음과 그 묶음이 가는 페이지들
        year = None
        for line in p["extras"]["schedule"]:
            yl = year_re.match(line)
            if yl:
                year, cur = yl.group(1), None
                continue
            m = tag_re.match(line)
            ks, text = targets(line, p["key"])
            if year and date_re.match(text) and not re.match(r"^\s*\d{4}", text):
                text = year + "." + text.lstrip()  # 화면에서 년도를 알 수 있게 (예: 2027.1/1(금) …)
            if date_re.match(text) or cur is None:
                cur = (ks, [text])
                for k in ks:
                    sched[k].append(cur[1])
            elif m:  # 날짜 없이 표시만 붙은 줄: 그 페이지들로 따로 (위 일정 바로 뒤에 붙여요)
                for k in ks:
                    if cur[1] in sched[k]:
                        sched[k][sched[k].index(cur[1])] = cur[1] + [text]
                    else:
                        sched[k].append([text])
            else:  # 날짜 없는 줄은 바로 위 날짜 일정에 붙어 다님 (정렬해도 같이, 표시도 위 줄을 따라감)
                cur[1].append(text)
        topic = []
        for line in p["extras"]["memo"] + [""]:
            if line.strip():
                topic.append(line)
                continue
            if topic:
                ks, first = targets(topic[0], p["key"])
                body = [first] + topic[1:]
                for k in ks:
                    if body not in memo[k]:
                        memo[k].append(body)
                topic = []
    now = datetime.now(KST)

    def date_key(line):
        m = re.match(r"^\s*(?:(\d{4})\s*[/.년-]\s*)?(\d{1,2})\s*[/.월]\s*(\d{1,2})", line)
        if not m:
            return (1, 0, 0)
        mon, day = int(m.group(2)), int(m.group(3))
        y = now.year - 1 if mon - now.month > 6 else (now.year + 1 if now.month - mon > 6 else now.year)
        if m.group(1):
            y = int(m.group(1))
        return (0, y * 10000 + mon * 100 + day, 0)
    for p in pages:
        groups, seen = [], set()
        for g in sched[p["key"]]:
            if tuple(g) not in seen:  # 같은 일정을 두 번 적어도 한 번만
                seen.add(tuple(g))
                groups.append(g)
        groups.sort(key=lambda g: date_key(g[0]))
        p["extras"]["schedule"] = [l for g in groups for l in g]
        out = []
        for i, body in enumerate(memo[p["key"]]):
            out += ([""] if i else []) + body
        p["extras"]["memo"] = out
    return pages


def channel_info(path):
    STATS["info_all"] += 1
    page = get(yt_url(path))
    if not page:
        return {}
    STATS["info_ok"] += 1
    return {"autoName": meta(page, "og:title"),
            "avatar": re.sub(r"=s\d+[^/]*$", "=s240-c-k-c0x00ffffff-no-rj", meta(page, "og:image"))}


DIAG = []
STATS = {"info_ok": 0, "info_all": 0, "live_ok": 0, "live_all": 0, "on": 0, "bot": 0, "other": 0, "notab": 0}
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


def selected_tab_url(page):
    """지금 화면에 선택된 탭의 주소 (예: /@이름/streams). 라이브 탭이 없으면 홈 탭이 선택돼 있어요."""
    for mk in re.finditer(r'"tabRenderer":\{', page):
        seg = page[mk.end():mk.end() + 2500]
        nxt = seg.find('"tabRenderer"')
        if nxt != -1:
            seg = seg[:nxt]
        if '"selected":true' in seg:
            m = re.search(r'"url":"([^"]+)"', seg)
            return m.group(1) if m else ""
    return ""


def in_post(page, vid):
    """영상이 게시글(커뮤니티 글) 안에 붙어 있는 것인지"""
    for m in re.finditer(re.escape('"' + vid + '"'), page):
        before = page[max(0, m.start() - 6000):m.start()]
        if re.search(r'backstagePost|"postRenderer"|sharedPostRenderer|"backstageAttachment"', before):
            return True
    return False


def own_channel_id(page):
    m = (re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})', page)
         or re.search(r'"externalId":"(UC[\w-]{22})"', page)
         or re.search(r'<meta itemprop="identifier" content="(UC[\w-]{22})"', page))
    return m.group(1) if m else ""


def video_author(vid):
    """oEmbed로 영상 올린 채널 이름 (실패하면 "")"""
    page = get("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote("https://www.youtube.com/watch?v=" + vid, safe=""))
    try:
        return json.loads(page).get("author_name", "") if page else ""
    except Exception:
        return ""


WATCH_TITLES = {}  # 라이브 확인 때 읽은 영상 페이지의 제목 (방제)


def watch_title(page):
    t = _watch_title(page).strip()
    # 동의·로봇 확인 화면 같은 엉뚱한 제목은 버림
    if not t or t.lower() in ("youtube", "-") or re.match(r"(?i)before you continue|계속하기 전에", t):
        return ""
    return t


def _watch_title(page):
    m = (re.search(r'<meta name="title" content="([^"]*)"', page)
         or re.search(r'<meta property="og:title" content="([^"]*)"', page))
    if m:
        return html.unescape(m.group(1))
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    return html.unescape(re.sub(r"\s*-\s*YouTube\s*$", "", m.group(1))).strip() if m else ""


def video_title(vid):
    """방송 제목. 라이브 확인 때 읽은 영상 페이지 → oEmbed → 영상 페이지 다시 읽기 순 (실패하면 "")
    (방송에서 '외부 사이트 퍼가기'를 막아 두면 oEmbed가 401로 거절해서, 영상 페이지 제목을 먼저 써요)"""
    if WATCH_TITLES.get(vid):
        return WATCH_TITLES[vid]
    page = get("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote("https://www.youtube.com/watch?v=" + vid, safe=""), timeout=10)
    try:
        t = (json.loads(page).get("title", "") if page else "") or ""
    except Exception:
        t = ""
    if not t:
        page = get("https://www.youtube.com/watch?v=" + vid, timeout=10)
        t = watch_title(page) if page else ""
    return t


def video_owner(vid):
    """영상을 올린(방송하는) 채널 ID. 실패하면 None"""
    page = get("https://www.youtube.com/watch?v=" + vid)
    if not page:
        return None
    try:
        WATCH_TITLES[vid] = watch_title(page)  # 방제도 같이 기억 (따로 요청하지 않게)
    except Exception:
        pass
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
    # 라이브 탭이 없는 채널은 이 주소에서 홈 화면을 보여 줘요. 홈의 게시글에 붙은 남의 방송을 잡지 않도록 확인.
    tab = selected_tab_url(page)
    if tab:
        has_tab = tab.endswith("/streams")
    else:  # 선택된 탭을 못 읽으면 예전 방식으로 (게시글 속 영상은 아래에서 따로 걸러요)
        has_tab = bool(re.search(r'"url":"/[^"]*/streams"', page))
    cands = [c for c in live_candidates(page) if not in_post(page, c)] if has_tab else []
    if not has_tab:
        STATS["notab"] += 1
    how = "라이브 탭"
    if not cands:  # 보조: /live 주소가 '지금 방송 중'인 화면으로 바로 연결되는지
        lp = get(base + "/live") or ""
        m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', lp)
        if m and '"isLiveNow":true' in lp and '"isUpcoming":true' not in lp:
            cands, how = [m.group(1)], "/live"
    vid, other, notes = "", 0, []
    own_name = meta(page, "og:title")
    for c in cands:
        same = None
        owner = video_owner(c)
        if owner and own:  # 1순위: 채널 ID로 비교
            same, by = owner == own, "ID"
        else:              # 2순위: 채널 이름으로 비교 (유튜브 공식 oEmbed)
            author = video_author(c)
            if author and own_name:
                same, by = author == own_name, "이름"
            else:
                by = "-"
        notes.append("소유확인(%s):%s" % (by, {True: "같음", False: "다름", None: "모름"}[same]))
        if same is False:
            other += 1
            STATS["other"] += 1
            continue
        if same is None and not (has_tab and how == "라이브 탭"):
            continue  # 끝내 모르면 본인 라이브 탭에서 찾은 경우만 인정
        vid = c
        break
    notes.insert(0, "선택된탭:" + (tab.rsplit("/", 1)[-1] or "모름") + " 라이브탭:" + ("있음" if has_tab else "없음") + " 찾은경로:" + (how if cands else "-") + " 채널ID:" + ("있음" if own else "없음"))
    DIAG.append((path, how if vid else "", notes if cands else [], other))
    if vid:
        STATS["on"] += 1
        return vid
    return ""


SOOP_RE = re.compile(r"(?:sooplive\.(?:co\.kr|com)/station/|ch\.sooplive\.(?:co\.kr|com)/|play\.sooplive\.(?:co\.kr|com)/"
                     r"|bj\.afreecatv\.com/|play\.afreecatv\.com/|afreecatv\.com/station/)([A-Za-z0-9_]+)", re.I)
SOOP_STATS = {"all": 0, "ok": 0, "on": 0}
SOOP_TITLES = {}  # 숲 방제 (방송 중일 때)


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
            res = str(ch.get("RESULT"))
            if res == "1":
                SOOP_STATS["ok"] += 1
                SOOP_STATS["on"] += 1
                SOOP_TITLES[bid] = str(ch.get("TITLE") or "")
                return "https://play.sooplive.co.kr/%s/%s" % (bid, ch.get("BNO", "")) if ch.get("BNO") else "https://play.sooplive.co.kr/" + bid
            if res == "0":
                SOOP_STATS["ok"] += 1
                return ""
            adult = (res == "-6")  # 19금 방송: 로그인해야 볼 수 있다는 뜻 = 방송 중
            break  # 그 밖의 값(19금·비번방 등)은 아래 방송국 정보로 다시 확인
    else:
        adult = False
    page = get("https://chapi.sooplive.co.kr/api/%s/station" % bid)  # 보조 확인
    if page:
        try:
            j = json.loads(page)
            SOOP_STATS["ok"] += 1
            if j.get("broad"):
                SOOP_STATS["on"] += 1
                SOOP_TITLES[bid] = str((j["broad"] or {}).get("broad_title") or "")
                no = j["broad"].get("broad_no", "")
                return "https://play.sooplive.co.kr/%s/%s" % (bid, no) if no else "https://play.sooplive.co.kr/" + bid
            if not adult:
                return ""
        except Exception:
            pass
    if adult:  # 방송국 정보에 방송이 안 보여도 19금 결과면 방송 중으로
        if not page:
            SOOP_STATS["ok"] += 1
        SOOP_STATS["on"] += 1
        SOOP_TITLES.setdefault(bid, "")
        return "https://play.sooplive.co.kr/" + bid
    return None


# ───────────── 소식(유튜브 커뮤니티 글 · 숲 방송국 글) ─────────────
POST_DAYS = 3                 # 최근 며칠 글까지 보여줄지
POST_EVERY = 9 * 60           # 소식은 약 10분마다 (실행 간격이 4~5분이라 9분이 지났으면 새로 가져옴)
POST_STATS = {"yt_all": 0, "yt_ok": 0, "yt_posts": 0, "soop_all": 0, "soop_ok": 0, "soop_posts": 0}
POST_DIAG = []                # 숲 응답 모양 진단용 (항목 이름만, 내용은 출력하지 않음)
KST = timezone(timedelta(hours=9))


def runs_text(d):
    if not isinstance(d, dict):
        return ""
    if "simpleText" in d:
        return d["simpleText"]
    return "".join(r.get("text", "") for r in d.get("runs", []) if isinstance(r, dict))


def find_all(o, key, skip=()):
    """o 안에서 key 항목을 문서 순서대로 모두 찾기 (skip 안쪽은 들어가지 않음)"""
    if isinstance(o, dict):
        for k, v in o.items():
            if k in skip:
                continue
            if k == key:
                yield v
            else:
                yield from find_all(v, key, skip)
    elif isinstance(o, list):
        for v in o:
            yield from find_all(v, key, skip)


def first(o, key):
    return next(find_all(o, key), None)


def initial_data(page):
    m = re.search(r'(?:var ytInitialData|window\["ytInitialData"\])\s*=\s*', page)
    if not m:
        return None
    try:
        return json.JSONDecoder().raw_decode(page, m.end())[0]
    except Exception:
        return None


AGE_UNITS = {"초": 1, "분": 60, "시간": 3600, "일": 86400, "주": 604800, "개월": 2592000, "년": 31536000,
             "second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000, "year": 31536000}


def age_seconds(text):
    """'3일 전', '1시간 전(수정됨)', '2 days ago' → 초. 못 읽으면 None"""
    m = re.search(r"(\d+)\s*(초|분|시간|일|주|개월|년|second|minute|hour|day|week|month|year)", text or "")
    return int(m.group(1)) * AGE_UNITS[m.group(2)] if m else None


def img_url(thumbs):
    if not thumbs:
        return ""
    u = thumbs[-1].get("url", "")
    return "https:" + u if u.startswith("//") else u


def yt_posts(path):
    """채널 '게시물' 탭의 최근 글. 확인 실패면 None"""
    POST_STATS["yt_all"] += 1
    page = get(yt_url(path) + "/posts", timeout=10)
    if not page or ("confirm you" in page and "not a bot" in page):
        return None
    data = initial_data(page)
    if data is None:
        return None
    POST_STATS["yt_ok"] += 1
    now = datetime.now(timezone.utc)
    out = []
    # 다른 채널 글을 공유한 것(sharedPostRenderer)은 원래 글이 남의 것이라 건너뜀
    for p in find_all(data, "backstagePostRenderer", skip=("sharedPostRenderer",)):
        pid = p.get("postId")
        age = age_seconds(runs_text(p.get("publishedTimeText")))
        if not pid or age is None or age > POST_DAYS * 86400:
            continue
        post = {"id": "yt:" + pid, "src": "yt", "url": "https://www.youtube.com/post/" + pid,
                "ts": (now - timedelta(seconds=age)).isoformat(timespec="seconds"), "est": age >= 3600,
                "title": "", "text": runs_text(p.get("contentText")), "imgs": [], "poll": [], "video": None,
                "like": runs_text(p.get("voteCount")), "cmt": ""}
        reply = first(p.get("actionButtons", {}), "replyButton")
        if reply:
            post["cmt"] = runs_text(first(reply, "text") or {})
        att = p.get("backstageAttachment") or {}
        for im in find_all(att, "backstageImageRenderer"):
            u = img_url((im.get("image") or {}).get("thumbnails"))
            if u:
                post["imgs"].append(u)
        poll = att.get("pollRenderer")
        if poll:
            for c in poll.get("choices", []):
                post["poll"].append([runs_text(c.get("text")), runs_text(c.get("votePercentage"))])
        vr = att.get("videoRenderer")
        if vr and vr.get("videoId"):
            post["video"] = {"id": vr["videoId"], "title": runs_text(vr.get("title")),
                             "thumb": img_url((vr.get("thumbnail") or {}).get("thumbnails"))}
        out.append(post)
    POST_STATS["yt_posts"] += len(out)
    return out


def strip_html(s):
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", s or "")
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def pick(d, *keys):
    """d에서 여러 후보 이름 중 처음 있는 값 (숲 응답 모양이 바뀌어도 버티도록)"""
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if v not in (None, "", []):
            return v
    return None


SOOP_API = "https://chapi.sooplive.co.kr/api/"
SOOP_HEADERS = {"User-Agent": HEADERS["User-Agent"], "Accept": "application/json, text/plain, */*",
                "Accept-Language": "ko-KR,ko;q=0.9", "Referer": "https://www.sooplive.com/", "Origin": "https://www.sooplive.com"}


def soop_json(url):
    try:
        req = urllib.request.Request(url, headers=SOOP_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def abs_url(u):
    if not isinstance(u, str) or not u:
        return ""
    return "https:" + u if u.startswith("//") else (u if u.startswith("http") else "")


def soop_imgs(item):
    out = []
    for ph in item.get("photos") or []:
        u = abs_url(pick(ph, "url", "image", "thumbnail", "file_url")) if isinstance(ph, dict) else abs_url(ph)
        if u:
            out.append(u)
    if not out:  # 사진 칸이 비어 있으면 본문 속 그림에서 (이모티콘은 제외)
        body = (item.get("content") or {}).get("content", "") if isinstance(item.get("content"), dict) else ""
        for tag in re.findall(r"<img\b[^>]*>", body or "", re.I):
            if re.search(r"emtn|ogq_item|data-emoji", tag):
                continue
            m = re.search(r"""\bsrc\s*=\s*["']([^"']+)["']""", tag)
            if m and abs_url(m.group(1)):
                out.append(abs_url(m.group(1)))
    return out


def soop_posts(bid):
    """숲 방송국 글. 방송국 정보에서 게시판 목록을 받고, 공개 게시판마다 최근 글을 가져옴. 확인 실패면 None"""
    POST_STATS["soop_all"] += 1
    st = soop_json(SOOP_API + bid + "/station")
    if not isinstance(st, dict) or not isinstance(st.get("station"), dict):
        return None
    # display_type 104 = 일반 게시판, auth_no 101 = 누구나 읽기 가능 (애청자 전용 게시판은 로그인 없이 못 읽음)
    boards = [m.get("bbs_no") for m in st["station"].get("menus") or []
              if isinstance(m, dict) and m.get("display_type") == 104 and m.get("auth_no") == 101 and m.get("bbs_no")][:10]
    if not boards and len(POST_DIAG) < 1:
        POST_DIAG.append(["게시판 목록 없음"] + sorted(st["station"].keys()))
    cut = datetime.now(timezone.utc) - timedelta(days=POST_DAYS)
    out, seen, read_any = [], set(), False
    for bbs in boards:
        if time.time() >= POST_DEADLINE[0]:  # 시간 다 쓰면 남은 게시판은 건너뜀
            break
        j = soop_json(SOOP_API + "%s/board/%s?page=1&per_page=10&field=title&orderby=reg_date" % (bid, bbs))
        if not isinstance(j, dict) or not isinstance(j.get("data"), list):
            continue
        read_any = True
        for it in j["data"]:
            if not isinstance(it, dict):
                continue
            no, reg = it.get("title_no"), it.get("reg_date")
            if not no or no in seen or not isinstance(reg, str):
                continue
            if str(it.get("user_id", bid)).lower() != bid.lower():  # 시청자가 쓴 글은 제외
                continue
            try:
                ts = datetime.strptime(reg[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
            except ValueError:
                continue
            if ts < cut:
                continue
            seen.add(no)
            cnt = it.get("count") if isinstance(it.get("count"), dict) else {}
            c = it.get("content") if isinstance(it.get("content"), dict) else {}
            text = strip_html(c.get("content") or "") or strip_html(c.get("summary") or c.get("text_content") or "")
            out.append({"id": "soop:%s" % no, "src": "soop", "url": "https://www.sooplive.com/station/%s/post/%s" % (bid, no),
                        "ts": ts.astimezone(timezone.utc).isoformat(timespec="seconds"), "est": False,
                        "title": strip_html(str(it.get("title_name") or "")), "text": text,
                        "imgs": soop_imgs(it), "poll": [], "video": None,
                        "like": str(cnt.get("like_cnt") or ""), "cmt": str(cnt.get("comment_cnt") or "")})
    if boards and not read_any:
        return None
    POST_STATS["soop_ok"] += 1
    POST_STATS["soop_posts"] += len(out)
    return out


def member_posts(member, name, prev_posts, pg=""):
    """한 멤버의 모든 채널(유튜브 대표·서브, 숲) 글. 확인 실패한 채널은 이전 글을 그대로 씀"""
    sources = [("yt:" + p, lambda p=p: yt_posts(p)) for p in ([member["path"]] if member["path"] else []) + member["subs"]]
    for l in member["links"]:
        m = SOOP_RE.search(l["value"])
        if m:
            sources.append(("soop:" + m.group(1).lower(), lambda b=m.group(1): soop_posts(b)))
            break
    out = []
    for key, fetch in sources:
        got = None
        if time.time() < POST_DEADLINE[0]:  # 시간을 너무 오래 쓰면 남은 채널은 이전 글로
            try:
                got = fetch()
            except Exception:
                got = None
        if got is None:
            got = [p for p in prev_posts if p.get("k") == key]
        for p in got:
            p["k"], p["member"], p["pg"] = key, name, pg
            old = PREV_POST_TS.get(p["id"])
            if old and old[0] and p.get("est"):
                p["ts"], p["est"] = old  # 처음 봤을 때 계산한 시각을 그대로 유지 (매번 바뀌지 않게)
            out.append(p)
    return out


PREV_POST_TS = {}
POST_DEADLINE = [0]


def fresh_posts(posts):
    cut = datetime.now(timezone.utc) - timedelta(days=POST_DAYS, hours=12)
    keep = []
    for p in posts:
        try:
            if datetime.fromisoformat(p["ts"]) >= cut:
                keep.append(p)
        except Exception:
            pass
    return keep


# 펀네이션 주소: funnation.co.kr/store/아이디, funn.at/아이디, 아이디.funn.at 모두 인식
FUN_RE = re.compile(r"(?:funnation\.co\.kr|funn\.at)/store/([A-Za-z0-9_-]+)"
                    r"|(?<![\w.])funn\.at/(?!store/|api/|donate/|login|signup|_next)([A-Za-z0-9_-]+)"
                    r"|(?<![\w.-])(?!www\.)([A-Za-z0-9_-]+)\.funn\.at", re.I)
FUN_STATS = {"all": 0, "ok": 0, "on": 0}
FUN_TITLES = {}


def fun_live(slug):
    """펀네이션 방송 중이면 (방송 주소, 대표 사진), 아니면 ("", 사진), 확인 실패면 None"""
    FUN_STATS["all"] += 1
    page = get("https://www.funnation.co.kr/api/stores/%s/live/status" % slug, timeout=10)
    try:
        j = json.loads(page) if page else None
    except Exception:
        j = None
    if not isinstance(j, dict) or "isLive" not in j:
        return None
    FUN_STATS["ok"] += 1
    pic = j.get("storeLogoUrl") or ((j.get("channel") or {}).get("thumbnailUrl") if isinstance(j.get("channel"), dict) else "") or ""
    if j.get("isLive") is True:
        FUN_STATS["on"] += 1
        FUN_TITLES[slug] = str(j.get("broadcastTitle") or j.get("title") or j.get("liveTitle") or "")
        # 유튜브 방송을 펀네이션에서 같이 보여 주는 경우 주소가 /live/영상ID (방송마다 바뀜), 펀네이션 자체 방송은 /live/broadcast
        vid = j.get("videoId") or j.get("liveVideoId")
        if isinstance(vid, str) and re.fullmatch(r"[\w-]{6,20}", vid):
            return "https://www.funnation.co.kr/store/%s/live/%s" % (slug, vid), pic
        return "https://www.funnation.co.kr/store/%s/live/broadcast" % slug, pic
    return "", pic


def check(member, prev):
    old = prev.get(member["name"] or member["path"] or "", {})
    out = {"name": member["name"], "channel": "", "avatar": "", "on": False, "video": "",
           "soop": "", "fun": "", "links": member["links"], "autoName": ""}
    # 숲 링크가 있으면 숲 방송도 확인
    soop_id = fun_id = ""
    for l in member["links"]:
        m = SOOP_RE.search(l["value"])
        if m:
            soop_id = soop_id or m.group(1)
            v = soop_live(m.group(1))
            if v is None and old.get("soop"):
                v = old["soop"]  # 확인 실패 시 이전 상태 유지
            if v:
                out["soop"] = v
                break
    # 펀네이션 링크가 있으면 펀네이션 방송도 확인
    fun_pic = ""
    for l in member["links"]:
        m = FUN_RE.search(l["value"])
        if m:
            fun_id = next(g for g in m.groups() if g)
            r = fun_live(fun_id)
            if r is None:
                if old.get("fun"):
                    out["fun"] = old["fun"]  # 확인 실패 시 이전 상태 유지
            else:
                out["fun"], fun_pic = r
            break
    def titles():
        # 방제: 유튜브 → 숲 → 펀네이션 순으로 방송 중인 첫 번째 것
        if out["on"] and out["video"]:
            return video_title(out["video"]) or old.get("live_title", "")
        if out["soop"]:
            return SOOP_TITLES.get(soop_id, "") or old.get("live_title", "")
        if out["fun"]:
            return FUN_TITLES.get(fun_id, "") or old.get("live_title", "")
        return ""
    if not member["path"]:
        out["name"] = out["name"] or "(이름 없음)"
        if soop_id:  # 유튜브 없이 숲만 있는 멤버: 프사 누르면 숲 방송국, 사진은 숲 프로필
            out["channel"] = "https://www.sooplive.com/station/" + soop_id
            out["avatar"] = "https://stimg.sooplive.co.kr/LOGO/%s/%s/%s.jpg" % (soop_id.lower()[:2], soop_id.lower(), soop_id.lower())
        else:
            out["avatar"] = fun_pic or old.get("avatar", "")  # 유튜브가 없으면 펀네이션 대표 사진
        out["live_title"] = titles()
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
    try:
        out["live_title"] = titles()
    except Exception:
        out["live_title"] = ""
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


PAGES_INFO = {}       # 지금 사이트에 올라가 있는 화면 정보 (못 읽으면 비어 있음)
PAGES_EVERY = 55 * 60  # 화면이 그대로여도 이만큼 지나면 사이트에 다시 올림 (약 1시간에 한 번)


def need_deploy(code):
    """사이트(GitHub Pages)에 새로 올려야 하는지. 라이브 결과는 live-data로 따로 전달돼서 매번 올릴 필요가 없어요."""
    if os.environ.get("FORCE_DEPLOY"):
        return True, "직접 요청"
    if not PAGES_INFO:
        return True, "사이트 화면을 못 읽음"
    if PAGES_INFO.get("code") != code:
        return True, "화면 파일이 바뀜"
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(PAGES_INFO.get("updated", ""))).total_seconds()
    except Exception:
        return True, "사이트 화면 시각을 모름"
    if age >= PAGES_EVERY:
        return True, "마지막으로 올린 지 %d분" % (age // 60)
    return False, "화면 그대로, %d분 전에 올림" % (age // 60)


def load_prev():
    """이전 실행 결과 전체. 올라가 있는 페이지와 저장소 live-data 칸 중 더 최근 것 (실패하면 {})"""
    found = []
    url = os.environ.get("PREV_DATA_URL")
    if url:
        try:
            with urllib.request.urlopen(url + "?t=%d" % datetime.now().timestamp(), timeout=15) as r:
                page = r.read().decode("utf-8")
            m = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S)
            pd = json.loads(m.group(1))
            found.append(pd)
            if isinstance(pd, dict):  # 지금 사이트(GitHub Pages)에 올라가 있는 화면의 버전과 시각
                PAGES_INFO.update({"code": pd.get("code", ""), "updated": pd.get("updated", "")})
        except Exception:
            pass
    prev_file = os.environ.get("PREV_FILE")  # 워크플로가 저장소 live-data에서 바로 받아 둔 이전 결과 (파일 서버 지연 없음)
    if prev_file and os.path.exists(prev_file):
        try:
            with open(prev_file, encoding="utf-8") as f:
                found.append(json.load(f))
        except Exception:
            pass
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo and not (prev_file and os.path.exists(prev_file)):
        try:
            raw = "https://raw.githubusercontent.com/%s/live-data/data.json?t=%d" % (repo, datetime.now().timestamp())
            with urllib.request.urlopen(raw, timeout=15) as r:
                found.append(json.loads(r.read().decode("utf-8")))
        except Exception:
            pass
    found = [d for d in found if isinstance(d, dict)]
    return max(found, key=lambda d: str(d.get("updated", ""))) if found else {}


def main():
    text = os.environ.get("MEMBER_LIST", "").strip()
    if text.startswith("http"):  # 비밀 Gist 주소면 그 안의 목록을 가져옴
        text = read_gist(text)
    if not text.strip() and os.path.exists(os.path.join(ROOT, "목록.txt")):  # 내 컴퓨터에서 시험할 때만
        with open(os.path.join(ROOT, "목록.txt"), encoding="utf-8-sig") as f:
            text = f.read()
    if not text.strip():
        print("MEMBER_LIST 설정이 비어 있어요. Settings → Secrets and variables → Actions → Secrets에 넣어 주세요.")
    pages = split_pages(text)
    title, groups, extras = pages[0]["title"], pages[0]["groups"], pages[0]["extras"]
    prev_data = load_prev()
    prev_by_pg = {"": {m["name"]: m for g in prev_data.get("groups", []) for m in g.get("members", [])}}
    for sp in prev_data.get("sub", []) if isinstance(prev_data.get("sub"), list) else []:
        prev_by_pg[sp.get("key", "")] = {m["name"]: m for g in sp.get("groups", []) for m in g.get("members", [])}
    allm = [(pg["key"], m) for pg in pages for g in pg["groups"] for m in g["members"]]
    members = [m for _, m in allm]
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda pm: check(pm[1], prev_by_pg.get(pm[0], {})), allm))

    # 소식에서 무슨 오류가 나도 라이브 결과는 그대로 페이지에 올라가도록 통째로 감쌈
    try:
        # 소식: 마지막으로 가져온 지 9분이 지났을 때만 새로 가져오고, 아니면 이전 결과를 그대로 씀
        prev_posts = [p for p in prev_data.get("posts", []) if isinstance(p, dict) and p.get("id")]
        PREV_POST_TS.update({p["id"]: (p.get("ts"), p.get("est", False)) for p in prev_posts})
        posts_updated = prev_data.get("postsUpdated", "")
        try:
            due = (datetime.now(timezone.utc) - datetime.fromisoformat(posts_updated)).total_seconds() >= POST_EVERY
        except Exception:
            due = True
        if due or os.environ.get("POSTS_FORCE"):
            POST_DEADLINE[0] = time.time() + 90  # 소식은 최대 약 1분 반까지만 (소식을 가져오는 실행에서만, 그만큼 페이지 반영이 늦어질 수 있어요)
            with ThreadPoolExecutor(6) as ex:  # 라이브 확인(8개)보다 적게 (유튜브 로봇 확인 위험 줄이기)
                lists = list(ex.map(lambda mr: member_posts(mr[0][1], mr[1]["name"], [p for p in prev_posts if p.get("pg", "") == mr[0][0]], mr[0][0]),
                                    zip(allm, results)))
            posts = [p for l in lists for p in l]
            posts_updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        else:
            posts = prev_posts
        seen, uniq = set(), []
        for p in sorted(fresh_posts(posts), key=lambda p: p["ts"], reverse=True):
            if (p.get("pg", ""), p["id"]) not in seen:
                seen.add((p.get("pg", ""), p["id"]))
                uniq.append(p)
        posts = uniq
    except Exception as e:
        print("소식 처리 중 오류(라이브 확인에는 영향 없음):", type(e).__name__)
        posts = [p for p in prev_data.get("posts", []) if isinstance(p, dict)] if isinstance(prev_data.get("posts"), list) else []
        posts_updated = prev_data.get("postsUpdated", "")
    i = 0
    out_pages = []
    for pg in pages:
        gs = []
        for g in pg["groups"]:
            n = len(g["members"])
            gs.append({"title": g["title"], "members": results[i:i + n]})
            i += n
        out_pages.append(gs)
    out_groups = out_pages[0]
    data = {
        "title": title or "라이브 보드",
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "groups": out_groups,
        "schedule": extras["schedule"],
        "memo": extras["memo"],
        "posts": posts,
        "postsUpdated": posts_updated,
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),  # 페이지가 저장소의 최신 결과(live-data)를 찾을 때 씀
        # 계열사 페이지 (Gist 목록의 '페이지 MBU' 아래). 멤버·일정·메모는 페이지마다 따로, 소식은 posts의 pg로 구분
        "sub": [{"key": pg["key"], "label": pg["label"], "title": pg["title"] or pg["label"], "groups": out_pages[k],
                 "schedule": pg["extras"]["schedule"], "memo": pg["extras"]["memo"]} for k, pg in enumerate(pages) if k],
    }
    site = os.path.join(ROOT, "site")
    os.makedirs(site, exist_ok=True)
    with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
        page = f.read()
    import hashlib
    data["code"] = hashlib.md5(page.encode("utf-8")).hexdigest()[:10]  # 화면 파일 버전 (사이트 화면이 옛 버전인지 알아보는 데 씀)
    blob = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")  # 글 내용 속 '<' 때문에 페이지가 깨지지 않도록
    with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as f:
        f.write(page.replace("__DATA__", blob))
    with open(os.path.join(site, "data.json"), "w", encoding="utf-8") as f:  # 저장소 live-data 칸에 따로 저장할 결과
        json.dump(data, f, ensure_ascii=False)
    deploy, why = need_deploy(data["code"])
    print("사이트에 올리기: %s (%s)" % ("함" if deploy else "안 함 — 라이브 결과는 live-data로 전달", why))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write("deploy=%s\n" % ("true" if deploy else "false"))
    print("완료: %d명 중 %d명 라이브 (유튜브 %d, 숲 %d)" % (len(results), sum(bool(r["on"] or r["soop"] or r.get("fun")) for r in results),
                                                     sum(bool(r["on"]) for r in results), sum(bool(r["soop"]) for r in results)))
    print("채널 페이지 읽기 %(info_ok)d/%(info_all)d, 라이브 페이지 읽기 %(live_ok)d/%(live_all)d, "
          "방송 중 %(on)d, 다른 채널 방송 제외 %(other)d, 라이브 탭 없는 채널 %(notab)d, 로봇 확인 화면 %(bot)d" % STATS)
    print("숲 확인 %(ok)d/%(all)d, 숲 방송 중 %(on)d" % SOOP_STATS)
    if FUN_STATS["all"]:
        print("펀네이션 확인 %(ok)d/%(all)d, 펀네이션 방송 중 %(on)d" % FUN_STATS)
    if POST_STATS["yt_all"] or POST_STATS["soop_all"]:
        print("소식 새로 가져옴: 유튜브 읽기 %(yt_ok)d/%(yt_all)d 글 %(yt_posts)d개, "
              "숲 읽기 %(soop_ok)d/%(soop_all)d 글 %(soop_posts)d개" % POST_STATS)
        if POST_DIAG:
            print("  숲 진단:", ", ".join(map(str, POST_DIAG[0])))
    else:
        print("소식은 이전 결과 사용 (10분마다 새로 가져와요)")
    print("소식 %d개 (최근 %d일)" % (len(posts), POST_DAYS))
    # 진단용: 이름 대신 목록 순서 번호만 출력 (로그가 공개라서)
    order, n = {}, 0
    for g in [g for pg in pages for g in pg["groups"]]:
        for m in g["members"]:
            n += 1
            order.setdefault(m["path"], n)
            for sp in m["subs"]:
                order.setdefault(sp, "%d-서브" % n)
    for path, how, notes, other in DIAG:
        if how or notes or other:
            print("  %s번 멤버 → %s / %s" % (order.get(path, "?"), ("방송 중(" + how + ")") if how else "꺼짐", " ".join(notes)))


if __name__ == "__main__":
    main()
