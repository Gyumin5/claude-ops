#!/usr/bin/env python3
"""Claude Code status line — model, context %, cost, rate limit, directory.

usage 추적: statusline 렌더 때마다 5h/7d/resets_at 등을
~/.claude/state/usage-history/YYYY-MM.jsonl 에 한 줄씩 누적.
중복 제거: 직전 record와 핵심 필드가 같으면 skip.
이상 탐지: 7d% 가 >=20pp 한 번에 떨어지면 컨트롤봇으로 알림.
"""
import json, os, sys, time, urllib.request, urllib.parse, datetime

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "..", "lib"))
import claude_session_lib as csl   # noqa: E402  (계정 식별 단일 출처)
from operator_chat import chat_id   # noqa: E402  (방 번호 단일 출처)

CACHE_FILE = os.path.expanduser("~/.claude/statusline_cache.json")
SEEN_GEN_FILE = os.path.expanduser("~/.claude/state/statusline-generation.json")
SEEN_GEN_KEEP_SEC = 7 * 24 * 3600
HISTORY_DIR = os.path.expanduser("~/.claude/state/usage-history")
ANOMALY_FLAG = os.path.expanduser("~/.claude/state/usage-anomaly-alerted.flag")
CONTROL_BOT_ENV = os.path.expanduser("~/.claude/control-bot/.env")
ANOMALY_DROP_PP = 20
ANOMALY_COOLDOWN_SEC = 30 * 60


def _alert_telegram(msg):
    try:
        if not os.path.exists(CONTROL_BOT_ENV):
            return
        env = {}
        for line in open(CONTROL_BOT_ENV).read().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
        token = env.get("CONTROL_BOT_TOKEN")
        if not token:
            return
        if os.path.exists(ANOMALY_FLAG):
            age = time.time() - os.path.getmtime(ANOMALY_FLAG)
            if age < ANOMALY_COOLDOWN_SEC:
                return
        url = "https://api.telegram.org/bot{}/sendMessage".format(token)
        data = urllib.parse.urlencode({"chat_id": chat_id(), "text": msg}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=3).read()
        open(ANOMALY_FLAG, "w").close()
    except Exception:
        pass


def _track_usage(data):
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        rl = data.get("rate_limits", {}) or {}
        fh = (rl.get("five_hour", {}) or {})
        sd = (rl.get("seven_day", {}) or {})
        rec = {
            "ts": int(time.time()),
            # 어느 계정의 수치인가. 이 파일 하나에 전 세션이 쌓는데 계정은 갈아 끼우므로,
            # 안 적으면 나중에 읽는 쪽이 남의 계정 수치를 같은 선 위에 놓는다 — 그러면
            # 계정이 갈린 자리가 "사용량이 줄었다" 로 읽힌다(2026-11-07 둘째 기계 실측).
            "acct": data.get("_account"),
            "fh_pct": fh.get("used_percentage"),
            "fh_reset": fh.get("resets_at"),
            "sd_pct": sd.get("used_percentage"),
            "sd_reset": sd.get("resets_at"),
        }
        if rec["fh_pct"] is None and rec["sd_pct"] is None:
            return
        ym = datetime.datetime.now().strftime("%Y-%m")
        path = os.path.join(HISTORY_DIR, "{}.jsonl".format(ym))
        prev = None
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 4096))
                    tail = f.read().decode("utf-8", errors="ignore").splitlines()
                    if tail:
                        prev = json.loads(tail[-1])
            except Exception:
                prev = None
        same = prev and all(prev.get(k) == rec.get(k)
                            for k in ("acct","fh_pct","fh_reset","sd_pct","sd_reset"))
        if not same:
            with open(path, "a") as f:
                f.write(json.dumps(rec) + "\n")
        # usage-tracker 알림 비활성 (2026-05-13, 사용자 요청: 부정확한 알림 과다).
        # history jsonl 기록은 유지(통계용), 텔레그램 알림만 제거.
    except Exception:
        pass


def _expired(w, now):
    """그 창의 초기화 시각이 이미 지났나. 시각이 없거나 수가 아니면 판정하지 않는다."""
    rt = w.get("resets_at") if isinstance(w, dict) else None
    return isinstance(rt, (int, float)) and now > rt


