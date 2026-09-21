#!/usr/bin/env python3
"""daily-research dedup/history — 검색 후보를 최근 노출 이력으로 결정론적 필터링하고,
실제 노출된 후보를 이력에 기록한다.

배경(2026-06-22): claude-daily-research 가 매 런마다 같은 고정키워드 검색을 돌리고
이전 노출 URL 을 기억·제외하지 않아, 같은 evergreen 글이 매일 반복 선정되던 문제.
검색과 AI선택 사이에 결정론적(코드) dedup 을 넣어 해결. (AI 프롬프트 패널티·영구제외는
기각 — 비결정성/업데이트 누락. ai-debate run-20260621T220407Z 합의.)

history jsonl 레코드:
  {canonical_url, raw_url, title, source, first_seen, last_seen, surfaced_count,
   decision, decision_reason}
정책: canonical_url 기준 롤링 제외 — decision=review_only 는 90일, 그 외 60일.
  (검색 윈도가 30일이라 30일 제외로는 부족 → 60일. '검토만' 항목은 90일.)
  hash/updated_at 기반 조기 재등장 허용은 후속 — 현재는 윈도 만료로 자연 재등장.

usage:
  daily-research-dedup.py filter <candidates.jsonl> <history.jsonl> <out.jsonl> [--log <f>]
      → stdout: "<kept> <excluded>"
  daily-research-dedup.py record <briefing_output.txt> <history.jsonl> [--date YYYY-MM-DD]
      → stdout: "<recorded_count>"
"""
import sys
import os
import json
import re
import datetime
from urllib.parse import urlsplit, urlunsplit

GENERAL_DAYS = 60
REVIEW_ONLY_DAYS = 90


def canonical(url):
    """추적 파라미터·fragment·trailing slash·www·scheme 차이를 제거한 정규 URL."""
    try:
        s = urlsplit(url.strip())
        host = (s.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = (s.path or "").rstrip("/")
        out = urlunsplit(("https", host, path, "", ""))
        return out or url.strip()
    except Exception:
        return url.strip()


def load_history(path):
    hist = {}
    if not os.path.exists(path):
        return hist
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                cu = d.get("canonical_url")
                if cu:
                    hist[cu] = d
    except Exception:
        pass
    return hist


def _age_days(last_seen_iso, today):
    try:
        d = datetime.date.fromisoformat(str(last_seen_iso)[:10])
        return (today - d).days
    except Exception:
        return 10 ** 6  # 파싱 불가 = 아주 오래된 것으로 취급(제외 안 함)


def cmd_filter(cand_path, hist_path, out_path, log_path=None):
    today = datetime.date.today()
    hist = load_history(hist_path)
    kept, excluded = [], []
    with open(cand_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except Exception:
                continue
            cu = canonical(c.get("url", ""))
            h = hist.get(cu)
            if h:
                window = REVIEW_ONLY_DAYS if h.get("decision") == "review_only" else GENERAL_DAYS
                age = _age_days(h.get("last_seen", ""), today)
                if age < window:
                    excluded.append((cu, h.get("decision", "seen"), age, window))
                    continue
            kept.append(line)
    with open(out_path, "w") as f:
        for l in kept:
            f.write(l + "\n")
    if log_path:
        try:
            with open(log_path, "a") as f:
                f.write("# %s filter: kept=%d excluded=%d\n" % (today, len(kept), len(excluded)))
                for cu, dec, age, win in excluded:
                    f.write("  EXCLUDE %s decision=%s age=%dd<%dd\n" % (cu, dec, age, win))
        except Exception:
            pass
    print("%d %d" % (len(kept), len(excluded)))


def parse_items(output):
    """브리핑 출력의 추천 블록 파싱. 각 항목: '#N 제목' + '왜:' + '실행:' + '출처:'."""
    items, cur = [], {}
    for line in output.splitlines():
        ls = line.strip()
        if ls.startswith("#"):
            if cur:
                items.append(cur)
            cur = {"title": ls}
        elif ls.startswith("출처:"):
            cur["url"] = ls.split("출처:", 1)[1].strip()
        elif ls.startswith("실행:"):
            cur["action"] = ls.split("실행:", 1)[1].strip()
    if cur:
        items.append(cur)
    return items


def cmd_record(output_path, hist_path, date_str=None):
    today = date_str or datetime.date.today().isoformat()
    output = open(output_path, encoding="utf-8", errors="replace").read()
    hist = load_history(hist_path)
    n = 0
    for it in parse_items(output):
        m = re.search(r'https?://[^\s)"\]]+', it.get("url", ""))
        if not m:
            continue
        raw = m.group(0)
        cu = canonical(raw)
        action = it.get("action", "")
        decision = "review_only" if ("검토만" in action or "review" in action.lower()) else "actionable"
        e = hist.get(cu, {"canonical_url": cu, "first_seen": today, "surfaced_count": 0})
        e["raw_url"] = raw
        e["title"] = it.get("title", "")[:200]
        e["last_seen"] = today
        e["surfaced_count"] = int(e.get("surfaced_count", 0)) + 1
        e["decision"] = decision
        e["decision_reason"] = action[:120]
        hist[cu] = e
        n += 1
    tmp = hist_path + ".tmp"
    with open(tmp, "w") as f:
        for e in hist.values():
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, hist_path)
    print(n)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: daily-research-dedup.py filter|record ...")
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd == "filter":
        log = None
        if "--log" in args:
            i = args.index("--log")
            log = args[i + 1]
            args = args[:i] + args[i + 2:]
        if len(args) < 3:
            sys.exit("filter <candidates> <history> <out> [--log f]")
        cmd_filter(args[0], args[1], args[2], log)
    elif cmd == "record":
        date = None
        if "--date" in args:
            i = args.index("--date")
            date = args[i + 1]
            args = args[:i] + args[i + 2:]
        if len(args) < 2:
            sys.exit("record <output> <history> [--date d]")
        cmd_record(args[0], args[1], date)
    else:
        sys.exit("unknown cmd: %s" % cmd)


if __name__ == "__main__":
    main()
