#!/usr/bin/env python3
"""세션 조율 모음 — 각 세션의 state/coord/status.json 과 실제 관측을 대조해 텔레그램용 평문을 만든다.

신고는 고치지 않는다. 관측은 옆에 따로 붙인다. 표시는 넷이다:
  확인됨  = 관측과 맞음
  신고만  = 기계로 대조할 수 없는 칸(목적·추천·우선순위 등)
  불일치  = 관측과 다름
  미확인  = 관측 못 함(원격 호스트·저장소 불명·확인 끊김)

사용:
  coord.py full            전체 보고 출력
  coord.py delta           직전 발송 기준 달라진 게 있으면 지금 봐야 할 최종 목록,
                           아니면 빈 출력. 풀린 항목은 안 적는다.
  coord.py mark-sent       방금 출력한 상태를 발송 기준으로 고정(전송 성공 뒤에만 부른다)
  coord.py close <세션> <항목> [--v<판본>] [사유]
                           답이 나온 항목을 취합자가 닫는다. 세션 파일은 안 건드린다 —
                           닫은 목록을 따로 들고 거기서 걸러낸다(단일 writer 유지).
                           세션이 같은 id 의 version 을 올리면 새 물음이라 다시 뜬다.
                           --v 를 주면 그 판본일 때만 닫는다. 보고에 적힌 판본을 그대로
                           넘기면 답을 받은 뒤 바뀐 물음을 조용히 덮지 않는다.
"""
import datetime as dt
import glob
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import coord_facts as facts_mod  # noqa: E402


def local_host():
    """이 기계의 신원. 유저봇 host 파일은 릴레이 대상이라 자기 신원이 아니다(claude-job-detach 와 같은 기준)."""
    try:
        with open(os.path.expanduser("~/.config/claude-machine-id")) as f:
            name = f.read().strip()
        if name:
            return name
    except OSError:
        pass
    return os.uname().nodename


LOCAL_HOST = local_host()
# 이 세션이 직접 못 재는 호스트. 접근이 확인되기 전까지는 미확인으로 둔다.
REMOTE_HOSTS = {"train-server": "학습 서버", "robot47": "로봇 47"}
# 확인 기한: 이 시간 넘게 세션이 다시 확인 안 한 항목은 미확인으로 낮춘다(자동 종료는 안 한다).
CHECK_WINDOW = {"decision": dt.timedelta(hours=24), "work": dt.timedelta(hours=6)}
REQUIRED = ("schema", "session", "decisions", "work")


def now():
    return dt.datetime.now().astimezone()


def parse_ts(s):
    return dt.datetime.fromisoformat(s) if s else None


def first_max_number(text):
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text or "")]
    return max(nums) if nums else None


def load_status(items):
    """세션 파일을 읽는다(각 항목은 (세션명, 경로)). 깨진 파일은 그 세션만 오류로 남기고 나머지는 계속한다.
    시각 형식이 틀린 항목은 그 항목만 빼고 오류로 적는다 — 한 칸 때문에 그 세션의 정할 것이
    통째로 사라지면 운영자 화면에는 '없음' 으로 보인다(2026-10-26 실제로 bias·velocity 열 건이
    그렇게 사라졌다)."""
    good, errors = [], []
    for name, p in sorted(items):
        try:
            with open(p) as f:
                d = json.load(f)
            missing = [k for k in REQUIRED if k not in d]
            if missing:
                raise ValueError("칸 없음: " + ",".join(missing))
        except (OSError, ValueError) as e:
            errors.append({"session": name, "path": p, "error": str(e)})
            continue
        for key in ("decisions", "work"):
            keep = []
            for it in list(d[key]):
                try:
                    parse_ts(it.get("checked_at"))
                except (TypeError, ValueError):
                    errors.append({"session": name, "path": p, "error":
                                   f'{it.get("id")} 의 checked_at 을 못 읽어 이 항목만 뺐다'
                                   f'("{it.get("checked_at")}") — '
                                   "2026-10-20T11:58:00+09:00 처럼 적어라"})
                else:
                    keep.append(it)
            d[key] = keep
        # 원문 시각은 취합하는 쪽이 파일을 stat 해서 적는다. 세션이 자기 파일 안에 적으면
        # 그 세션이 멈춘 순간의 값에서 굳어, 멈춘 파일과 방금 갱신한 파일이 같아 보인다.
        d["origin"] = {"mtime": dt.datetime.fromtimestamp(
            os.stat(p).st_mtime).replace(microsecond=0).isoformat()}
        good.append(d)
    return good, errors


