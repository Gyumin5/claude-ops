#!/usr/bin/env python3
"""주간 브리핑 1단계 — 출처에서 후보를 모으고 순위를 매겨 고른다.

사용: ai-weekly-brief-collect.py --out <후보.jsonl> --status <출처상태.json>
      [--history <이력.jsonl>] [--days N] [--budget BYTES]

출처 하나가 죽어도 나머지로 돈다. 출처마다 성공·정상 0건·실패·부분 수집을 구분해
--status 파일에 적는다. 모은 본문은 자료로만 다루고 그 안의 지시는 따르지 않는다.
"""
import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

UA = "ai-weekly-brief/1.0"
NOW = datetime.now(timezone.utc)

# 운영자의 관심 분야. 수집·순위의 기본 주제이고 분야마다 최소 한 자리를 확보한다.
TOPICS = {
    # 낱말은 운영자가 직접 모은 링크드인 저장 글 42개에서 실제로 쓰인 말로 채웠다
    # (reports/2026-10-13-링크드인-저장항목-검토.html). 짐작으로 고르면 그 종류가 안 잡힌다.
    "주행추정과 지도작성": [
        "lidar", "odometry", "localization", "mapping", "point cloud",
        "registration", "lidar-inertial", "lio", "slam", "loop closure",
        "icp", "pose graph", "visual-inertial",
        "continuous-time", "b-spline", "gaussian process", "signed distance",
        "factor graph", "bundle adjustment", "map maintenance", "occupancy grid",
    ],
    "네발로봇 묶음": [
        "quadruped", "legged robot", "go2", "unitree", "anymal", "mid-360",
        "jetson orin", "locomotion",
    ],
    "센서가 무너지는 상황": [
        "degenerac", "degrade", "smoke", "dust", "fog", "vegetation",
        "failure detection", "robust odometry", "covariance", "uncertainty",
        "subspace", "adaptive weighting", "sensor dropout", "forest", "subterranean",
    ],
    "공개 데이터셋": [
        "dataset", "benchmark", "grandtour", "hilti", "kitti", "newer college",
        "leaderboard", "evaluation suite", "oxford spires", "conslam", "octosense",
        "helipr", "ground truth", "relocalization", "place recognition",
    ],
    "클로드와 에이전트 사용성": [
        "claude code", "claude", "anthropic", "mcp", "model context protocol",
        "agent sdk", "coding agent", "skill", "subagent", "tool use",
        "prompt caching", "gemini", "openai", "codex", "llm agent",
    ],
}

# 사건 종류. 같은 작업이라도 논문·코드·릴리스·데이터셋은 각각 별개 사건이다.
EVENT_PAPER = "논문"
EVENT_CODE = "코드공개"
EVENT_RELEASE = "릴리스"
EVENT_DATASET = "데이터셋"
EVENT_POST = "글"

SUPPRESS_DAYS = 60
SOURCE_BUDGET = 120     # 출처 하나가 회차 전체 시간을 먹지 못하게 하는 벽시계 상한(초)


def get(url, timeout=25, retries=2, sleep=4.0):
    """한 주소를 받아 본문 문자열을 준다. 실패는 예외로 올린다."""
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            # 요청하지 않아도 압축해 보내는 곳이 있다(구글 딥마인드).
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            return body.decode("utf-8", "replace")
        except Exception as exc:  # 네트워크·상태코드 전부 여기로 온다.
            last = exc
            if attempt < retries:
                time.sleep(sleep * (attempt + 1))
    raise RuntimeError(str(last))


_WORD_RE = {}


