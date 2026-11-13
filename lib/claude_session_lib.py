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
CLAUDE_JSON = os.path.join(HOME, ".claude.json")
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
    슬래시만 치환하던 옛 계산은 `/home/u/some_work_dir` 처럼 밑줄이 든 경로에서
    빗나갔고, 그런 세션은 도입 이후 내내 감시 사각지대였다(2026-07-30 실측).

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
    (한 기계 실측: 어느 프로젝트 폴더는 12개 중 11개가 그것, 홈 폴더 쪽은 845개).
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
    "정상"으로 판정한다. 실제로 한 세션에서 4초 차이로 그 상태였다.

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
LOGIN_APPLIED = os.path.join(HOME, ".claude", "state", "rate-limit-login-applied")
LOGIN_FUTURE_SLACK_SEC = 120   # 이보다 더 미래인 로그인 표시는 안 믿는다


def login_applied_at(path=None):
    """마지막으로 로그인이 적용된 시각(유닉스 초). 없거나 못 믿으면 None.

    claude-accounts --apply-login 이 자격증명을 갈아끼울 때만 찍는다.
    토큰 자동 갱신은 이 경로를 안 타므로 표시가 안 생긴다.

    앞선 시각은 안 믿는다. 이 값이 미래면 캐시가 아무리 갱신돼도 계속 그보다 먼저라
    relogin 상태에서 못 빠져나온다 — 시계가 틀어졌거나 손으로 쓴 값이면 가드가 영구히
    열린 채로 남는다. 한 뼘(LOGIN_FUTURE_SLACK_SEC)은 봐준다: 로그인 직후 표시와
    이 판정 사이의 시계 흔들림까지 막으면 정작 고치려던 자리가 안 열린다.
    """
    try:
        with open(path or LOGIN_APPLIED) as fh:
            ts = float(fh.read().strip())
    except (OSError, ValueError, TypeError):
        return None
    return None if ts > time.time() + LOGIN_FUTURE_SLACK_SEC else ts


ACCOUNT_GENERATION = os.path.join(HOME, ".claude", "state", "account-generation")
ACCOUNT_MARKER = os.path.join(HOME, ".claude", "state", "account-current")
LIVE_CRED = os.path.join(HOME, ".claude", ".credentials.json")
ACCOUNTS_ROOT = os.path.join(HOME, "claude-accounts")


def account_generation(path=None):
    """지금 계정이 몇 번째 교체인가. 교체를 한 번도 안 한 기계면 None.

    `bin/claude-accounts` 가 자격을 갈아끼울 때마다 하나씩 올린다. 쓰는 곳은 하나다 —
    캐시에 적힌 수치가 "지금 계정으로 갈아끼운 뒤에 실제로 받아온 값인가" 를 가른다.

    왜 계정 번호만으로는 모자라나(2026-11-09 실측). 계정은 요청마다 다시 읽히지만
    세션이 들고 있는 한도 수치는 그 세션이 마지막으로 받은 응답의 것이다. 교체 직전에
    응답을 받아 둔 세션이 교체 뒤에 상태줄을 그리면, 그때 읽히는 계정은 이미 새 계정이라
    옛 수치에 새 계정 표가 붙는다. 계정 비교가 통과하니 other-account 가 안 걸리고,
    높은 값 유지 병합이 그 수치를 되살려 전 세션이 남의 한도로 막힌다. 그날 홈에서
    88퍼센트가 그렇게 눌러앉았고 실제 계정은 14퍼센트였다 — 임계까지 2퍼센트포인트였다.

    세대는 그 자리를 가른다. 교체 뒤에 그 세션이 실제로 한 번 더 그렸을 때만 그 수치를
    이 세대 것으로 인정한다(claude/statusline.py). 교체 전에 받아 둔 응답은 교체 뒤
    첫 렌더 한 번에만 실리므로, 그 한 번을 빼면 남는 것은 새 계정이 돌려준 수치뿐이다.
    """
    try:
        with open(path or ACCOUNT_GENERATION) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError, TypeError):
        return None


