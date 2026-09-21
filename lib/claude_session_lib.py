"""세션 transcript 를 고르는 규칙 한 벌. 감시기들이 각자 복제하지 않게 한다.

왜 있나: 2026-08-03 하루에 같은 결함을 두 번 고쳤다. `claude-api-5xx-watcher` 가
mtime 최신 jsonl 을 세션 transcript 로 집던 버그를 고쳤더니 `claude-stall-guard` 에
같은 코드가 복제돼 있어 또 고쳐야 했다. 프로젝트 키 계산(`[/_.]` 치환)도 마찬가지로
두 곳에 같은 오류가 있었다. 규칙이 세 벌이면 고칠 일도 세 번이고, 세 번째를 빠뜨리면
그 감시기만 조용히 틀린 파일을 본다.

쓰는 쪽: claude-api-5xx-watcher / claude-stall-guard / claude-session-probe.
전부 repo bin/ 을 가리키는 심링크라 `os.path.realpath(__file__)` 기준 `../lib` 로 잡힌다.

호출부가 자기 상수를 그대로 쓰도록 `projects` 를 인자로 받는다(테스트가 임시
디렉토리로 갈아끼운다). 경고 문구는 감시기마다 나가는 곳이 달라서 `on_fallback`
콜백으로 넘긴다 — 여기서 직접 찍으면 stderr 로 새거나 로그 파일이 갈린다.
"""
import json
import os
import re
import time

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
STATUSLINE_CACHE = os.path.join(HOME, ".claude", "statusline_cache.json")
SCAN_LINES = 60          # transcript 종류는 헤더 근처에서 갈린다
CWD_SCAN_LINES = 200     # cwd 도 헤더 근처. 통째로 읽지 않는다
CWD_SCAN_FILES = 5


def _notify(cb, msg):
    if cb:
        try:
            cb(msg)
        except Exception:
            pass