def _merge_rate_limits(old, new):
    """앞서 저장된 한도 수치와 지금 세션이 든 수치를 합친다.

    한 창 안에서 사용량은 줄어들 수 없다 — 초기화될 때까지 쌓이기만 한다.
    그런데 캐시는 전 세션이 같이 쓰는 파일 하나이고, 세션은 자기가 마지막으로
    계정에서 받아온 값을 들고 있다가 상태줄을 그릴 때 그대로 쓴다. 오래 쉰 세션이
    한 번 깨어나면 몇십 분 전 값이 방금 잰 값처럼 저장된다. 실측으로 2026-09 한 달에
    같은 창에서 10퍼센트포인트 이상 거꾸로 간 자리가 36곳이었고(최대 09-21 17:40 의
    93퍼센트가 18:21 에 24퍼센트로), 창이 통째로 빠진 채 저장된 기록이 160건이었다.
    낮은 쪽을 읽으면 가드가 안 막고 복구 장치가 해제로 본다.

    그래서 낮은 값은 높은 값을 못 덮고, 빠진 창은 앞 값을 못 지운다. 창이 초기화되면
    초기화 시각이 바뀌므로 그때는 새 값을 그대로 받는다 — 계정을 바꿔도 초기화 시각이
    같이 갈리니 옛 계정 값에 갇히지 않는다(2026-09-20 락업).

    사용량 초기화권은 여기서 자동으로 못 가려낸다. 그 초기화는 주간 창 끝 시각을
    그대로 두고 수치만 내리므로 래칫이 매번 버린다. 그때는 사람이 초기화를 알려 주고
    `bin/claude-quota-cache-clear` 로 이 캐시의 한도 칸을 비운다 — 다음 렌더가 실제 수치를
    새로 넣는다.

    빠진 창을 이어받는 것은 초기화 시각이 아직 안 지난 창에만 한다. 그 조건이 없으면
    창이 아예 없는 계정에서 남의 값이 영원히 산다 — 울트라 등급에는 5시간 창이 없어서
    매 렌더가 "빠짐" 이고, 이어받기가 매번 옛 값을 되살려 초기화 시각을 지나도 안 지워졌다.
    새 값이 사전일 때만 도는 초기화 시각 비교(아래)는 그 경우에 닿지 못한다. 일시 누락
    대책은 그대로다 — 유효한 창은 여전히 이어받으므로 2026-09 의 누락 160건은 같이 보호된다.
    """
    if not isinstance(old, dict):
        return new
    now = time.time()
    out = dict(new) if isinstance(new, dict) else {}
    for key in ("five_hour", "seven_day"):
        o, n = old.get(key), out.get(key)
        if not isinstance(o, dict):
            continue
        if not isinstance(n, dict):
            if _expired(o, now):
                continue          # 지난 창은 보존하지 않는다 — 아래 설명
            out[key] = o
            continue
        if o.get("resets_at") != n.get("resets_at"):
            continue
        op, np_ = o.get("used_percentage"), n.get("used_percentage")
        if isinstance(op, (int, float)) and not (isinstance(np_, (int, float)) and np_ >= op):
            merged = dict(n)
            merged["used_percentage"] = op
            out[key] = merged
    return out


def _sub(obj, key):
    """중첩된 칸 하나를 사전으로 꺼낸다. 없거나 null 이면 빈 사전을 준다.

    하네스가 넘기는 칸은 빠지는 게 아니라 null 로 오는 경우가 있다. `.get(키, {})` 는
    그때 None 을 돌려주고 이어지는 `.get` 이 죽는다. 공유 캐시가 있을 때는 합치기가
    그 null 을 앞 값으로 채워 가려 왔고, 캐시가 없는 첫 회에만 상태줄이 통째로 죽었다.
    """
    val = obj.get(key) if isinstance(obj, dict) else None
    return val if isinstance(val, dict) else {}


def _generation_seen(session_id, gen):
    """이 세션이 이번 교체 세대에서 이미 한 번 그렸나. 그리고 그렸다고 적는다.

    계정을 갈아끼운 뒤 그 세션이 처음 그리는 한 번은, 교체 전에 시작된 응답의 수치를
    들고 있을 수 있다. 두 번째부터는 교체 뒤에 보낸 요청의 응답이므로 이 계정 것이
    맞다. 그래서 "이미 한 번 그렸나" 가 그 수치의 출처를 가르는 조건이 된다.

    기록은 세션마다 한 줄이고 일주일 안 보이면 지운다. 두 세션이 동시에 쓰면 한쪽
    기록이 덮일 수 있는데, 그 대가는 그 세션이 다음 렌더 한 번을 더 미확인으로
    보내는 것뿐이라 잠그지 않는다.
    """
    if not isinstance(session_id, str) or not session_id:
        return False
    now = time.time()
    try:
        with open(SEEN_GEN_FILE) as fh:
            seen = json.load(fh)
        if not isinstance(seen, dict):
            seen = {}
    except Exception:
        seen = {}
    row = seen.get(session_id)
    ok = isinstance(row, list) and len(row) == 2 and row[0] == gen
    seen = {k: v for k, v in seen.items()
            if isinstance(v, list) and len(v) == 2
            and isinstance(v[1], (int, float)) and now - v[1] <= SEEN_GEN_KEEP_SEC}
    seen[session_id] = [gen, now]
    try:
        os.makedirs(os.path.dirname(SEEN_GEN_FILE), exist_ok=True)
        tmp = "{}.tmp-{}".format(SEEN_GEN_FILE, os.getpid())
        with open(tmp, "w") as fh:
            json.dump(seen, fh)
        os.replace(tmp, SEEN_GEN_FILE)
    except Exception:
        pass
    return ok


