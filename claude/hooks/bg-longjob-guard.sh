#!/bin/bash
# bg-longjob-guard.sh: PreToolUse(Bash) 훅 — run_in_background=true 로 띄우면
# 세션 소프트 재시작(rate-limit-recovery·claude-bun-ensure·queue-flush)에
# 자식프로세스로 함께 죽어 결과가 유실되는 장시간 잡을, "실행계약이 확정된
# 대상만" 결정론적으로 차단(hard-block)한다.
#
# 정책 (ai-debate run-20260721T080318Z + run-20260828T145237Z 결론):
#  - ai-debate 직접 실행 → foreground·background 무관하게 차단. detach 전용이다.
#    (foreground 는 Bash 도구 상한 600초에 걸려 런이 통째로 유실되고, background 는
#     소프트 재시작에 조용히 죽는다. 대신 ai-debate-detach 를 foreground 로 부른다.)
#  - ai-debate-detach 를 run_in_background 로 실행 → 차단(즉시 반환하는 명령이다).
#  - manifest 에 명시 등록된 벤치 엔트리포인트를 run_in_background → 차단.
#    claude-job-detach 사용 요구.
#  - 미등록 임의 명령 → 명령 문자열만으로 장시간 여부 추론 불가 → 차단하지 않고
#    관측 로그만 남긴다(오탐 방지).
#  - auto-rewrite(자동 detach 치환) 안 함 — cwd/환경/입출력/종료코드/알림 계약이 바뀜.
#  - 셸 부분문자열이 아니라 "토큰화된 실행위치"로 프로그램을 판정
#    (grep/echo/cat 인자에 ai-debate 가 들어간 건 오탐 제외).
#  - fail-open: 입력 파싱·의존성 오류 시 일반 Bash 를 막지 않는다(exit 0).
#  - kill switch: env CLAUDE_BG_GUARD_OFF=1 또는 ~/.claude/state/bg-longjob-guard.off.
#
# 차단은 exit 2 + stderr(사유가 모델 컨텍스트로 전달됨). 경고(exit 0)는 모델에
# 실제 노출되는지 불확실하므로 확정대상은 차단, 그 외는 관측로그로만 처리.
set -uo pipefail

# --- kill switch (fail-open) ---
[ "${CLAUDE_BG_GUARD_OFF:-0}" = "1" ] && exit 0
[ -f "$HOME/.claude/state/bg-longjob-guard.off" ] && exit 0

command -v python3 >/dev/null 2>&1 || exit 0   # fail-open

export BG_GUARD_INPUT="$(cat 2>/dev/null || echo '{}')"
export BG_GUARD_MANIFEST="${CLAUDE_BG_GUARD_MANIFEST:-$HOME/.claude/state/bg-longjob-manifest.txt}"
export BG_GUARD_OBSLOG="$HOME/.claude/state/bg-longjob-guard.observed.log"

python3 - <<'PY'
import json, os, re, shlex, sys, datetime

# fail-open on any parse problem
try:
    d = json.loads(os.environ.get("BG_GUARD_INPUT", "{}") or "{}")
except Exception:
    sys.exit(0)

if (d.get("tool_name") or "") != "Bash":
    sys.exit(0)

ti = d.get("tool_input") or {}
bg = ti.get("run_in_background") is True

cmd = ti.get("command") or ""
if not cmd.strip():
    sys.exit(0)

# 명시 등록된 벤치 엔트리포인트(basename 단위, # 주석/빈줄 무시)
registered = set()
try:
    with open(os.environ.get("BG_GUARD_MANIFEST", "")) as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                registered.add(os.path.basename(line))
except Exception:
    pass  # manifest 없으면 벤치 차단 없음, ai-debate 규칙만 작동

# heredoc 본문은 명령이 아니라 데이터다. 줄 단위로 세그먼트를 자르기 때문에 본문에
# "ai-debate ..." 로 시작하는 줄이 있으면 실행위치로 오인한다(커밋 메시지·문서에서 실제
# 발생). 구분자를 만나면 종료줄까지 통째로 들어낸다.
def strip_heredocs(text):
    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", line)
        i += 1
        if not m:
            continue
        delim = m.group(2)
        while i < len(lines) and lines[i].strip() != delim:
            i += 1
        i += 1   # 종료줄도 버린다
    return "\n".join(out)


