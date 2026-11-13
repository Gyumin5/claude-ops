#!/usr/bin/env python3
"""깨워도 또 막히는 구간에서 자동 깨우기를 멈추는 차단기를 본다.

간격 늘리기(10/20/40/60분)는 간격만 늘릴 뿐 60분마다 영원히 깨운다.

  1 세는 것은 "깨운 뒤 그 세션이 또 막혀 큐에 새 항목을 넣었다" 하나다. 마커 재적재도
    큐 서명 변화도 아니다 — 마커는 한 차단 구간에 하나뿐이고, 프롬프트 가드는 처리
    전에 큐를 비우므로 둘 다 재차단의 증거가 못 된다. 두 번 연속이면 멈추고 한 번 알린다.
    이 세기는 복구기가 큐를 직접 읽어서 한다(2026-10-31). 가드 훅이 적게 돼 있었지만
    깨우기 이름표가 다음 회차 재무장에 지워져 한 번도 안 세었다.
  2 깨울지는 창의 리셋 시각으로 가른다. 임계를 넘긴 창의 리셋이 아직 미래면 보류한다.
    캐시가 낡아도 리셋 시각은 절대 시각이라 유효하다(운영자 지시).
  3 깨울 차례인 프로젝트가 없으면 창 판정까지 가지 않는다.

정상으로 되돌리는 신호는 신선한 관측이 차단 아님을 말할 때, 큐 내용이 바뀌었을 때,
그리고 사람이 상태 파일을 지웠을 때뿐이다.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RECOVERY = REPO / "bin" / "rate-limit-recovery"
GUARD = REPO / "claude" / "hooks" / "rate-limit-guard.sh"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}"
          + (f"\n      {detail}" if not cond and detail else ""))


def run_funcs(names, body, env=None, queue_dir=None):
    """복구 장치에서 함수 몇 개만 떼어 내 돌린다."""
    src = "\n".join(
        f'eval "$(awk "/^{n}\\(\\)/,/^}}/" {RECOVERY})"' for n in names)
    script = (f'set -uo pipefail\nRECOVERY_BREAKER_LIMIT=2\n'
              f'QUEUE_DIR="{queue_dir or ""}"\n{src}\n{body}\n')
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          timeout=120, env=e)


def state(path, **fields):
    Path(path).write_text(json.dumps(fields), encoding="utf-8")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


with tempfile.TemporaryDirectory() as td:
    q = Path(td)
    sf = q / "dotfiles.recovery-state.json"

    # --- 차단기 판정 ---
    for count, want in ((0, "OPEN"), (1, "OPEN"), (2, "TRIPPED"), (5, "TRIPPED")):
        state(sf, signature="a", post_wake_block_count=count)
        got = run_funcs(["recovery_state_breaker"],
                        f'recovery_state_breaker "{sf}"').stdout.strip()
        check(f"재차단 {count}회는 {want}", got == want, got)
    sf.unlink()
    check("상태 파일이 없으면 열려 있다",
          run_funcs(["recovery_state_breaker"],
                    f'recovery_state_breaker "{sf}"').stdout.strip() == "OPEN")

    # --- 알림은 한 번만 ---
    state(sf, signature="a", post_wake_block_count=2)
    first = run_funcs(["recovery_breaker_mark"], f'recovery_breaker_mark "{sf}"')
    second = run_funcs(["recovery_breaker_mark"], f'recovery_breaker_mark "{sf}"')
    check("차단기를 처음 세울 때만 알림 차례가 온다",
          first.returncode == 0 and second.returncode == 1,
          f"{first.returncode} / {second.returncode}")
    check("차단기 표시가 파일에 남는다",
          load(sf).get("breaker") is True and load(sf).get("alerted_at"),
          json.dumps(load(sf), ensure_ascii=False))

    # --- 깨울 차례가 없으면 창 판정까지 가지 않는다 ---
    (q / "dotfiles.jsonl").write_text('{"id": "autoresume-dotfiles"}\n', encoding="utf-8")
    funcs = ["recovery_queue_signature", "recovery_state_due",
             "recovery_state_breaker", "recovery_any_due"]
    state(sf, signature="1:x", next_due_at=9999999999, post_wake_block_count=0)
    # 서명이 달라 DUE 가 되는 것을 막으려면 실제 서명을 넣어야 한다.
    sig = run_funcs(["recovery_queue_signature"],
                    f'recovery_queue_signature "{q}/dotfiles.jsonl"').stdout.strip()
    state(sf, signature=sig, next_due_at=9999999999, post_wake_block_count=0)
    r = run_funcs(funcs, "recovery_any_due", queue_dir=str(q))
    check("모든 프로젝트가 대기 중이면 깨울 차례가 아니다", r.returncode == 1)

    state(sf, signature=sig, next_due_at=0, post_wake_block_count=0)
    r = run_funcs(funcs, "recovery_any_due", queue_dir=str(q))
    check("차례가 된 프로젝트가 있으면 깨울 차례가 된다", r.returncode == 0)

    state(sf, signature=sig, next_due_at=0, post_wake_block_count=2)
    r = run_funcs(funcs, "recovery_any_due", queue_dir=str(q))
    check("차례가 됐어도 차단기가 섰으면 깨우지 않는다", r.returncode == 1)

    # --- 상태 기록: 차단기 칸 보존과 재무장 ---
    state(sf, signature=sig, attempts=1, post_wake_block_count=1,
          last_counted_wake_id="w1", wake_id="w1", wake_sent_at=100)
    run_funcs(["recovery_state_record"],
              f'recovery_state_record "{sf}" "{sig}" false ""')
    st = load(sf)
    check("큐 내용이 그대로면 재차단 수를 이어간다", st.get("post_wake_block_count") == 1,
          json.dumps(st, ensure_ascii=False))
    check("깨우기 실패면 앞 깨우기 이름표를 그대로 둔다", st.get("wake_id") == "w1")
    check("같은 큐가 반복되면 간격이 늘어난다", st["next_due_at"] > 0 and st["attempts"] == 2,
          json.dumps(st, ensure_ascii=False))

    run_funcs(["recovery_state_record"],
              f'recovery_state_record "{sf}" "2:다른서명" false ""')
    st = load(sf)
    check("큐 내용이 바뀌면 차단기가 풀린다",
          "post_wake_block_count" not in st and "breaker" not in st
          and st["attempts"] == 0 and st["next_due_at"] == 0,
          json.dumps(st, ensure_ascii=False))

    run_funcs(["recovery_state_record"],
              f'recovery_state_record "{sf}" "2:다른서명" true ""')
    st = load(sf)
    check("깨우기에 성공하면 새 이름표와 보낸 시각을 적는다",
          st.get("wake_id") and st.get("wake_sent_at"),
          json.dumps(st, ensure_ascii=False))
    wake_id = st["wake_id"]

    # 되튕김 세기는 가드 훅이 아니라 복구기가 한다(아래 전용 구간). 가드 쪽 쓰기는
    # 한 번도 센 적이 없어 지웠다 — 같은 칸을 두 곳에서 쓰면 어느 쪽이 셌는지 흐려진다.
    check("가드에 되튕김 쓰기 코드가 없다",
          'st["post_wake_block_count"]' not in GUARD.read_text(encoding="utf-8"),
          "가드에 옛 쓰기 코드가 남았다")

    # --- 재무장: 신선한 관측이 차단 아님을 말할 때 ---
    state(sf, signature=sig, attempts=3, next_due_at=9999999999,
          post_wake_block_count=2, breaker=True, alerted_at=1, wake_id="w9")
    run_funcs(["recovery_rearm_all"], 'recovery_rearm_all false', queue_dir=str(q))
    st = load(sf)
    check("재무장하면 차단기와 재차단 수가 사라진다",
          "breaker" not in st and "post_wake_block_count" not in st
          and "wake_id" not in st, json.dumps(st, ensure_ascii=False))
    check("차단에서 넘어온 것이 아니면 재시도 간격은 그대로 둔다",
          st["attempts"] == 3 and st["next_due_at"] == 9999999999,
          json.dumps(st, ensure_ascii=False))

    state(sf, signature=sig, attempts=3, next_due_at=9999999999, breaker=True)
    run_funcs(["recovery_rearm_all"], 'recovery_rearm_all true', queue_dir=str(q))
    st = load(sf)
    check("차단에서 해제로 넘어온 그 한 번은 바로 다음 차례가 된다",
          st["attempts"] == 0 and st["next_due_at"] == 0,
          json.dumps(st, ensure_ascii=False))

    # 아무것도 안 걸린 상태에서는 파일을 건드리지 않는다. 매 tick 쓰기를 만들면 안 된다.
    state(sf, signature=sig, attempts=3, next_due_at=9999999999)
    before = Path(sf).stat().st_mtime_ns
    run_funcs(["recovery_rearm_all"], 'recovery_rearm_all false', queue_dir=str(q))
    check("풀 것이 없으면 상태 파일을 안 건드린다",
          Path(sf).stat().st_mtime_ns == before and load(sf)["attempts"] == 3,
          json.dumps(load(sf), ensure_ascii=False))

# --- 깨울지는 창의 리셋 시각으로 가른다 ---
for label, env, want in (
        ("임계를 넘긴 창의 리셋이 아직 미래면 보류한다", "true", 0),
        ("리셋이 지났으면 깨우기로 넘어간다", "false", 1),
        ("판정값이 아예 없으면 막혔다고 보지 않는다", "", 1)):
    r = run_funcs(["still_blocked_by_window"],
                  f'QUOTA_WINDOWS_BLOCKED="{env}"\n'
                  'still_blocked_by_window; echo "rc=$?"')
    check(label, f"rc={want}" in r.stdout, r.stdout + r.stderr)

RECOVERY_SRC = RECOVERY.read_text(encoding="utf-8")
check("찔러보기 경로가 복구 장치에 안 남아 있다",
      "probe_release" not in RECOVERY_SRC and "PROBE_BACKOFF" not in RECOVERY_SRC,
      "지운 경로의 흔적이 남았다")
check("깨우기 조건이 창 판정 한 곳에서만 온다",
      RECOVERY_SRC.count("still_blocked_by_window") == 2,
      f"호출/정의 합 {RECOVERY_SRC.count('still_blocked_by_window')}")

# --- 되튕김 세기: 깨운 뒤 새로 들어온 한도 막힘을 한 번만 센다 ---
with tempfile.TemporaryDirectory() as td:
    q = Path(td)
    sf = q / "bias.recovery-state.json"
    qf = q / "bias.jsonl"
    sent = 1793300000.0

    def rec(ts, cause="rate_limit_blocked", ts_unix=None):
        d = {"ts": ts, "cause": cause, "id": "autoresume-bias"}
        if ts_unix is not None:
            d["ts_unix"] = ts_unix
        return json.dumps(d)

    def run_count(**extra):
        fields = dict(signature="s", wake_id="w1", wake_sent_at=sent)
        fields.update(extra)
        state(sf, **fields)
        return run_funcs(["recovery_count_rebound"],
                         f'recovery_count_rebound "{qf}" "{sf}"')

    before = "2026-10-29T12:00:00+09:00"      # sent 보다 앞
    after = "2026-10-30T12:00:00+09:00"       # sent 보다 뒤

    qf.write_text(rec(after) + "\n", encoding="utf-8")
    r = run_count()
    check("깨운 뒤 들어온 한도 막힘을 센다", r.stdout.strip() == "1", r.stdout + r.stderr)
    check("센 깨우기에 이름표를 남긴다", load(sf).get("last_counted_wake_id") == "w1",
          json.dumps(load(sf), ensure_ascii=False))

    r = run_funcs(["recovery_count_rebound"],
                  f'recovery_count_rebound "{qf}" "{sf}"')
    check("같은 깨우기는 두 번 세지 않는다",
          r.stdout.strip() == "" and load(sf)["post_wake_block_count"] == 1,
          r.stdout + json.dumps(load(sf), ensure_ascii=False))

    qf.write_text(rec(before) + "\n", encoding="utf-8")
    r = run_count()
    check("깨우기 전에 들어온 항목은 안 센다",
          r.stdout.strip() == "" and "post_wake_block_count" not in load(sf),
          r.stdout + json.dumps(load(sf), ensure_ascii=False))

    qf.write_text(rec(after, cause="user_message") + "\n", encoding="utf-8")
    r = run_count()
    check("한도 막힘이 아닌 항목은 안 센다", r.stdout.strip() == "",
          r.stdout + json.dumps(load(sf), ensure_ascii=False))

    qf.write_text(rec(after) + "\n", encoding="utf-8")
    r = run_count(wake_id=None, wake_sent_at=None)
    check("깨운 적이 없으면 세지 않는다", r.stdout.strip() == "",
          r.stdout + json.dumps(load(sf), ensure_ascii=False))

    qf.write_text("깨진 줄\n" + rec(after) + "\n", encoding="utf-8")
    r = run_count()
    check("깨진 줄이 섞여도 뒤 항목을 센다", r.stdout.strip() == "1",
          r.stdout + r.stderr)

    # 글자 시각의 형식은 쓰는 쪽마다 갈린다 — 가드 훅은 "+09:00", 이 스크립트의 합성
    # 레코드는 "+0900" 이고 파이썬 3.8 은 뒤쪽을 못 읽는다. 둘 다 적는 ts_unix 를 먼저
    # 보므로 형식이 갈려도 세기가 꺼지지 않는다.
    qf.write_text(rec("2026-10-30T12:00:00+0900", ts_unix=sent + 60) + "\n",
                  encoding="utf-8")
    r = run_count()
    check("글자 시각을 못 읽어도 ts_unix 로 센다", r.stdout.strip() == "1",
          r.stdout + r.stderr)

    qf.write_text(rec("2026-10-29T12:00:00+0900", ts_unix=sent - 60) + "\n",
                  encoding="utf-8")
    r = run_count()
    check("ts_unix 가 깨우기보다 앞서면 안 센다", r.stdout.strip() == "",
          r.stdout + json.dumps(load(sf), ensure_ascii=False))

    # 두 번째 되튕김에서 차단기가 선다 — 세기와 멈춤이 이어지는지 본다.
    qf.write_text(rec(after) + "\n", encoding="utf-8")
    state(sf, signature="s", wake_id="w2", wake_sent_at=sent,
          post_wake_block_count=1, last_counted_wake_id="w1")
    run_funcs(["recovery_count_rebound"], f'recovery_count_rebound "{qf}" "{sf}"')
    got = run_funcs(["recovery_state_breaker"],
                    f'recovery_state_breaker "{sf}"').stdout.strip()
    check("두 번째 되튕김에서 차단기가 선다", got == "TRIPPED",
          got + " " + json.dumps(load(sf), ensure_ascii=False))

# --- 차단기 만료: 창이 리셋될 만큼 지나면 스스로 풀린다 ---
# 풀어 주지 않으면 영구 잠김이다. 푸는 조건이 신선한 관측 하나뿐인데, 세션들이 다 막혀
# 멈추면 캐시가 안 갱신돼 그 관측이 영영 안 온다 — 2026-10-31 에 낡은 관측 경로에서
# 끊은 그 순환이 차단기 경로에만 남아 있었다.
TTL = 18000
with tempfile.TemporaryDirectory() as td:
    q = Path(td)
    sf = q / "bias.recovery-state.json"

    def run_expire(**fields):
        state(sf, **fields)
        return run_funcs(["recovery_breaker_expire"],
                         f'recovery_breaker_expire "{sf}" "{TTL}"; echo "rc=$?"')

    old = 1793300000.0
    now_ish = __import__("time").time()

    r = run_expire(signature="s", post_wake_block_count=2, wake_id="w2",
                   last_counted_wake_id="w2", alerted_at=old,
                   last_post_wake_block_at=now_ish - TTL - 60, attempts=3,
                   next_due_at=old)
    st = load(sf)
    check("5시간이 지나면 차단기를 푼다", "rc=0" in r.stdout, r.stdout + r.stderr)
    check("푼 뒤에는 차단기가 열려 있다",
          run_funcs(["recovery_state_breaker"],
                    f'recovery_state_breaker "{sf}"').stdout.strip() == "OPEN",
          json.dumps(st, ensure_ascii=False))
    check("풀 때 깨우기 이름표와 알림 표시도 같이 지운다",
          not any(k in st for k in ("wake_id", "last_counted_wake_id", "alerted_at")),
          json.dumps(st, ensure_ascii=False))
    check("풀어도 간격 늘리기는 그대로 둔다",
          st.get("attempts") == 3 and st.get("next_due_at") == old,
          json.dumps(st, ensure_ascii=False))

    r = run_expire(signature="s", post_wake_block_count=2,
                   last_post_wake_block_at=now_ish - 60)
    check("5시간이 안 지났으면 안 푼다",
          "rc=1" in r.stdout and load(sf).get("post_wake_block_count") == 2,
          r.stdout + json.dumps(load(sf), ensure_ascii=False))

    r = run_expire(signature="s", post_wake_block_count=2)
    check("되튕김 시각이 없으면 안 푼다 — 사람이 지우는 경로를 남긴다",
          "rc=1" in r.stdout and load(sf).get("post_wake_block_count") == 2,
          r.stdout + json.dumps(load(sf), ensure_ascii=False))

    sf.unlink()
    r = run_funcs(["recovery_breaker_expire"],
                  f'recovery_breaker_expire "{sf}" "{TTL}"; echo "rc=$?"')
    check("상태 파일이 없으면 조용히 넘어간다", "rc=1" in r.stdout, r.stdout + r.stderr)

    # 쓸어 담는 쪽은 차단기가 선 것만 건드린다.
    open_sf = q / "velocity.recovery-state.json"
    state(open_sf, signature="s", post_wake_block_count=1,
          last_post_wake_block_at=now_ish - TTL - 60)
    state(q / "bias.recovery-state.json", signature="s", post_wake_block_count=2,
          last_post_wake_block_at=now_ish - TTL - 60)
    r = run_funcs(["recovery_state_breaker", "recovery_breaker_expire",
                   "recovery_expire_breakers"],
                  'log_line() { :; }\n'
                  f'RECOVERY_BREAKER_EXPIRY={TTL}\nRECOVERY_LOG=/dev/null\n'
                  'recovery_expire_breakers; echo "rc=$?"',
                  queue_dir=str(q))
    check("쓸어 담기는 차단기가 선 것만 푼다",
          load(q / "bias.recovery-state.json").get("post_wake_block_count") is None
          and load(open_sf).get("post_wake_block_count") == 1,
          r.stdout + r.stderr)

check("창 판정이 차단기 만료보다 먼저 온다",
      RECOVERY_SRC.index("still_blocked_by_window; then")
      < RECOVERY_SRC.index("recovery_expire_breakers\n"),
      "아직 막힌 구간에서 차단기를 풀면 되튕김 고리가 되돌아온다")
check("차단기 만료가 깨울 차례 판정보다 먼저 온다",
      RECOVERY_SRC.index("recovery_expire_breakers\n")
      < RECOVERY_SRC.rindex("recovery_any_due || exit 0"),
      "순서가 뒤집히면 푼 그 회차를 통째로 건너뛴다")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