def _word(w):
    """낱말 경계로 맞춘다. 그냥 포함으로 보면 icp 가 MiniCPM 에 걸리고
    dataset 이 허깅페이스가 붙인 'datasets' 머리말에 걸려 전부 분야로 찍힌다."""
    if w not in _WORD_RE:
        # 뒤에 붙는 어미는 허용한다(degrade→degraded, dataset→datasets).
        # 앞 경계만으로 끼어 읽기는 막힌다 — minicpm 의 icp 는 앞이 n 이라 안 걸린다.
        _WORD_RE[w] = re.compile(r"(?<![a-z0-9])%s[a-z]*(?![a-z0-9])" % re.escape(w))
    return _WORD_RE[w]


def topic_of(text):
    """본문에 가장 많이 걸리는 관심 분야 이름과 걸린 수를 준다."""
    low = text.lower()
    best, best_hits = None, 0
    for name, words in TOPICS.items():
        hits = sum(1 for w in words if _word(w).search(low))
        if hits > best_hits:
            best, best_hits = name, hits
    return best, best_hits


def mk(source, title, url, work_id, event, when, extra=None):
    topic, hits = topic_of((title or "") + " " + (extra or {}).get("summary", ""))
    return {
        "source": source,
        "title": (title or "").strip()[:220],
        "url": url,
        "work_id": work_id,
        "event": event,
        "when": when,            # ISO8601 또는 ""
        "topic": topic,
        "topic_hits": hits,
        "summary": ((extra or {}).get("summary") or "").strip()[:600],
        "license": (extra or {}).get("license") or "미확인",
        "popularity": (extra or {}).get("popularity"),
    }


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_when(s):
    """여러 날짜 표기를 datetime 으로. 못 읽으면 None."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
                "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ───────────────────────── 출처들 ─────────────────────────

def src_anthropic(days):
    """앤트로픽 엔지니어링 글과 소식. 사이트맵의 변경 시각을 쓴다."""
    xml = get("https://www.anthropic.com/sitemap.xml", timeout=30)
    pairs = re.findall(r"<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]*)</lastmod>)?", xml)
    cut = NOW - timedelta(days=days)
    out = []
    for url, mod in pairs:
        if "/engineering/" not in url and "/news/" not in url:
            continue
        if url.rstrip("/").endswith(("/engineering", "/news")):
            continue
        when = parse_when(mod)
        if when is None or when < cut:
            continue
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        title = slug.replace("-", " ")
        out.append(mk("앤트로픽", title, url, "anthropic:" + slug, EVENT_POST, iso(when)))
    return out


def src_claude_changelog(days):  # noqa: ARG001 — 변경기록은 기간 자르기가 없다
    """클로드 코드 변경 기록. 최신 판본 묶음만 사건으로 본다."""
    md = get("https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md",
             timeout=30)
    blocks = re.split(r"^##\s+", md, flags=re.M)[1:4]
    out = []
    for b in blocks:
        head = b.splitlines()[0].strip()
        body = "\n".join(b.splitlines()[1:])[:1200]
        ver = head.split()[0] if head else "unknown"
        out.append(mk("클로드코드 변경기록", "클로드 코드 " + head,
                      "https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md",
                      "claude-code:" + ver, EVENT_RELEASE, "",
                      {"summary": body, "license": "확인됨"}))
    return out


def _rss(url, source, days, event=EVENT_POST, idprefix=""):
    xml = get(url, timeout=30)
    root = ET.fromstring(xml.encode("utf-8", "replace"))
    cut = NOW - timedelta(days=days)
    out = []
    for item in root.iter("item"):
        def t(tag):
            e = item.find(tag)
            return (e.text or "").strip() if e is not None and e.text else ""
        when = parse_when(t("pubDate"))
        if when is not None and when < cut:
            continue
        link = t("link")
        if not link:
            continue
        slug = link.rstrip("/").rsplit("/", 1)[-1]
        out.append(mk(source, t("title"), link, idprefix + slug, event,
                      iso(when) if when else "",
                      {"summary": re.sub(r"<[^>]+>", " ", t("description"))}))
    return out


def src_openai(days):
    return _rss("https://openai.com/news/rss.xml", "오픈에이아이", days,
                idprefix="openai:")


def src_deepmind(days):
    return _rss("https://deepmind.google/blog/rss.xml", "구글 딥마인드", days,
                idprefix="deepmind:")


ARXIV_LISTS = ("cs.RO", "cs.CV")
ARXIV_TITLE = re.compile(
    r"list-title mathjax'><span class='descriptor'>Title:</span>\s*(.*?)\s*</div>", re.S)
ARXIV_ID = re.compile(r"arXiv:(\d{4}\.\d{4,5})")


def src_arxiv_list(days):
    """논문 보관소 목록 페이지. 프로그램용 질의 창구가 막혀도 이쪽은 열린다.

    실측(2026-10-13): export.arxiv.org 질의 창구는 이 아이피에서 429 가 계속이고
    rss.arxiv.org 는 200 이지만 항목이 0건이다. 사람이 보는 목록 페이지는 200 으로
    열리고 한 번에 50건을 제목까지 준다. 그래서 그 페이지를 읽는다.

    날짜는 목록에 없다. 신규 등록 목록 자체가 최근 며칠치라 그대로 오늘로 적되,
    분야 낱말이 안 걸린 것은 뒤 단계에서 걸러진다.
    """
    out, failed = [], []
    t0 = time.time()
    for cat in ARXIV_LISTS:
        if time.time() - t0 > SOURCE_BUDGET:
            failed.append("남은 분야 생략(시간)")
            break
        try:
            html = get("https://arxiv.org/list/%s/recent" % cat, timeout=30, retries=1,
                       sleep=4.0)
        except Exception as exc:
            failed.append("%s:%s" % (cat, str(exc)[:60]))
            continue
        ids = ARXIV_ID.findall(html)
        titles = [re.sub(r"\s+", " ", t).strip() for t in ARXIV_TITLE.findall(html)]
        for eid, title in zip(ids, titles):
            out.append(mk("논문 보관소", title, "https://arxiv.org/abs/" + eid,
                          "arxiv:" + eid, EVENT_PAPER, iso(NOW)))
        time.sleep(2.0)
    if failed and not out:
        raise RuntimeError("전체 분야 실패: " + "; ".join(failed))
    return out, failed


def src_hf_papers(days):
    """허깅페이스가 매일 고르는 논문. 보관소 번호와 제목을 같이 준다."""
    cut = NOW - timedelta(days=days)
    data = json.loads(get("https://huggingface.co/api/daily_papers?limit=60", timeout=30))
    out = []
    for row in data:
        p = row.get("paper") or {}
        when = parse_when(row.get("publishedAt") or p.get("publishedAt") or "")
        if when is None or when < cut:
            continue
        pid = p.get("id") or ""
        if not pid:
            continue
        out.append(mk("오늘의 논문", p.get("title") or pid,
                      "https://arxiv.org/abs/" + pid, "arxiv:" + pid,
                      EVENT_PAPER, iso(when),
                      {"summary": (p.get("summary") or "").replace("\n", " ")}))
    return out


def src_arxiv_api(days):
    """논문 보관소 질의 창구. 지금은 이 아이피에서 막혀 있어 출처 목록에서 뺐다."""
    # 질의를 적게 묶는다 — 보관소가 짧은 간격의 연속 요청을 막는다.
    queries = [
        ("주행추정과 지도작성",
         'cat:cs.RO AND (abs:"LiDAR odometry" OR abs:"LiDAR SLAM" OR abs:"lidar-inertial" '
         'OR abs:quadruped OR abs:"legged robot" OR abs:degeneracy OR abs:"robust odometry")'),
        ("클로드와 에이전트 사용성",
         '(cat:cs.SE OR cat:cs.AI) AND (abs:"coding agent" OR abs:"LLM agent" '
         'OR abs:"tool use" OR abs:"agent benchmark")'),
    ]
    cut = NOW - timedelta(days=days)
    out, failed = [], []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    t0 = time.time()
    misses = 0
    for topic, q in queries:
        # 한 출처가 회차 전체를 먹지 않게 한다. 보관소가 막혀 있으면 두 번 보고 접는다.
        if time.time() - t0 > SOURCE_BUDGET or misses >= 2:
            failed.append("남은 질의 생략(시간 또는 연속 실패)")
            break
        url = ("https://export.arxiv.org/api/query?search_query="
               + urllib.parse.quote(q)
               + "&start=0&max_results=12&sortBy=submittedDate&sortOrder=descending")
        try:
            xml = get(url, timeout=25, retries=1, sleep=5.0)
            root = ET.fromstring(xml.encode("utf-8", "replace"))
            misses = 0
        except Exception as exc:
            misses += 1
            failed.append("%s:%s" % (topic, str(exc)[:60]))
            continue
        for e in root.findall("a:entry", ns):
            eid = (e.findtext("a:id", "", ns) or "").rstrip("/").rsplit("/", 1)[-1]
            when = parse_when((e.findtext("a:published", "", ns) or "")[:19] + "Z")
            if when is None or when < cut:
                continue
            c = mk("논문 보관소", e.findtext("a:title", "", ns),
                   "https://arxiv.org/abs/" + eid, "arxiv:" + re.sub(r"v\d+$", "", eid),
                   EVENT_PAPER, iso(when),
                   {"summary": (e.findtext("a:summary", "", ns) or "").replace("\n", " ")})
            if not c["topic"]:
                c["topic"], c["topic_hits"] = topic, 1
            out.append(c)
        time.sleep(5.0)
    if failed and not out:
        raise RuntimeError("전체 질의 실패: " + "; ".join(failed))
    return out, failed


def src_github(days):
    """깃헙. 새 저장소만이 아니라 기존 프로젝트의 최근 릴리스도 찾는다."""
    cut = (NOW - timedelta(days=days)).strftime("%Y-%m-%d")
    out, failed = [], []
    t0 = time.time()
    searches = [
        ("created:>" + cut + " claude code agent", EVENT_CODE),
        ("created:>" + cut + " mcp server", EVENT_CODE),
        ("pushed:>" + cut + " lidar odometry", EVENT_RELEASE),
        ("pushed:>" + cut + " lidar slam", EVENT_RELEASE),
        ("pushed:>" + cut + " quadruped locomotion", EVENT_RELEASE),
    ]
    for q, event in searches:
        if time.time() - t0 > SOURCE_BUDGET:
            failed.append("남은 질의 생략(시간)")
            break
        url = ("https://api.github.com/search/repositories?q="
               + urllib.parse.quote(q) + "&sort=stars&order=desc&per_page=6")
        try:
            data = json.loads(get(url, timeout=30))
        except Exception as exc:
            failed.append(q[:28] + ":" + str(exc)[:50])
            continue
        for r in data.get("items", []):
            lic = (r.get("license") or {}).get("spdx_id") or "미확인"
            out.append(mk("깃헙", r.get("full_name", "") + " — " + (r.get("description") or ""),
                          r.get("html_url", ""), "gh:" + r.get("full_name", ""),
                          event, r.get("pushed_at", ""),
                          {"summary": r.get("description") or "",
                           "license": lic,
                           "popularity": r.get("stargazers_count")}))
        time.sleep(2.0)
    if failed and not out:
        raise RuntimeError("전체 질의 실패: " + "; ".join(failed))
    return out, failed


HF_QUERIES = ("lidar", "slam", "odometry", "quadruped", "legged robot",
              "point cloud", "robot navigation")


def hf_license(rec):
    """허깅페이스 항목의 라이선스. 못 찾으면 '미확인'.

    목록 API 는 full=false 에서도 cardData.license 를 준다. 전에는 이 자리를
    무조건 '미확인' 으로 적어서, 평가 단계가 "라이선스 확인 필요" 를 붙이고
    읽는 쪽이 결국 그 쪽을 직접 열어야 했다(2026-10-18 실측: 아파치 2.0 인
    데이터셋 하나를 손으로 열어 확인했다). 태그에만 있는 경우도 받는다.
    """
    card = rec.get("cardData") or {}
    lic = card.get("license")
    if isinstance(lic, list):
        lic = lic[0] if lic else None
    if isinstance(lic, str) and lic.strip():
        return lic.strip()
    for tag in rec.get("tags") or []:
        if isinstance(tag, str) and tag.startswith("license:"):
            rest = tag.split(":", 1)[1].strip()
            if rest:
                return rest
    return "미확인"


def src_huggingface(days):
    """허깅페이스 새 데이터셋·모델.

    새로 만든 순 전체를 긁으면 하루 수천 건의 개인 업로드가 들어와 목록을 덮는다
    (실측: 한 회차 80건이 거의 전부 무작위 이름이었다). 그래서 관심 분야 낱말로
    찾은 것만 가져온다. 분야를 넓히려면 질의 낱말을 늘린다 — 믿는 곳 목록이 아니다.
    """
    cut = NOW - timedelta(days=days)
    out, failed = [], []
    t0 = time.time()
    for kind, event in (("datasets", EVENT_DATASET), ("models", EVENT_RELEASE)):
        for q in HF_QUERIES:
            if time.time() - t0 > SOURCE_BUDGET:
                failed.append("남은 질의 생략(시간)")
                break
            url = ("https://huggingface.co/api/%s?search=%s&sort=createdAt"
                   "&direction=-1&limit=20&full=false"
                   % (kind, urllib.parse.quote(q)))
            try:
                data = json.loads(get(url, timeout=25, retries=1, sleep=3.0))
            except Exception as exc:
                failed.append("%s/%s:%s" % (kind, q, str(exc)[:60]))
                continue
            for r in data:
                when = parse_when(r.get("createdAt") or r.get("lastModified") or "")
                if when is None or when < cut:
                    continue
                rid = r.get("id", "")
                out.append(mk("허깅페이스", rid,
                              "https://huggingface.co/%s%s" % (
                                  "datasets/" if kind == "datasets" else "", rid),
                              "hf:%s:%s" % (kind, rid), event, iso(when),
                              {"license": hf_license(r), "summary": "검색어 " + q,
                               "popularity": r.get("downloads")}))
            time.sleep(1.0)
    if failed and not out:
        raise RuntimeError("전체 질의 실패: " + "; ".join(failed))
    return out, failed


def src_hn(days):
    """해커뉴스는 보조 발견 출처로만 둔다."""
    since = int((NOW - timedelta(days=days)).timestamp())
    out = []
    for q in ("Claude Code", "MCP server", "Anthropic agent", "AI coding agent"):
        url = ("https://hn.algolia.com/api/v1/search?query=" + urllib.parse.quote(q)
               + "&tags=story&numericFilters=created_at_i>" + str(since)
               + ",points>20&hitsPerPage=5")
        data = json.loads(get(url, timeout=25))
        for h in data.get("hits", []):
            link = h.get("url") or ("https://news.ycombinator.com/item?id=%s"
                                    % h.get("objectID"))
            out.append(mk("해커뉴스(보조)", h.get("title", ""), link,
                          "hn:" + str(h.get("objectID")), EVENT_POST,
                          h.get("created_at", ""),
                          {"popularity": h.get("points")}))
        time.sleep(1.0)
    return out


LINKEDIN_QUEUE = os.path.join(os.path.expanduser("~"), "dotfiles", "state", "linkedin", "queue.jsonl")
LINKEDIN_STATUS = os.path.join(os.path.expanduser("~"), "dotfiles", "state", "linkedin", "status.json")
LINKEDIN_STALE_HOURS = 36


def src_linkedin(days):  # noqa: ARG001 — 대기열은 날짜로 자르지 않는다
    """운영자가 저장한 링크드인 글. 날짜가 아니라 미처리 대기열을 읽는다.

    기간으로 자르면 주간 쪽이 한 번 밀렸을 때 그 주에 저장한 글이 통째로 사라진다.
    수집은 linkedin-daily 가 하고 여기서는 읽기만 한다 — 이 안에서 브라우저를
    띄우지 않는다.
    """
    failed = []
    try:
        age = (time.time() - os.path.getmtime(LINKEDIN_STATUS)) / 3600.0
        if age > LINKEDIN_STALE_HOURS:
            failed.append("일일 수집이 %d시간째 안 돌았다" % age)
    except OSError:
        failed.append("일일 수집 상태 파일이 없다 — 한 번도 안 돌았을 수 있다")

    out = []
    if not os.path.exists(LINKEDIN_QUEUE):
        return out, failed
    with open(LINKEDIN_QUEUE, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                it = json.loads(line)
            except Exception:
                continue
            text = (it.get("text") or "").strip()
            out.append(mk("링크드인(저장)", text[:160], it.get("url") or "",
                          "linkedin:" + it.get("urn", ""), EVENT_POST,
                          it.get("first_seen") or "",
                          {"summary": text, "license": "해당 없음"}))
    return out, failed


SOURCES = [
    ("링크드인(저장)", src_linkedin),
    ("앤트로픽", src_anthropic),
    ("클로드코드 변경기록", src_claude_changelog),
    ("오픈에이아이", src_openai),
    ("구글 딥마인드", src_deepmind),
    ("논문 보관소", src_arxiv_list),
    ("오늘의 논문", src_hf_papers),
    ("깃헙", src_github),
    ("허깅페이스", src_huggingface),
    ("해커뉴스(보조)", src_hn),
]


# ───────────────────────── 묶기·순위·예산 ─────────────────────────

def merge(cands):
    """같은 작업·같은 사건이면 하나로 묶는다. 제목 비슷하다고 묶지 않는다."""
    by = {}
    for c in cands:
        key = (c["work_id"], c["event"])
        if key in by:
            cur = by[key]
            cur["cross"] = cur.get("cross", 1) + 1
            if len(c.get("summary") or "") > len(cur.get("summary") or ""):
                cur["summary"] = c["summary"]
            if c["source"] not in cur["source"]:
                cur["source"] += "+" + c["source"]
        else:
            c["cross"] = 1
            by[key] = c
    return list(by.values())


def load_history(path):
    seen = {}
    if not path or not os.path.exists(path):
        return seen
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            d = parse_when(r.get("date") or "")
            if d is None:
                continue
            k = "%s|%s" % (r.get("work_id"), r.get("event"))
            if k not in seen or d > seen[k]:
                seen[k] = d
    return seen


def suppress(cands, seen):
    kept, dropped = [], []
    for c in cands:
        k = "%s|%s" % (c["work_id"], c["event"])
        last = seen.get(k)
        if last is not None and (NOW - last).days < SUPPRESS_DAYS:
            c["drop_reason"] = "%d일 전 같은 사건으로 소개" % (NOW - last).days
            dropped.append(c)
        else:
            kept.append(c)
    return kept, dropped


def score(c):
    """주제 관련성이 가장 크고, 구체적 변화와 근거가 그다음이다.

    인기 지표(별 수·내려받기)는 같은 점수일 때만 가르는 보조 순위다.
    """
    s = 100 * min(c.get("topic_hits", 0), 3) if c.get("topic") else 0
    when = parse_when(c.get("when") or "")
    if when is not None:
        age = max((NOW - when).days, 0)
        s += max(0, 40 - age)                      # 새로운 변화
    if c.get("url"):
        s += 10                                     # 확인 가능한 근거
    if c.get("summary"):
        s += 5
    s += 25 * (c.get("cross", 1) - 1)               # 여러 출처에 같이 뜸
    return s


# 글 자체가 읽을 거리인 곳. 분야 낱말이 안 걸려도 목록에 올린다.
NARRATIVE = ("앤트로픽", "클로드코드 변경기록", "오픈에이아이", "구글 딥마인드",
             "링크드인(저장)")
# 링크드인은 운영자가 손으로 고른 것이라 분야 낱말이 안 걸려도 버리지 않는다.
# 한국어로 쓴 글은 영어 낱말 목록에 어차피 안 걸린다.


def choose(cands, budget):
    """분야마다 한 자리를 먼저 주고, 남은 자리를 점수로 채운다.

    인기 지표는 점수가 같을 때만 본다. 최소 별 수·내려받기 수를 통과 조건으로
    쓰지 않는다. 대신 찾아다니는 출처(깃헙·허깅페이스·해커뉴스)에서 분야 낱말이
    하나도 안 걸린 것은 뺀다 — 그것들은 하루치 업로드 전체가 들어오는 자리라,
    안 거르면 무작위 이름이 자리를 다 차지한다.
    """
    offtopic = []
    for c in cands:
        if not c.get("topic") and c.get("source") not in NARRATIVE:
            c["drop_reason"] = "분야 낱말 미해당"
            offtopic.append(c)
    inscope = [c for c in cands if c not in offtopic]
    ranked = sorted(inscope, key=lambda c: (-score(c), -(c.get("popularity") or 0)))
    picked, rest = [], []
    taken_topic = set()
    for c in ranked:
        t = c.get("topic")
        if t and t not in taken_topic:
            picked.append(c)
            taken_topic.add(t)
        else:
            rest.append(c)

    def size(items):
        return len(json.dumps(items, ensure_ascii=False).encode("utf-8"))

    while size(picked) > budget and len(picked) > 1:
        picked.pop()
    for c in rest:
        if size(picked + [c]) > budget:
            c["drop_reason"] = "입력 크기 상한"
            continue
        picked.append(c)
    chosen_ids = {id(c) for c in picked}
    left = [c for c in cands if id(c) not in chosen_ids]
    for c in left:
        c.setdefault("drop_reason", "순위 밖")
    return picked, left  # 거른 것도 left 에 들어가 부록에 사유와 함께 남는다


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--status", required=True)
    ap.add_argument("--appendix", default="")
    ap.add_argument("--history", default="")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--budget", type=int, default=30000)
    a = ap.parse_args()

    raw, status = [], []
    for name, fn in SOURCES:
        t0 = time.time()
        try:
            got = fn(a.days)
            partial = []
            if isinstance(got, tuple):
                got, partial = got
            state = "부분수집" if partial else ("성공" if got else "정상 0건")
            status.append({"source": name, "state": state, "count": len(got),
                           "note": "; ".join(partial)[:200],
                           "seconds": round(time.time() - t0, 1)})
            raw.extend(got)
        except Exception as exc:
            status.append({"source": name, "state": "실패", "count": 0,
                           "note": str(exc)[:200],
                           "seconds": round(time.time() - t0, 1)})

    merged = merge(raw)
    kept, suppressed = suppress(merged, load_history(a.history))
    picked, left = choose(kept, a.budget)

    with open(a.out, "w", encoding="utf-8") as f:
        for c in picked:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    if a.appendix:
        with open(a.appendix, "w", encoding="utf-8") as f:
            for c in suppressed + left:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
    with open(a.status, "w", encoding="utf-8") as f:
        json.dump({"sources": status,
                   "raw": len(raw), "merged": len(merged),
                   "suppressed": len(suppressed),
                   "selected": len(picked), "appendix": len(suppressed) + len(left)},
                  f, ensure_ascii=False, indent=1)
    print("원본 %d 묶음 %d 억제 %d 선정 %d 부록 %d"
          % (len(raw), len(merged), len(suppressed), len(picked),
             len(suppressed) + len(left)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