def _file_digest(path):
    """그 파일 내용의 지문. 못 읽으면 None. 내용은 어디에도 안 남긴다."""
    import hashlib
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def seat_account_id(marker=None, root=None, live_cred=None):
    """이름표가 가리키는 자리의 계정 번호. 자격이 그 자리 것일 때만 돌려준다.

    `bin/claude-accounts` 는 갈아끼울 때 자격 파일과 이름표를 같이 쓴다. 그래서
    "지금 어느 계정으로 요청이 나가나" 의 가장 가까운 근거는 그 둘이다.

    자격 지문을 맞춰 보는 이유: 사람이 `claude login` 으로 직접 갈아끼우면 이름표는
    안 따라온다. 그때 이름표를 그대로 믿으면 이 함수가 틀린 계정을 가리킨다.

    지문이 같다는 것은 제한적인 긍정 근거이고, 다르다는 것은 계정이 바뀌었다는
    증거가 아니다 — 토큰이 저절로 갱신되기만 해도 달라진다. 그래서 다를 때는
    모름(None)으로 떨어뜨리고, 그 모름을 어떻게 다룰지는 `account_identity` 가 정한다.
    """
    try:
        with open(marker or ACCOUNT_MARKER) as fh:
            name = fh.read().strip()
    except OSError:
        return None
    if not name or "/" in name or name.startswith("."):
        return None
    base = root or os.environ.get("CLAUDE_ACCOUNTS_ROOT") or ACCOUNTS_ROOT
    live = _file_digest(live_cred or LIVE_CRED)
    if live is None or live != _file_digest(
            os.path.join(base, name, ".claude", ".credentials.json")):
        return None
    try:
        with open(os.path.join(base, name, ".claude.json")) as fh:
            acc = json.load(fh).get("oauthAccount")
    except (OSError, ValueError):
        return None
    if not isinstance(acc, dict):
        return None
    uid = acc.get("accountUuid")
    return uid if isinstance(uid, str) and uid else None


def account_identity(path=None, marker=None, root=None, live_cred=None):
    """(어디서 알았나, 계정 번호). 셋 중 하나다.

    - `("seat", 번호)` — 이름표가 가리키는 자리의 자격이 지금 쓰는 자격과 같다.
      가장 가까운 근거다.
    - `("config", 번호 또는 None)` — 이름표가 아예 없는 기계다. 갈아끼우는 장치를
      안 쓰므로 예전처럼 `~/.claude.json` 을 읽는다.
    - `("unverified", None)` — 이름표는 있는데 그 자리와 안 맞는다. 모른다는 뜻이지
      계정이 바뀌었다는 뜻이 아니다.

    셋을 가르는 이유(2026-11-10 토론 run-20261109T232244Z): 지문이 다를 때 설정
    파일로 되돌아가면, 토큰이 저절로 갱신되기만 해도 번호가 낡은 값으로 바뀐다.
    복구 장치는 번호가 바뀐 것을 계정 교체로 읽어 캐시를 비우고 전 세션을 깨우므로,
    아무도 아무것도 안 한 회차에 가짜 교체가 선다. 모르는 것은 모른다고 내는 편이
    틀린 번호를 확신하는 것보다 낫다 — 받는 쪽은 번호가 없으면 아무 일도 안 한다.
    """
    if path is not None:
        return ("config", _config_account_id(path))
    try:
        with open(marker or ACCOUNT_MARKER) as fh:
            name = fh.read().strip()
    except OSError:
        name = ""
    if not name:
        # 갈아끼운 적 없는 기계. 예전 그대로 설정 파일을 믿는다.
        return ("config", _config_account_id(None))
    uid = seat_account_id(marker, root, live_cred)
    return ("seat", uid) if uid else ("unverified", None)