def load_closed():
    """취합자가 닫은 항목. 상태지 소스가 아니라 저장소 밖에 둔다."""
    try:
        with open(CLOSED) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def apply_closed(statuses, closed):
    """닫은 항목을 목록에서 뺀다. 남의 status.json 은 소유 세션만 쓴다 — 여기서는 거르기만 한다.
    세션이 version 을 올리면 같은 id 라도 새 물음이라 다시 뜬다."""
    out, dropped = [], []
    for s in statuses:
        keep = []
        for it in s["decisions"]:
            c = closed.get(f'{s["session"]}:{it["id"]}')
            if c and it.get("version", 1) <= c.get("version", 0):
                dropped.append((s["session"], it, c))
            else:
                keep.append(it)
        out.append(dict(s, decisions=keep))
    return out, dropped


def gather_facts(statuses, fx):
    """신고에 나온 PR 만 조회한다. fx 는 facts 모듈과 같은 함수를 가진 객체(시험에서 갈아끼운다)."""
    prs = {}
    for s in statuses:
        for it in s["decisions"] + s["work"]:
            for r in it.get("refs", {}).get("pr", []):
                if r.get("repo"):
                    key = (r["repo"], r["number"])
                    if key not in prs:
                        prs[key] = fx.pr(*key)
    return {"jobs": fx.jobs(), "host": fx.host(), "prs": prs, "sessions": fx.sessions()}


SELF = "dotfiles"  # 이 취합 세션의 유닛 이름

# 이 모음이 맡는 세션. 기계마다 다르므로 설정에서 읽는다 — 한 줄에 세션 이름 하나,
# '#' 로 시작하는 줄은 설명이다.
#   printf '%s\n' alpha beta > ~/.config/claude-coord-scope
# 설정이 없으면 떠 있는 세션을 전부 맡는다. 이름을 지어내지 않는 쪽이고, 맡는 범위가
# 넓어질 뿐이라 빠지는 세션이 생기지 않는다 — 좁히고 싶으면 그때 설정을 만든다.
SCOPE_FILE = os.environ.get(
    "CLAUDE_COORD_SCOPE",
    os.path.join(os.path.expanduser("~"), ".config", "claude-coord-scope"))