# 파이프라인/체인 세그먼트로 분리 후 각 세그먼트의 "실행위치 프로그램" 추출
WRAPPERS = {"env", "timeout", "nice", "nohup", "stdbuf", "ionice", "time",
            "sudo", "command", "exec", "setsid"}
segments = re.split(r"\|\||&&|[|;&\n]", strip_heredocs(cmd))

def tokens_of(seg):
    try:
        return shlex.split(seg, comments=False, posix=True)
    except Exception:
        return []

def exec_program(toks):
    # 래퍼/환경대입을 벗긴 "실행위치" 프로그램 basename (ai-debate 판정용, 엄격)
    i = 0
    while i < len(toks):
        t = toks[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):   # VAR=val 선행 대입
            i += 1; continue
        base = os.path.basename(t)
        if base in WRAPPERS:
            i += 1
            while i < len(toks) and (toks[i].startswith("-") or re.match(r"^[0-9]+[a-z]?$", toks[i])):
                i += 1
            continue
        return base
    return None

hit_aidebate = False
hit_wrapper = False
hit_bench = None
for seg in segments:
    toks = tokens_of(seg)
    if not toks:
        continue
    # ai-debate: 실행위치만(흔한 단어라 grep/echo 인자 오탐 방지)
    prog = exec_program(toks)
    if prog == "ai-debate":
        hit_aidebate = True
    elif prog == "ai-debate-detach":
        hit_wrapper = True
    # 등록 벤치: 사용자가 명시 등록한 정확한 엔트리포인트라 전체 토큰 매칭
    # (python3 run_bench.py 처럼 인터프리터로 실행되는 스크립트도 잡음)
    for t in toks:
        if os.path.basename(t) in registered:
            hit_bench = os.path.basename(t)

if not hit_aidebate and not (bg and (hit_wrapper or hit_bench)):
    sys.exit(0)   # 미등록/무해 → 차단 안 함

if hit_aidebate:
    # foreground 도 막는다. 세션 안에서 부르면 Bash 도구 상한 600초에 걸려 런이 통째로
    # 유실되고, run_in_background 는 소프트 재시작에 조용히 죽는다 — 둘 다 못 쓴다.
    # (ai-debate run-20260828T145237Z: 예외 없이 전량 detach.)
    msg = ("[bg-longjob-guard] ai-debate 를 세션에서 직접 실행하려 합니다. 이제 "
           "ai-debate 는 detach 전용입니다 — foreground 는 도구 상한 600초에 걸려 런이 "
           "통째로 유실되고, run_in_background 는 세션 소프트 재시작에 조용히 죽습니다. "
           "대신 `cat brief.md | ai-debate-detach \"<질문>\" [옵션...]` 을 foreground 로 "
           "부르세요. 즉시 반환하고, 결과 요약과 원문 경로가 끝난 뒤 이 세션에 주입됩니다.")
elif hit_wrapper:
    msg = ("[bg-longjob-guard] ai-debate-detach 를 run_in_background 로 실행하려 합니다. "
           "이 명령은 즉시 반환하므로 foreground 로 부르면 됩니다 — 백그라운드로 띄우면 "
           "제출 자체가 세션 재시작에 죽어 토론이 시작조차 안 될 수 있습니다.")
else:
    msg = (f"[bg-longjob-guard] 등록된 장시간 잡 '{hit_bench}' 를 run_in_background 로 "
           "실행하려 합니다. 세션 재시작에 죽습니다. claude-job-detach --slug <이름> "
           "[--notify-success] -- <명령...> 으로 세션 밖 독립 유닛에서 실행하세요.")

try:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with open(os.environ.get("BG_GUARD_OBSLOG", ""), "a") as f:
        label = "ai-debate" if hit_aidebate else ("ai-debate-detach" if hit_wrapper else hit_bench)
        f.write(f"{ts}\tBLOCK\t{label}\tbg={bg}\t{cmd[:300]}\n")
except Exception:
    pass

sys.stderr.write(msg + "\n")
sys.exit(2)
PY
rc=$?
[ "$rc" = "2" ] && exit 2
exit 0