def current_account_id(path=None, marker=None, root=None, live_cred=None):
    """지금 쓰는 자격의 계정 식별자. 모르면 None.

    `account_identity` 의 번호 쪽만 꺼낸 것이다. 이름표가 가리키는 자리의 자격이 지금
    자격과 같으면 그 자리의 번호, 이름표가 없으면 `~/.claude.json` 의
    `oauthAccount.accountUuid`, 그 외에는 None 이다. `path` 를 직접 주면 그 파일만 읽는다.
    이 값은 비밀이 아니다 — 토큰은 따로 있고, 자격 파일은 지문을 내는 데에만 연다.

    왜 설정 파일 하나로는 안 되나(2026-11-10 실측): 그 파일의 계정 번호는 로그인할 때
    적히고 갈아끼울 때는 안 바뀐다. 08시에 공용 계정에서 개인 계정으로 갈아끼운 뒤에도
    그 파일은 14분 넘게 공용 계정 번호를 들고 있었고, 실제로 나가는 요청과 돌아온 수치는
    개인 계정 것이었다. 그래서 개인 계정 수치에 공용 계정 이름표가 붙는다. 게다가 그
    파일은 도는 세션이 자기 시작 시점 번호로 다시 쓰므로, 어느 세션이 마지막으로 썼느냐에
    따라 값이 오간다 — 계정이 바뀐 것을 알아보는 장치가 양쪽에서 같은 낡은 값을 보면
    눈이 먼다.

    왜 필요한가: 공유 한도 캐시에는 계정 표시가 없었다. 그래서 계정을 갈아끼운
    직후에도 옛 계정의 수치가 그대로 근거로 쓰였고, 2026-09-20 14:43 에 옛 계정의
    7일 99퍼센트를 신선한 값으로 들고 전 세션이 갇혔다. 사람이 bypass 를 손으로
    켜서 빠져나오는 것 말고는 길이 없었다.
    """
    return account_identity(path, marker, root, live_cred)[1]


def _config_account_id(path=None):
    """`~/.claude.json` 의 계정 번호. 못 읽으면 None."""
    try:
        with open(path or CLAUDE_JSON) as fh:
            d = json.load(fh)
    except Exception:
        return None
    acc = d.get("oauthAccount")
    if not isinstance(acc, dict):
        return None
    uid = acc.get("accountUuid")
    return uid if isinstance(uid, str) and uid else None


def account_name(uuid, root=None):
    """그 계정 번호가 어느 자리 이름인가. 못 찾으면 빈 글자.

    계정 번호는 사람이 읽을 글에 그대로 쓸 수 없다 — 운영자가 보는 것은 acct-a·acct-b
    같은 자리 이름이다(R03). 자리마다 자기 홈이 있고 그 홈의 `.claude.json` 에
    번호가 적혀 있으므로, 번호를 받아 되짚는다. 지금 계정이 무엇인가를 묻는
    `bin/claude-accounts` 의 길과 다른 물음이다 — 그쪽은 표식을 먼저 보고 지금
    하나를 답하고, 이쪽은 지난 기록에 적힌 아무 번호나 이름으로 되돌린다.
    """
    if not isinstance(uuid, str) or not uuid:
        return ""
    base = root or os.environ.get("CLAUDE_ACCOUNTS_ROOT") or \
        os.path.join(HOME, "claude-accounts")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return ""
    for name in names:
        try:
            with open(os.path.join(base, name, ".claude.json")) as fh:
                acc = json.load(fh).get("oauthAccount")
        except (OSError, ValueError):
            continue
        if isinstance(acc, dict) and acc.get("accountUuid") == uuid:
            return name
    return ""


NO_FIVE_HOUR = os.path.join(HOME, ".config", "claude-usage", "no-five-hour")