def _write_cache(data):
    """공유 캐시에 남긴다. 덮어쓰지 않고 합치며, 갈아끼우기로 반쪽 파일을 안 남긴다.

    계정 표시(`_account`)를 같이 적는다. 이 파일 하나를 전 세션이 공유하는데 그동안
    누구 계정의 수치인지가 안 적혀 있었다 — 계정을 갈아끼워도 옛 수치가 그대로
    근거로 쓰였고, 가드는 그걸 신선한 값으로 읽어 전 세션을 가뒀다. 계정이 다르면
    합치지도 않는다. 남의 계정 수치는 이 계정의 하한이 아니다.

    계정 표만으로는 한 자리가 안 막힌다. 계정은 요청마다 다시 읽히는데 세션이 든
    수치는 마지막 응답의 것이라, 교체 직전에 받아 둔 수치에 교체 뒤의 계정 표가 붙는다
    (2026-11-09 홈: 옛 계정 88퍼센트가 그렇게 눌러앉아 전 세션을 막을 뻔했다. 실제
    계정은 14퍼센트였다). 그래서 교체 세대를 같이 본다 — 이 세션이 이번 세대에서 이미
    한 번 그렸을 때만 그 수치를 공유 칸에 넣고 세대 표(`_generation`)를 단다. 아니면
    앞 값을 그대로 두고 이번 수치는 `_unverified_rate_limits` 에 진단용으로만 남긴다.
    높은 값 유지 합치기도 같은 세대끼리만 한다 — 지난 세대 값이 이 세대의 하한이 되면
    고치려던 자리가 그대로 돌아온다.

    쓰는 것은 사본이다. 상태줄에 그리는 수치와 사용량 기록은 이 세션이 실제로 받은
    값이어야 하므로, 공유 칸을 지키려고 끌어온 앞 값으로 원본을 덮지 않는다."""
    acct = csl.current_account_id()
    data["_account"] = acct
    out = dict(data)
    try:
        with open(CACHE_FILE) as fh:
            old = json.load(fh)
    except Exception:
        old = None
    if not isinstance(old, dict):
        old = {}
    gen = csl.account_generation()
    if gen is None:
        # 계정을 한 번도 갈아끼운 적 없는 기계. 가를 세대가 없으니 예전 그대로 둔다.
        if old.get("_account") == acct:
            out["rate_limits"] = _merge_rate_limits(old.get("rate_limits"),
                                                    out.get("rate_limits"))
    elif _generation_seen(out.get("session_id"), gen):
        out["_generation"] = gen
        if old.get("_account") == acct and old.get("_generation") == gen:
            out["rate_limits"] = _merge_rate_limits(old.get("rate_limits"),
                                                    out.get("rate_limits"))
    else:
        unverified = out.pop("rate_limits", None)
        if unverified is not None:
            out["_unverified_rate_limits"] = unverified
        if old.get("rate_limits") is not None:
            out["rate_limits"] = old["rate_limits"]
        if old.get("_generation") is not None:
            out["_generation"] = old["_generation"]
    if csl.no_five_hour(acct):
        # 그 계정엔 5시간 창이 없다. 앞 계정 값을 이어받은 칸이 남아 있으면 지운다.
        for key in ("rate_limits", "_unverified_rate_limits"):
            if isinstance(out.get(key), dict):
                out[key] = {k: v for k, v in out[key].items() if k != "five_hour"}
    tmp = "{}.tmp-{}".format(CACHE_FILE, os.getpid())
    with open(tmp, "w") as fh:
        json.dump(out, fh)
    os.replace(tmp, CACHE_FILE)


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    try:
        data["_cached_at"] = time.time()
        _write_cache(data)
    except Exception:
        pass

    try:
        # 5시간 창이 없는 계정이면 세션이 앞 계정의 값을 들고 있어도 기록·표시에서 뺀다.
        if csl.no_five_hour(data.get("_account")) and isinstance(data.get("rate_limits"), dict):
            data["rate_limits"] = {k: v for k, v in data["rate_limits"].items()
                                   if k != "five_hour"}
    except Exception:
        pass

    _track_usage(data)

    parts = []

    # Model (short name)
    model = data.get("model", {})
    name = model.get("display_name", "") if isinstance(model, dict) else str(model)
    if name:
        parts.append(name)

    # Context usage %
    pct = _sub(data, "context_window").get("used_percentage")
    if pct is not None:
        parts.append(f"ctx:{pct}%")

    # Session cost
    usd = _sub(data, "cost").get("total_cost_usd", 0)
    if usd and usd > 0:
        parts.append(f"${usd:.2f}")

    # Rate limits (5h / 7d)
    rls = _sub(data, "rate_limits")
    rl5 = _sub(rls, "five_hour").get("used_percentage")
    rl7 = _sub(rls, "seven_day").get("used_percentage")
    if rl5 is not None and rl7 is not None:
        parts.append(f"rl:{int(round(rl5))}%/7d:{int(round(rl7))}%")
    elif rl5 is not None:
        parts.append(f"rl:{int(round(rl5))}%")
    elif rl7 is not None:
        parts.append(f"7d:{int(round(rl7))}%")

    # Directory basename
    cwd = data.get("cwd", "")
    if cwd:
        parts.append(os.path.basename(cwd))

    print(" | ".join(parts) if parts else "")

if __name__ == "__main__":
    main()
