#!/bin/bash
# compact-flag-lib: 압축 표식의 경로를 세션별로 가른다. 훅 셋이 같이 쓴다
# (postcompact-mark · postcompact-warn · block-ai-after-compact).
#
# 왜 가르나: 표식이 기계에 하나뿐이면 한 세션의 압축이 다른 세션의 토론 제출을
# 막고, 그 다른 세션의 평범한 메시지 하나가 원래 경고를 받아야 할 세션의 표식을
# 지운다. 둘 다 조용히 일어난다.
#
# 표식을 둘로 나누는 이유: 경고는 다음 메시지 한 번에 소비되는 것이고(one-shot),
# 차단 창은 그 메시지와 상관없이 제 시간만큼 열려 있어야 한다. 한 파일로 두면
# 아무 메시지나 하나가 차단 창까지 같이 닫는다.
#
# 부르는 쪽(훅은 심링크라 자기 실체 옆에서 찾는다):
#   . "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/compact-flag-lib.sh"

COMPACT_FLAG_DIR="$HOME/.claude/state/compact-flags"

# 입력 JSON 에서 세션 식별자를 꺼낸다. 그 값이 파일 이름이 되므로 글자를 검사한다 —
# 경로 구분자가 섞인 값을 그대로 쓰면 표식이 엉뚱한 자리에 생기거나 안 생긴다.
# 못 꺼내면 rc 1 이고 아무것도 안 찍는다.
compact_flag_sid() {
    local sid
    sid=$(printf '%s' "${1:-}" | jq -r '.session_id // ""' 2>/dev/null) || return 1
    case "$sid" in
        "" | *[!A-Za-z0-9_-]*) return 1 ;;
    esac
    [ "${#sid}" -ge 8 ] && [ "${#sid}" -le 64 ] || return 1
    printf '%s' "$sid"
}

# <세션 식별자> <warn|block>
compact_flag_path() {
    printf '%s/%s.%s' "$COMPACT_FLAG_DIR" "$1" "$2"
}

# 세션을 모르면 전역 표식으로 조용히 내려가지 않는다 — 그러면 지금 고치는 동작이
# 그대로 돌아온다. 대신 그 사실을 남긴다. 이 파일이 비어 있는 것이 정상이다.
compact_flag_nosid() {
    local log="$HOME/.claude/logs/compact-flag-nosid.log"
    mkdir -p "$(dirname "$log")" 2>/dev/null || return 0
    printf '[%s] WARN %s 세션 식별자 없음 — 압축 표식을 세션별로 못 가른다\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "${1:-unknown}" >> "$log"
}

# 차단 표식은 AI 진입 명령을 실제로 시도해야 지워진다. 시도가 없으면 그 회차 것이
# 남으므로, 표식을 만들 때 하루 넘은 것을 같이 턴다.
compact_flag_prune() {
    find "$COMPACT_FLAG_DIR" -maxdepth 1 -type f -mmin +1440 -delete 2>/dev/null || true
}