def no_five_hour(uuid, path=None, root=None):
    """그 계정에 5시간 창이 없나. 설정 파일에 자리 이름이 한 줄에 하나씩 적혀 있다.

    울트라 등급은 5시간 창이 없어서 응답에 그 칸이 안 온다. 그런데 계정을 갈아 끼우기
    전부터 떠 있던 세션은 앞 계정의 5시간 수치를 계속 들고 있고, 공유 캐시는 빠진 칸을
    앞 값으로 이어받는다 — 그래서 없는 창이 앞 계정의 85퍼센트로 보였다(2026-11-11
    둘째 기계 ogm3614). 응답만으로는 "이 계정엔 그 창이 없다" 와 "이번에 잠깐 빠졌다" 를
    못 가르므로 사람이 적어 둔 목록으로 가른다. 목록은 그 이름의 홈에서 번호를 읽어
    비교한다 — 모든 자리 홈을 훑지 않는다(상태줄이 그릴 때마다 부른다).
    """
    if not isinstance(uuid, str) or not uuid:
        return False
    try:
        with open(path or NO_FIVE_HOUR, encoding="utf-8") as fh:
            names = [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return False
    base = root or os.environ.get("CLAUDE_ACCOUNTS_ROOT") or ACCOUNTS_ROOT
    for name in names:
        try:
            with open(os.path.join(base, name, ".claude.json")) as fh:
                acc = json.load(fh).get("oauthAccount")
        except (OSError, ValueError):
            continue
        if isinstance(acc, dict) and acc.get("accountUuid") == uuid:
            return True
    return False


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


def account_quota_observation(cache=None, account=None, login_applied=None,
                              generation=None):
    """두 창을 함께 내놓는다: {status, cached_at, account, five_hour, seven_day}.

    바깥 status 가 fresh 가 아니면 안쪽 수치는 근거가 아니다. 소비자는 이 함수 하나만
    쓴다 — 각자 캐시를 파싱하지 않는다.

    왜 한 벌이어야 하나(2026-08-28 실사고). 같은 계산이 rate-limit-recovery ·
    rate-limit-guard.sh · rate-limit-prompt-guard.sh 에 세 벌 복제돼 있었고, 셋 다
    cached_at 을 안 봤다. 그 계산은 resets_at 이 지난 창만 0 으로 만드는데, 얼어붙은
    캐시의 7일 창은 리셋이 아직 미래라 얼어붙은 99% 가 그대로 살아남는다. 그래서
    한도가 실제로 풀린 뒤에도 세션이 계속 차단됐다 — 한 기계가 그렇게 6시간을
    갇혔고, 도구가 막히니 턴이 없고, 턴이 없으니 캐시가 갱신되지 않아 스스로
    못 빠져나오는 순환이었다. 하나만 고치면 나머지 둘이 그대로 막는다.

    낡은 캐시는 차단의 근거도 해제의 근거도 아니다. 차단 쪽은 fail-open 이라
    한 턴이 429 로 실패하면 그 턴이 캐시를 새로 채워 다음부터 정상 판정된다 —
    유계다. 해제 쪽은 fail-open 이 아니다(큐를 성급히 비우면 실제로 잃는다).
    큐가 있는데 관측이 fresh 가 아니면 창의 리셋 시각으로 가른다(windows_blocked) —
    사용률은 얼어붙어도 리셋 시각은 절대 시각이라 그대로 유효하다.

    남의 계정 수치도 근거가 아니다(other-account). 캐시에 적힌 계정과 지금 로그인된
    계정이 다르면 그 수치는 이 계정에 대해 아무것도 말하지 않는다 — 낡은 것보다
    나쁘다. 계정을 갈아끼우자마자 모든 가드가 열리므로, 사람이 bypass 를 손으로
    켜서 빠져나올 필요가 없다.

    로그인보다 먼저 적힌 수치도 근거가 아니다(relogin). 같은 계정으로 다시 로그인하면
    식별자가 그대로라 other-account 가 안 잡히는데, 그 수치는 로그인 이전 자격증명이
    받아온 값이다. 이걸 여기서 안 보면 해제는 5분짜리 복구 타이머 한 곳에만 걸린다 —
    2026-09-29 홈에서 12시 20분 로그인이 12시 23분에야 반영됐고, 그 사이 도구는 계속
    막혔다. 타이머가 죽어 있으면 창이 리셋될 때까지 영영 안 풀린다. 여기서 보면 다음
    도구 호출에서 바로 풀린다. 복구 타이머가 캐시를 비우고 세션이 한 턴을 돌면
    cached_at 이 로그인보다 나중이 되어 저절로 fresh 로 돌아온다.

    낡음 판정이 재로그인 판정보다 앞선다. 15분 넘게 얼어붙은 수치는 로그인이
    있었든 없었든 이미 근거가 아니고, 그걸 relogin 으로 바꿔 적으면 얼어붙었다는
    사실만 사라진다. relogin 은 그러지 않았으면 fresh 로 통했을 수치에만 붙는다.

    지금 계정이 무엇인지 자체가 안 서면 그 수치도 근거가 아니다(account-unverified).
    이름표는 있는데 그 자리의 자격이 지금 자격과 안 맞는 상태다 — 토큰이 저절로
    갱신되기만 해도 그렇게 된다(account_identity 참조). 그때 캐시에 적힌 계정과
    맞춰 볼 상대가 없으므로, 맞다고도 틀리다고도 못 한다. 모르는 수치를 차단의
    근거로 올리지 않는다. 차단 쪽은 fail-open 이라 실제로 막혀 있으면 그 턴이 429 로
    실패하며 캐시를 새로 채운다.

    교체 세대가 안 맞는 수치도 근거가 아니다(stale-gen). 계정 표가 맞고 방금 적혔어도,
    그 수치를 받아온 응답이 교체 전에 시작된 것일 수 있다 — 그때는 옛 계정의 수치에
    새 계정 표가 붙는다(account_generation 참조). 세대가 확인된 수치만 차단의 근거로
    쓴다. 교체를 한 번도 안 한 기계는 세대 파일이 없고, 그러면 이 판정 자체를 건너뛴다.
    맨 뒤에 두는 이유는 다른 판정을 가리지 않기 위해서다 — 이 판정은 그러지 않았으면
    fresh 로 통했을 수치만 끌어내린다.
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
        return {"status": status, "cached_at": None, "account": None,
                "five_hour": dict(empty), "seven_day": dict(empty)}
    now = time.time()
    ca = d.get("_cached_at")
    rl = d.get("rate_limits") or {}
    acct = d.get("_account")
    out = {"status": "fresh", "cached_at": ca,
           "account": acct if isinstance(acct, str) else None,
           "five_hour": _window_state(rl.get("five_hour") or {}, now),
           "seven_day": _window_state(rl.get("seven_day") or {}, now)}
    if account is None:
        source, cur = account_identity()
    else:
        source, cur = "config", account
    login_at = login_applied_at(login_applied)
    if out["account"] and isinstance(cur, str) and out["account"] != cur:
        out["status"] = "other-account"   # 지금 계정의 수치가 아니다
    elif not isinstance(ca, (int, float)) or now - ca > CACHE_FRESH_SEC:
        out["status"] = "stale"   # 세션이 턴을 안 돌고 있다 = 수치가 얼었다
    elif login_at is not None and login_at > ca and _has_used_number(out):
        out["status"] = "relogin"   # 로그인보다 먼저 적힌 수치다 = 지금 자격증명 것이 아니다
    elif source == "unverified" and _has_used_number(out):
        # 지금 어느 계정인지 자체가 안 선다 = 이 수치가 누구 것인지도 모른다.
        out["status"] = "account-unverified"
    else:
        gen = account_generation(generation)
        if gen is not None and d.get("_generation") != gen and _has_used_number(out):
            out["status"] = "stale-gen"   # 교체 뒤에 받아온 수치라는 확인이 없다
    return out


def _has_used_number(obs):
    """두 창 중 하나라도 실제 사용률 숫자를 들고 있나.

    relogin 은 "이 수치를 믿지 마라" 는 표시다. 그래서 믿을 수치가 있을 때만 붙인다.
    복구 장치는 로그인을 보면 캐시에서 rate_limits 를 들어내는데, 그때 relogin 이
    그대로 남으면 없는 수치를 계속 불신하느라 대기 큐가 안 풀린다 — 들어낸 쪽이
    해제 신호인데 그 신호를 이 표시가 덮는다.
    """
    return any(isinstance((obs.get(w) or {}).get("used"), (int, float))
               for w in ("five_hour", "seven_day"))


def quota_blocked(cache=None, block_5h=None, block_7d=99, account=None,
                  login_applied=None, generation=None):
    """(차단인가, 사유). fresh 관측이 임계를 넘을 때만 True.

    낡은 관측은 차단 근거가 아니다 — 그래야 얼어붙은 수치가 세션을 영구히 가두지
    못한다. 모르면 열어두고, 실제로 막혀 있으면 그 턴이 429 로 실패하며 캐시를
    새로 채운다."""
    obs = account_quota_observation(cache, account, login_applied, generation)
    if obs["status"] != "fresh":
        return False, "quota=%s(수치 안 믿음)" % obs["status"]
    lim = {"five_hour": QUOTA_BLOCK if block_5h is None else block_5h,
           "seven_day": block_7d}
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        w = obs[key]
        if w["status"] == "fresh" and w["used"] is not None and w["used"] >= lim[key]:
            return True, "%s %s%% (>= %s%%)" % (label, int(w["used"]), lim[key])
    return False, "quota=fresh 임계 아래"


def windows_blocked(cache=None, block_5h=None, account=None, login_applied=None,
                    generation=None):
    """(5시간 창이 아직 막혀 있나, 사유). 캐시가 낡았어도 그 창만 보고 답한다.

    quota_blocked 와 묻는 것이 다르다. 저쪽은 "지금 이 턴을 막을까" 라서 낡은 수치를
    근거로 쓰지 않는다. 여기는 "지금 깨워도 되나" 라 반대다 — 창의 리셋 시각은 절대
    시각이라 캐시가 낡아도 그대로 유효하고, 임계를 넘긴 창의 리셋이 아직 미래면
    깨워봐야 그 세션이 다시 막혀 큐로 되돌아온다.

    7일 창은 일부러 안 본다. 낡은 수치로 그 창을 근거 삼으면 최대 7일을 갇히는데,
    그게 2026-08-28 여섯 시간 락업의 모양이다(얼어붙은 99% + 리셋은 이틀 뒤).
    5시간 창은 리셋이 지나면 반드시 열리므로 잘못 보류해도 최대 다섯 시간이고, 그
    사이 모름 알림이 사람에게 한 번 간다. 7일 한도가 진짜로 찼으면 깨어난 세션이
    그 턴에 다시 막히고 되튕김 차단기가 두 번 만에 멈춘다.

    창 상태 fresh 는 "리셋이 아직 미래" 라는 뜻이고 expired 는 그 창이 리셋됐다는
    뜻이다(_window_state). 수치가 아예 없으면(invalid) 막혔다는 근거도 없으므로 False.
    다른 계정 수치면 이 계정에 대해 아무것도 말하지 않으므로 역시 False.

    교체 세대가 안 맞는 수치도 같다(stale-gen). 그 수치는 교체 전 응답의 것일 수 있어
    어느 계정 것인지가 안 선다 — 낡은 것과 달리 창의 리셋 시각조차 이 계정 것이라는
    보장이 없으므로 깨우기를 보류할 근거로 쓰지 않는다.
    """
    obs = account_quota_observation(cache, account, login_applied, generation)
    if obs["status"] == "other-account":
        return False, "quota=other-account(이 계정 수치 아님)"
    if obs["status"] == "stale-gen":
        return False, "quota=stale-gen(교체 뒤 수치라는 확인 없음)"
    w = obs["five_hour"]
    lim = QUOTA_BLOCK if block_5h is None else block_5h
    if w["status"] == "fresh" and w["used"] is not None and w["used"] >= lim:
        return True, "5h %s%% (>= %s%%, 리셋 전)" % (int(w["used"]), lim)
    return False, "5h 창이 임계 아래이거나 리셋됨"


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