def read_scope():
    """맡는 세션 이름들. 설정이 없으면 None(= 전부 맡는다)."""
    try:
        with open(SCOPE_FILE, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    names = {ln.strip() for ln in text.splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")}
    return (names | {SELF}) if names else None


SCOPE = read_scope()


def coverage(statuses, obs):
    """떠 있는 세션과 현황 파일의 짝. 짝이 안 맞는 쪽을 드러낸다."""
    live = None if obs["sessions"] is None else {
        k: v for k, v in obs["sessions"].items() if SCOPE is None or k in SCOPE}
    if live is None:
        return [("미확인", "세션 유닛 목록 조회 실패", "sessfail")]
    have = {s["session"] for s in statuses}
    out = []
    for name in sorted(live):
        if name not in have:
            what = "취합자 자기 현황 아직 안 넣음" if name == SELF else "떠 있는데 현황 파일 없음"
            out.append(("현황 없음", f"{name}: {what}", f"nostatus:{name}"))
    for name in sorted(have - set(live)):
        out.append(("미확인", f"{name}: 세션이 안 떠 있음 — 신고가 낡았을 수 있다", f"offline:{name}"))
    return out


def check_item(s, it, kind, obs, t):
    """항목 하나의 표시 목록. 각 표시는 (종류, 문장, 지문 키)."""
    marks = []
    for r in it.get("refs", {}).get("pr", []):
        label = f'{r.get("repo") or "?"}#{r["number"]}'
        if not r.get("repo"):
            marks.append(("미확인", f'PR "{r.get("text", r["number"])}" 저장소를 못 정한다', f'norepo:{s["session"]}:{r["number"]}'))
            continue
        actual = obs["prs"].get((r["repo"], r["number"]))
        if actual is None:
            marks.append(("미확인", f"{label} 조회 실패", f"prfail:{label}"))
            continue
        for k, v in (r.get("claim") or {}).items():
            a = len(actual.get("reviewers", [])) if k == "reviewers" else actual.get(k)
            if a != v:
                marks.append(("불일치", f"{label} {k}: 신고 {v} / 관측 {a}", f"prmis:{label}:{k}:{v}:{a}"))
            else:
                marks.append(("확인됨", f"{label} {k}={a}", None))
    for u in it.get("refs", {}).get("units", []):
        if obs["jobs"] is None:
            marks.append(("미확인", f"잡 {u} 조회 실패", f"jobfail:{u}"))
        elif not any(name.startswith(u) for name in obs["jobs"]):
            marks.append(("불일치", f"잡 {u}: 신고는 도는 중 / 관측 없음", f"jobgone:{u}"))
        else:
            marks.append(("확인됨", f"잡 {u} 도는 중", None))
    eta = parse_ts((it.get("eta") or {}).get("at"))
    if kind == "work" and eta and t > eta and it.get("state") == "진행":
        marks.append(("불일치", f'예상 끝 {eta:%m-%d %H:%M} 지났는데 갱신 없음',
                      f'overdue:{it["id"]}:{it.get("version", 1)}'))
    ck = parse_ts(it.get("checked_at"))
    if ck and t - ck > CHECK_WINDOW[kind]:
        marks.append(("미확인", f"{ck:%m-%d %H:%M} 뒤로 세션 확인 없음",
                      f'stale:{it["id"]}:{it.get("version", 1)}'))
    for res in it.get("resources", []):
        if res["host"] in REMOTE_HOSTS:
            marks.append(("미확인", f'{REMOTE_HOSTS[res["host"]]} 사용량은 신고값(직접 못 잼)', None))
    return marks


def unlisted_jobs(statuses, obs):
    """돌고 있는데 그 세션 신고 어디에도 없는 잡. 사람 결정의 누락은 이걸로 못 찾는다."""
    if obs["jobs"] is None:
        return []
    claimed = {}
    for s in statuses:
        claimed.setdefault(s["session"], [])
        for it in s["work"]:
            claimed[s["session"]] += it.get("refs", {}).get("units", [])
    out = []
    for name, j in sorted(obs["jobs"].items()):
        sess = j.get("session")
        if sess not in claimed:
            continue
        if not any(name.startswith(u) for u in claimed[sess]):
            out.append((sess, name, j.get("since", "")))
    return out


def contention(statuses, obs):
    """같은 자원을 여러 항목이 잡는 자리. 사용 중끼리는 경합, 요청이 끼면 잠재 경합."""
    lines = []
    host = obs["host"]
    cpu_use, cpu_req, mem_use, mem_req = [], [], [], []
    for s in statuses:
        for it in s["work"]:
            for r in it.get("resources", []):
                if r["host"] != LOCAL_HOST:
                    continue
                n = first_max_number(r["amount"])
                tag = (s["session"], it["id"], r["amount"], n)
                if r["device"] == "cpu":
                    (cpu_use if r["status"] == "in_use" else cpu_req).append(tag)
                elif r["device"] == "mem":
                    (mem_use if r["status"] == "in_use" else mem_req).append(tag)
    if cpu_use and cpu_req:
        use = sum(x[3] or 0 for x in cpu_use)
        req = sum(x[3] or 0 for x in cpu_req)
        if use + req > host["cores"]:
            who_u = ", ".join(f"{x[0]} {x[2]}" for x in cpu_use)
            who_r = ", ".join(f"{x[0]} {x[2]}" for x in cpu_req)
            lines.append(("잠재 경합",
                          f'이 기계 코어: 사용 중 {who_u} + 요청 {who_r} = 최대 {use + req:g} > {host["cores"]}코어 '
                          f'(관측 1분 부하 {host["load1"]})',
                          f"cpu:{'/'.join(sorted(x[1] for x in cpu_use + cpu_req))}"))
    if mem_use or mem_req:
        need = sum(x[3] or 0 for x in mem_use + mem_req)
        if host["mem_avail_gib"] is not None and need > host["mem_avail_gib"]:
            lines.append(("잠재 경합", f'이 기계 메모리: 신고 합 {need:g} > 관측 여유 {host["mem_avail_gib"]}', "mem:local"))
    # 원격 호스트: 두 세션 이상이 같은 호스트를 잡으면 잠재 경합. 장치 이름이 달라도(시뮬 vs 메모리)
    # 같은 기계의 메모리를 나눠 쓰므로 호스트 단위로 묶는다. 관측은 못 하니 신고값만 보인다.
    status_word = {"in_use": "사용 중", "requested": "요청", "approved": "승인"}
    for rh, label in REMOTE_HOSTS.items():
        us = []
        for s in statuses:
            for it in s["decisions"] + s["work"]:
                for r in it.get("resources", []):
                    if r["host"] == rh:
                        us.append((s["session"], it["id"], r))
        if len({u[0] for u in us}) > 1:
            desc = "; ".join(f'{u[0]} {u[1]} {u[2]["device"]} {status_word.get(u[2]["status"], u[2]["status"])} {u[2]["amount"]}'
                             + (f' / {u[2]["of"]}' if u[2].get("of") else "") for u in us)
            lines.append(("잠재 경합", f"{label}: {desc} — 신고값, 관측 못 함",
                          f"remote:{rh}:{'/'.join(sorted(u[1] for u in us))}"))
    return lines


def status_paths(fx):
    """떠 있는 세션마다 자기 작업 디렉토리의 state/coord/status.json 하나. 없는 파일은 뺀다(coverage 가 잡는다)."""
    live = {k: v for k, v in (fx.sessions() or {}).items()
            if SCOPE is None or k in SCOPE}
    out = []
    for name in sorted(live):
        p = os.path.join(live[name], "state", "coord", "status.json")
        if os.path.exists(p):
            out.append((name, p))
    return out


def build(status_dir, fx, t=None, proposals=None):
    """status_dir 가 폴더면 그 안의 *.json, None 이면 떠 있는 세션들의 실제 경로를 읽는다."""
    t = t or now()
    if status_dir is None:
        items = status_paths(fx)
    else:
        items = [(os.path.splitext(os.path.basename(p))[0], p)
                 for p in glob.glob(os.path.join(status_dir, "*.json"))]
    statuses, errors = load_status(items)
    statuses, dropped = apply_closed(statuses, load_closed())
    obs = gather_facts(statuses, fx)
    report = {"t": t, "errors": errors, "sessions": [], "unlisted": unlisted_jobs(statuses, obs),
              "contention": contention(statuses, obs), "coverage": coverage(statuses, obs),
              "host": obs["host"], "proposals": proposals or [], "closed": dropped}
    for s in statuses:
        sess = {"session": s["session"], "origin": s.get("origin", {}), "decisions": [], "work": []}
        for kind, key in (("decision", "decisions"), ("work", "work")):
            for it in s[key]:
                sess[key].append({"item": it, "marks": check_item(s, it, kind, obs, t)})
        report["sessions"].append(sess)
    return report


def fingerprints(report):
    """보낼 가치가 있는 사건의 지문. 진행률 같은 문장 변화는 지문에 안 넣는다(반복 알림 방지)."""
    fp = {}
    for e in report["errors"]:
        fp[f'err:{e["session"]}'] = f'{e["session"]} 파일 오류: {e["error"]}'
    for s in report["sessions"]:
        for d in s["decisions"]:
            it = d["item"]
            # version 은 REQUIRED 가 아니다 — 나머지 경로가 전부 기본값 1 로 읽으므로 여기도
            # 같이 읽는다. 직접 색인하면 그 칸 없는 세션 파일 하나가 mark-sent 를 통째로
            # 죽이고, 기준이 안 고정돼 다음 회차가 바뀐 것만 고르지 못한다.
            fp[f'dec:{it["id"]}:{it.get("version", 1)}'] = f'{s["session"]} — {it["title"]}'
        for key in ("decisions", "work"):
            for x in s[key]:
                for kind, text, k in x["marks"]:
                    if k:
                        fp[k] = f'[{kind}] {x["item"]["id"]} {text}'
    for sess, name, since in report["unlisted"]:
        fp[f"unl:{name}"] = f"[미등재 잡] {sess}: {name} ({since} 부터)"
    for kind, text, k in report["contention"] + report["coverage"]:
        fp[k] = f"[{kind}] {text}"
    return fp


def render_brief(report):
    """운영자용: 정할 것만 한 줄씩. 추천·자원·대조는 물을 때 render_full 로 보여준다."""
    items = [(s["session"], d["item"]) for s in report["sessions"] for d in s["decisions"]]
    # 읽기 실패는 '없음' 과 같은 뜻이 아니다. 조용히 삼키면 못 읽은 세션의 정할 것이
    # 화면에서 사라지고 운영자는 다 닫힌 것으로 읽는다(R16).
    bad = [f'읽기 실패 — {e["session"]}: {e["error"]}' for e in report.get("errors", [])]
    if not items:
        head = f'{report["t"]:%H:%M} 지금 정할 것 없음'
        return "\n".join([head] + bad) if bad else head
    L = [f'정할 것 {len(items)}개 ({report["t"]:%m-%d %H:%M})'] + bad
    # 표식을 같이 적는다. 순번은 그 회차의 자리라 앞 항목이 닫히면 다른 물음을 가리키는데,
    # 운영자는 번호로 답하고 그 답이 다음 회차에 읽힐 수 있다.
    L += [f'{i}. [{it["id"]} v{it.get("version", 1)}] {sess} — {it["title"]}'
          for i, (sess, it) in enumerate(items, 1)]
    L.append("자세한 건 번호로 물어보면 된다.")
    return "\n".join(L)


def render_full(report):
    L = []
    t = report["t"]
    h = report["host"]
    L.append(f"세션 조율 모음 {t:%m-%d %H:%M}")
    L.append(f'{LOCAL_HOST} 관측: 1분 부하 {h["load1"]} / {h["cores"]}코어, 여유 메모리 {h["mem_avail_gib"]} GiB')
    for e in report["errors"]:
        L.append(f'! {e["session"]} 파일을 못 읽음: {e["error"]}')
    for kind, text, _ in report["coverage"]:
        L.append(f"[{kind}] {text}")
    L.append("")
    L.append("정할 것 (세션 원문 그대로, 번호는 세션 것)")
    for s in report["sessions"]:
        o = s["origin"]
        src = f' — 원문 {o.get("mtime", "")[5:16].replace("T", " ")}' if o else ""
        L.append(f'■ {s["session"]}{src}')
        for d in s["decisions"]:
            it = d["item"]
            L.append(f'{it["id"]}. {it["title"]}')
            if it.get("options"):
                L.append("   선택지: " + " / ".join(it["options"]))
            L.append(f'   추천(세션): {it.get("recommendation", "-")}')
            L.append(f'   안 정하면: {it.get("blocks", "-")}')
            for kind, text, _ in d["marks"]:
                if kind != "확인됨":
                    L.append(f"   [{kind}] {text}")
    for sess, it, c in report.get("closed", []):
        L.append(f'[닫음] {sess} {it["id"]} v{it.get("version", 1)} — {it["title"]} '
                 f'({c.get("at", "")[5:16].replace("T", " ")} · {c.get("why", "사유 없음")})')
    L.append("")
    L.append("업무 (우선순위는 세션 제안 — 전체 순위는 운영자가 정한다)")
    for s in report["sessions"]:
        for w in s["work"]:
            it = w["item"]
            eta = (it.get("eta") or {}).get("text") or "-"
            L.append(f'{it["id"]} {s["session"]} [{it.get("state", "-")} · 제안 {it.get("priority", "-")}] '
                     f'{it["title"]} — 끝: {eta}')
            for kind, text, _ in w["marks"]:
                if kind in ("불일치", "미확인") and "신고값" not in text:
                    L.append(f"   [{kind}] {text}")
    L.append("")
    L.append("자원")
    for kind, text, _ in report["contention"]:
        L.append(f"[{kind}] {text}")
    for sess, name, since in report["unlisted"]:
        L.append(f"[미등재 잡] {sess}: {name} — {since} 부터 도는데 신고에 없음")
    if report["proposals"]:
        L.append("")
        L.append("취합자 제안 (세션 신고 아님)")
        for p in report["proposals"]:
            L.append(f"- {p}")
    L.append("")
    L.append("표시: 확인됨=관측과 맞음, 불일치=관측과 다름, 미확인=못 잼. 목적·추천·우선순위는 세션 신고라 기계로 확인 안 됨.")
    return "\n".join(L)


def render_delta(report, baseline):
    """바뀐 게 있을 때만, 지금 봐야 할 최종 목록을 그대로 보낸다(운영자 7298).
    풀린 항목은 안 적는다 — 이미 볼 필요가 없는 것이라 목록에서 빠지는 것으로 충분하다.
    남은 것이 없으면 조용하다. 대조·자원 변화는 지문에 남아 물을 때 꺼낸다."""
    cur = {k: v for k, v in fingerprints(report).items() if k.startswith("dec:")}
    base = {k: v for k, v in baseline.items() if k.startswith("dec:")}
    if cur == base or not cur:
        return ""
    return render_brief(report)


# 발송 기준은 저장소 밖에 둔다 — 상태지 소스가 아니다.
STATE = os.path.expanduser("~/.claude/state/coord-last-sent.json")
CLOSED = os.path.expanduser("~/.claude/state/coord-closed.json")
PROPOSALS = []


def close_item(session, item_id, why, fx=facts_mod, answered_version=None):
    """답이 나온 항목을 닫는다. 그 항목이 지금 실제로 올라와 있을 때만 닫는다 — 오타로 빈 칸을 적지 않게.

    answered_version 을 주면 그 판본일 때만 닫는다. 답을 받은 뒤 세션이 물음을 고쳐
    version 을 올렸으면, 그 답은 새 물음의 답이 아니라서 닫으면 새 물음이 조용히 사라진다."""
    statuses, _ = load_status(status_paths(fx))
    for s in statuses:
        for it in s["decisions"]:
            if s["session"] == session and it["id"] == item_id:
                cur = it.get("version", 1)
                if answered_version is not None and answered_version != cur:
                    return 1, (f'{session} {item_id} 는 지금 v{cur} 인데 답은 v{answered_version} 것이다 '
                               f'— 물음이 바뀌었으니 새 물음으로 다시 물어라')
                closed = load_closed()
                closed[f"{session}:{item_id}"] = {"version": it.get("version", 1),
                                                  "at": now().isoformat(timespec="seconds"),
                                                  "why": why or "운영자 답"}
                tmp = CLOSED + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(closed, f, ensure_ascii=False, indent=1)
                os.replace(tmp, CLOSED)
                return 0, f'닫음: {session} {item_id} v{it.get("version", 1)} — {it["title"]}'
    return 1, f"{session} 의 {item_id} 를 지금 올라온 정할 것에서 못 찾았다 — 이름을 확인해라"


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "brief"
    if cmd == "close":
        rest = list(argv[2:])
        ver = None
        for i, a in enumerate(rest):
            if a.startswith("--v") and a[3:].isdigit():
                ver = int(a[3:])
                del rest[i]
                break
        if len(rest) < 2:
            print("쓰기: coord.py close <세션> <항목id> [--v<판본>] [사유]", file=sys.stderr)
            return 2
        rc, msg = close_item(rest[0], rest[1], " ".join(rest[2:]), answered_version=ver)
        print(msg, file=sys.stderr if rc else sys.stdout)
        return rc
    rep = build(None, facts_mod, proposals=PROPOSALS)
    if cmd == "brief":
        print(render_brief(rep))
    elif cmd == "full":
        print(render_full(rep))
    elif cmd == "delta":
        try:
            with open(STATE) as f:
                base = json.load(f)
        except (OSError, ValueError):
            base = {}
        print(render_delta(rep, base))
    elif cmd == "mark-sent":
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(fingerprints(rep), f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
        print("기준 고정:", hashlib.sha1(open(STATE, "rb").read()).hexdigest()[:8])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
