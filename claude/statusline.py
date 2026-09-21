#!/usr/bin/env python3
"""Claude Code status line — model, context %, cost, rate limit, directory.

usage 추적: statusline 렌더 때마다 5h/7d/resets_at 등을
~/.claude/state/usage-history/YYYY-MM.jsonl 에 한 줄씩 누적.
중복 제거: 직전 record와 핵심 필드가 같으면 skip.
이상 탐지: 7d% 가 >=20pp 한 번에 떨어지면 컨트롤봇으로 알림.
"""
import json, os, sys, time, urllib.request, urllib.parse, datetime

CACHE_FILE = os.path.expanduser("~/.claude/statusline_cache.json")
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
        data = urllib.parse.urlencode({"chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""), "text": msg}).encode()
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
        same = prev and all(prev.get(k) == rec.get(k) for k in ("fh_pct","fh_reset","sd_pct","sd_reset"))
        if not same:
            with open(path, "a") as f:
                f.write(json.dumps(rec) + "\n")
        # usage-tracker 알림 비활성 (2026-05-13, 사용자 요청: 부정확한 알림 과다).
        # history jsonl 기록은 유지(통계용), 텔레그램 알림만 제거.
    except Exception:
        pass


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        return

    try:
        data["_cached_at"] = time.time()
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)
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
    ctx = data.get("context_window", {})
    pct = ctx.get("used_percentage")
    if pct is not None:
        parts.append(f"ctx:{pct}%")

    # Session cost
    cost = data.get("cost", {})
    usd = cost.get("total_cost_usd", 0)
    if usd and usd > 0:
        parts.append(f"${usd:.2f}")

    # Rate limits (5h / 7d)
    rls = data.get("rate_limits", {})
    rl5 = rls.get("five_hour", {}).get("used_percentage")
    rl7 = rls.get("seven_day", {}).get("used_percentage")
    if rl5 is not None and rl7 is not None:
        parts.append(f"rl:{int(round(rl5))}%/7d:{int(round(rl7))}%")
    elif rl5 is not None:
        parts.append(f"rl:{int(round(rl5))}%")

    # Directory basename
    cwd = data.get("cwd", "")
    if cwd:
        parts.append(os.path.basename(cwd))

    print(" | ".join(parts) if parts else "")

if __name__ == "__main__":
    main()