def utc_epoch(ts):
    """ISO 타임스탬프(UTC 표기) → epoch. timegm 이어야 함 — mktime 은 9시간 어긋난다."""
    import calendar
    try:
        return calendar.timegm(time.strptime((ts or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0


def jsonls(pdir):
    """그 디렉토리의 transcript 후보. mtime 최신순. 마이그레이션 잔재는 뺀다."""
    out = []
    try:
        entries = os.listdir(pdir)
    except OSError:
        return out
    for fn in entries:
        if not fn.endswith(".jsonl") or fn.endswith(".MIGRATED.jsonl"):
            continue
        p = os.path.join(pdir, fn)
        try:
            if not os.path.isfile(p):     # *.jsonl 이름의 디렉토리·특수파일 배제
                continue
            out.append((os.path.getmtime(p), p))
        except OSError:
            continue
    out.sort(reverse=True)
    return [p for _, p in out]


def recorded_cwd(pdir):
    """그 디렉토리의 jsonl 이 스스로 적어둔 cwd. 키 계산 규칙을 안 믿기 위한 정답지."""
    for path in jsonls(pdir)[:CWD_SCAN_FILES]:
        try:
            with open(path, errors="replace") as fh:
                for _ in range(CWD_SCAN_LINES):
                    line = fh.readline()
                    if not line:
                        break
                    if '"cwd"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(o, dict):   # 레코드가 list·문자열일 수 있다
                        continue
                    c = o.get("cwd")
                    if c:
                        return c
        except OSError:
            continue
    return None


def project_dir(cwd, projects=None, on_fallback=None):
    """세션 cwd → `~/.claude/projects/<key>` 디렉토리. 못 찾으면 None.

    Claude Code 는 경로의 비영숫자를 전부 '-' 로 바꾼다(슬래시뿐 아니라 밑줄도).
    슬래시만 치환하던 옛 계산은 `~/session_c_work` 같은 경로에서
    빗나갔고, 그 세션은 도입 이후 내내 감시 사각지대였다(2026-07-30 peer 실측).

    계산으로만 믿지 않는다 — 인코딩이 또 바뀌면 조용히 빗나간다. 계산값이 없으면
    각 디렉토리의 jsonl 이 기록한 cwd 로 역조회하고, 그때는 반드시 알린다.
    """
    if not cwd:
        return None
    root = projects or PROJECTS
    guess = os.path.join(root, re.sub(r"[^a-zA-Z0-9]", "-", cwd))
    if os.path.isdir(guess):
        return guess
    try:
        for fn in os.listdir(root):
            d = os.path.join(root, fn)
            if os.path.isdir(d) and recorded_cwd(d) == cwd:
                _notify(on_fallback,
                        "경로 키 계산이 빗나갔다 — 역조회로 찾음: cwd=%s → %s "
                        "(Claude Code 인코딩 규칙이 바뀐 것일 수 있다)" % (cwd, fn))
                return d
    except OSError:
        pass
    return None


def transcript_kind(path, scan_lines=SCAN_LINES):
    """'cli'(대화형 세션) / 'sdk-cli'(claude -p 일회성) / 'sidechain' / None(모름).

    한 프로젝트 디렉토리에 그 세션 transcript 만 있는 게 아니다. progress-updater
    같은 `claude -p` 일회성 호출이 같은 cwd 로 돌면 자기 jsonl 을 같은 폴더에 남긴다
    (home 기준 -home-<사용자>-dotfiles 12개 중 11개가 그것, -home-<사용자> 는 845개).
    레코드의 entrypoint 필드가 둘을 정확히 가른다: 대화형은 'cli', -p 는 'sdk-cli'.
    """
    try:
        with open(path, errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= scan_lines:
                    break
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if not isinstance(o, dict):
                    continue
                if o.get("isSidechain"):
                    return "sidechain"
                if o.get("entrypoint"):
                    return o["entrypoint"]
    except OSError:
        return None
    return None


def session_transcript(pdir, on_fallback=None):
    """그 세션의 대화형 transcript. mtime 최신이 아니라 entrypoint 로 고른다.

    mtime 최신을 집으면 15분마다 도는 progress-updater 의 일회성 transcript 가
    세션 것보다 나중에 쓰인 순간 그게 뽑힌다. 하필 세션이 멈춰 파일이 안 자라면
    일회성 파일이 영구히 최신이다 — 감시가 필요한 바로 그때 남의 파일을 보고
    "정상"으로 판정한다. -home-<사용자>-mon-session-j 에서 4초 차이로 실제 그 상태였다.

    entrypoint 를 안 남기던 옛 transcript 는 배제하면 그 세션이 사각지대가 되므로
    쓰되, 규칙이 또 바뀐 경우와 구분되게 알린다.
    """
    unknown = None
    for p in jsonls(pdir):
        kind = transcript_kind(p)
        if kind == "cli":
            return p
        if kind is None and unknown is None:
            unknown = p
    if unknown is not None:
        _notify(on_fallback,
                "%s: entrypoint=cli 인 transcript 가 없어 %s 로 대체했다 "
                "(레코드 형식이 바뀐 것일 수 있다)"
                % (os.path.basename(pdir), os.path.basename(unknown)))
    return unknown


QUEUE_DIR = os.path.join(HOME, ".claude", "state", "rate-limit-queue")
CACHE_FRESH_SEC = 15 * 60   # statusline 캐시가 이보다 오래되면 수치를 믿지 않는다
QUOTA_BLOCK = 90            # 이 이상이면 한도로 본다(가드 공통값)


def quota_5h_state(cache=None):
    """5h 사용률을 상태와 함께 돌려준다: {status, used, cached_at, resets_at}.

    status 는 fresh | stale | expired | missing | error 다. 숫자만 돌려주면 안 되는
    이유가 실측으로 드러났다(2026-08-06 밤) — statusline 캐시는 세션이 턴을 돌며
    statusline 을 렌더할 때만 갱신된다. 한도로 차단된 세션은 턴이 없으니 캐시가
    차단 직전 값에 그대로 언다. 그날 5시간 40분 차단 내내 38% 로 읽혔고, 90% 임계를
    보던 가드는 한 번도 안 걸렸다. 가장 확실히 차단된 순간에 "한도 아님" 이라고
    답한 것이다. 게다가 옛 구현은 resets_at 이 지나면 0.0 을 돌려줬는데, 얼어붙은
    resets_at 은 차단 중에 반드시 지나가므로 그 뒤로는 0% 로 위조됐다.

    그래서 "모름" 을 숫자로 뭉개지 않는다. 차단 판정은 rate_limited() 를 쓰고,
    이 함수의 수치는 fresh 일 때만 근거가 된다."""
    path = cache or STATUSLINE_CACHE
    try:
        with open(path) as fh:
            d = json.load(fh)
    except FileNotFoundError:
        return {"status": "missing", "used": None, "cached_at": None, "resets_at": None}
    except Exception:
        return {"status": "error", "used": None, "cached_at": None, "resets_at": None}
    w = (d.get("rate_limits") or {}).get("five_hour") or {}
    ca, rt, u = d.get("_cached_at"), w.get("resets_at"), w.get("used_percentage")
    used = float(u) if isinstance(u, (int, float)) else None
    out = {"status": "fresh", "used": used, "cached_at": ca, "resets_at": rt}
    if not isinstance(ca, (int, float)) or time.time() - ca > CACHE_FRESH_SEC:
        out["status"] = "stale"          # 세션이 턴을 안 돌고 있다 = 수치가 얼었다
    elif isinstance(rt, (int, float)) and time.time() > rt:
        out["status"] = "expired"        # 창은 지났는데 캐시가 아직 옛 창을 들고 있다
    elif used is None:
        out["status"] = "error"
    return out


def _window_state(w, now):
    """한 창(5시간/7일)의 상태. quota_5h_state 와 같은 규칙을 창 단위로 쓴다."""
    rt, u = w.get("resets_at"), w.get("used_percentage")
    used = float(u) if isinstance(u, (int, float)) else None
    if used is None:
        return {"status": "invalid", "used": None, "resets_at": rt}
    if isinstance(rt, (int, float)) and now > rt:
        return {"status": "expired", "used": used, "resets_at": rt}
    return {"status": "fresh", "used": used, "resets_at": rt}


def account_quota_observation(cache=None):
    """두 창을 함께 내놓는다: {status, cached_at, five_hour, seven_day}.

    바깥 status 가 fresh 가 아니면 안쪽 수치는 근거가 아니다. 소비자는 이 함수 하나만
    쓴다 — 각자 캐시를 파싱하지 않는다.

    왜 한 벌이어야 하나(2026-08-28 실사고). 같은 계산이 rate-limit-recovery ·
    rate-limit-guard.sh · rate-limit-prompt-guard.sh 에 세 벌 복제돼 있었고, 셋 다
    cached_at 을 안 봤다. 그 계산은 resets_at 이 지난 창만 0 으로 만드는데, 얼어붙은
    캐시의 7일 창은 리셋이 아직 미래라 얼어붙은 99% 가 그대로 살아남는다. 그래서
    한도가 실제로 풀린 뒤에도 세션이 계속 차단됐다 — 옆 기계가 그렇게 6시간을
    갇혔고, 도구가 막히니 턴이 없고, 턴이 없으니 캐시가 갱신되지 않아 스스로
    못 빠져나오는 순환이었다. 하나만 고치면 나머지 둘이 그대로 막는다.

    낡은 캐시는 차단의 근거도 해제의 근거도 아니다. 차단 쪽은 fail-open 이라
    한 턴이 429 로 실패하면 그 턴이 캐시를 새로 채워 다음부터 정상 판정된다 —
    유계다. 해제 쪽은 fail-open 이 아니다(큐를 성급히 비우면 실제로 잃는다).
    큐가 있는데 관측이 fresh 가 아니면 실측으로 확정한다(ai-debate
    run-20260829T232742Z 결론 D).
    """
    path = cache or STATUSLINE_CACHE
    d, status = None, "fresh"
    try:
        with open(path) as fh:
            d = json.load(fh)
    except FileNotFoundError:
        status = "missing"
    except Exception:
        status = "error"
    empty = {"status": "unknown", "used": None, "resets_at": None}
    if d is None:
        return {"status": status, "cached_at": None,
                "five_hour": dict(empty), "seven_day": dict(empty)}
    now = time.time()
    ca = d.get("_cached_at")
    rl = d.get("rate_limits") or {}
    out = {"status": "fresh", "cached_at": ca,
           "five_hour": _window_state(rl.get("five_hour") or {}, now),
           "seven_day": _window_state(rl.get("seven_day") or {}, now)}
    if not isinstance(ca, (int, float)) or now - ca > CACHE_FRESH_SEC:
        out["status"] = "stale"   # 세션이 턴을 안 돌고 있다 = 수치가 얼었다
    return out


def quota_blocked(cache=None, block_5h=None, block_7d=99):
    """(차단인가, 사유). fresh 관측이 임계를 넘을 때만 True.

    낡은 관측은 차단 근거가 아니다 — 그래야 얼어붙은 수치가 세션을 영구히 가두지
    못한다. 모르면 열어두고, 실제로 막혀 있으면 그 턴이 429 로 실패하며 캐시를
    새로 채운다."""
    obs = account_quota_observation(cache)
    if obs["status"] != "fresh":
        return False, "quota=%s(수치 안 믿음)" % obs["status"]
    lim = {"five_hour": QUOTA_BLOCK if block_5h is None else block_5h,
           "seven_day": block_7d}
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        w = obs[key]
        if w["status"] == "fresh" and w["used"] is not None and w["used"] >= lim[key]:
            return True, "%s %s%% (>= %s%%)" % (label, int(w["used"]), lim[key])
    return False, "quota=fresh 임계 아래"


def quota_5h(cache=None):
    """5h 사용률(%). 모르면 -1. fresh 일 때만 수치를 준다.

    옛 구현은 stale/expired 를 0.0 으로 돌려줘 "한도 아님" 의 적극적 근거로 쓰였다.
    모르는 것을 0 으로 답하지 않는다 — 표시용으로도 -1(모름)이 정직하다."""
    st = quota_5h_state(cache)
    return st["used"] if st["status"] == "fresh" and st["used"] is not None else -1


def queue_pending(proj, queue_dir=None):
    """한도로 밀려 대기 중인 작업의 바이트 수. 없으면 0.

    이게 차단의 양성 증거다. 차단 중에는 도달 못한 작업이 이 파일에 append 되고
    (hook 이 쓰므로 세션 턴과 무관하게 갱신된다), 해제되면 flush 되어 0바이트가 된다.
    세션이 그냥 놀고 있으면 이 파일은 비어 있다 — 그래서 '차단' 과 '유휴' 가 구분된다."""
    try:
        return os.path.getsize(os.path.join(queue_dir or QUEUE_DIR, f"{proj}.jsonl"))
    except OSError:
        return 0


def rate_limited(proj, queue_dir=None, cache=None):
    """(차단인가, 사유). 큐가 1차 양성 신호이고 fresh quota 는 보조다.

    stale/unknown quota 를 "한도 아님" 의 근거로 쓰지 않는다 — 그게 2026-08-06 밤
    오탐의 원인이었다. 반대로 모름을 차단으로 치지도 않는다: 캐시나 큐가 영구히
    굳으면 데드맨이 조용히 죽는다. 모름은 fail-open(차단 아님)이고, 그때의 오탐
    비용은 호출부의 재시도 상한이 유계로 만든다."""
    n = queue_pending(proj, queue_dir)
    if n > 0:
        return True, f"큐 대기 {n}바이트"
    st = quota_5h_state(cache)
    if st["status"] == "fresh" and st["used"] is not None and st["used"] >= QUOTA_BLOCK:
        return True, f"사용률 {st['used']}%"
    return False, f"큐 비었고 quota={st['status']}" + (
        f" {st['used']}%" if st["status"] == "fresh" and st["used"] is not None else "")
