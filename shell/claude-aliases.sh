# Claude Code aliases
# cl: 터미널에서 Claude Code 세션 붙기. systemd 서비스가 있으면 자동으로 멈추고
#     종료 시 다시 살림. 없으면 그냥 일반 cl 동작.
cl() {
  # 텔레그램 페어링 자동 유지: 글로벌 access.json에 ID 주입
  local _tg_access="$HOME/.claude/channels/telegram/access.json"
  local _tg_id="${TELEGRAM_CHAT_ID:-}"
  if [ -f "$_tg_access" ]; then
    python3 -c "
import json, sys
p='$_tg_access'
try:
    d=json.load(open(p))
    if '$_tg_id' not in d.get('allowFrom',[]):
        d.setdefault('allowFrom',[]).append('$_tg_id')
        json.dump(d,open(p,'w'),indent=2)
except: pass
" 2>/dev/null
  fi

  # 환경변수는 export 로 현재 셸에 남기지 않는다 — cl 은 셸 함수라 export 가 함수 종료
  # 후에도 그 터미널에 그대로 남아, 다른 프로젝트로 cd 해서 뭔가 돌리면 stale 값이
  # 상속된다(ai-debate run-20260722T062557Z 지적). 아래 _envpfx 로 claude 프로세스에만
  # 실어보낸다.
  local _base_args="--remote-control"
  local _tg_state=""
  if [ -d ".claude/telegram" ]; then
    _base_args="$_base_args --channels plugin:telegram@claude-plugins-official"
    _tg_state="$(pwd)/.claude/telegram"
  fi

  local _pwd="$PWD"
  # systemd 서비스가 관리 중이면: failed 자동 복구 + stop → exit 시 auto-start
  # systemd unit 이름 규약상 언더스코어를 대시로 정규화
  local _svc="claude-$(basename "$PWD" | tr '_' '-')"
  # 발신 세션 신원 각인: systemd 유닛이 Environment 로 주던 CLAUDE_SESSION_NAME 을 cl 로
  # 띄울 때도 똑같이 넣는다. 없으면 claude-job-detach 가 발신세션을 못 찾아(env 없음 +
  # claude-*.service cgroup 밖) unknown 으로 떨어뜨리고, 완료보고가 원래 세션 주입 대신
  # 사람 직접알림으로 샌다 (2026-07-22 session-d session-m 잡 사고).
  #
  # 단 폴더이름만으로 각인하면 안 된다(ai-debate run-20260722T062557Z): /tmp/session-d 처럼
  # basename 만 같은 무관한 경로에서 cl 을 켜면 진짜 그 세션이 아닌데도 등록된 이름으로
  # 각인돼, 결과가 엉뚱한 세션에 "성공적으로" 주입된다 — 알림이 안 오는 것보다 나쁘다.
  # 그래서 해당 유닛이 실제로 존재하고 그 WorkingDirectory 가 지금 위치와 같은
  # 실경로일 때만 각인하고, 아니면 각인하지 않아 unknown → 사람 직접알림(발신폴더 표시)
  # 으로 안전하게 떨어뜨린다.
  # 각인 대상 유닛은 폴더 basename 추측이 아니라 WorkingDirectory 역조회로 찾는다.
  # (2026-07-27 ai-debate run-20260727T010915Z) basename 추측은 세션명과 폴더명이 다르면
  # 항상 실패한다 — $HOME/proj-b 의 실제 유닛은 claude-session-b, session_c_work
  # 는 claude-session_c 라 매번 각인 생략 경고가 떴고 사용자가 에러로 오인했다.
  # 판정 기준은 그대로 realpath 완전일치라 2026-07-22 에 세운 오배달 방지 속성은 유지된다.
  # 후보가 0개거나 2개 이상이면 fail-closed(각인 생략) — 복수 후보 자동 선택은 하지 않는다.
  #
  # 활성 유닛으로 한정하지 않는다(중재안에서 의도적으로 벗어난 부분): cl 은 바로 아래에서
  # 그 유닛을 stop 하고 자기가 대신 뜨는 도구라, 각인 시점에 유닛이 이미 inactive 인 정상
  # 경우가 있다. 각인은 '지금 그 유닛이 메시지를 받을 수 있나'가 아니라 '이 프로젝트의 세션
  # 신원이 무엇인가'를 정하는 것이고, 실제 수신자는 지금 뜨는 cl 세션 자신이다. 대신 보조·
  # 워치독 유닛이 섞이지 않게 ExecStart 가 claude-service-exec 인 것만 세션 유닛으로 본다
  # (claude-bun-ensure 63b3f06 의 positive gate 와 같은 기준).
  local _sess="" _pwd_real _cand="" _cand_n=0 _line
  local _u="" _wd="" _is_sess=0
  _pwd_real="$(readlink -f "$PWD")"
  local _unit_list
  _unit_list=$(systemctl --user list-unit-files 'claude-*.service' --no-pager --no-legend --plain 2>/dev/null \
               | awk '{print $1}' | grep -v '@')
  if [ -n "$_unit_list" ]; then
    # 유닛 1개당 show 를 부르지 않고 한 번에 조회한다(블록은 빈 줄로 구분, Id 가 블록 끝).
    while IFS= read -r _line; do
      case "$_line" in
        Id=*) _u="${_line#Id=}" ;;
        WorkingDirectory=*) _wd="${_line#WorkingDirectory=}" ;;
        ExecStart=*claude-service-exec*) _is_sess=1 ;;
        "")
          if [ "$_is_sess" = 1 ] && [ -n "$_wd" ] && [ -n "$_u" ] \
             && [ "$(readlink -f "$_wd")" = "$_pwd_real" ]; then
            _cand="$_u"; _cand_n=$((_cand_n + 1))
          fi
          _u=""; _wd=""; _is_sess=0 ;;
      esac
    done <<< "$(systemctl --user show $_unit_list -p Id -p WorkingDirectory -p ExecStart 2>/dev/null; echo)"
  fi

  if [ "$_cand_n" = 1 ]; then
    # 각인 직전 재검증 — 조회와 각인 사이에 유닛 정의가 바뀌었을 수 있다.
    local _recheck
    _recheck=$(systemctl --user show "$_cand" -p WorkingDirectory --value 2>/dev/null)
    if [ -n "$_recheck" ] && [ "$(readlink -f "$_recheck")" = "$_pwd_real" ]; then
      _sess="${_cand%.service}"; _sess="${_sess#claude-}"
    else
      _cand_n=0
    fi
  fi

  if [ -n "$_sess" ]; then
    # 아래 stop/auto-start 로직도 같은 유닛을 봐야 한다. basename 으로 추측한 이름을
    # 그대로 두면 cl 세션과 systemd 세션이 같은 cwd 에서 동시에 떠 봇 토큰 getUpdates
    # 409 경쟁이 난다(이 파일이 원래 막으려던 바로 그 사고).
    _svc="claude-$_sess"
  elif [ "$_cand_n" -gt 1 ]; then
    echo "[cl] 안내: 이 경로를 WorkingDirectory 로 쓰는 세션 유닛이 ${_cand_n}개라 세션명을 정할 수 없어 각인 생략. Claude 는 정상 실행됩니다 — detach 잡 결과만 세션 자동주입 대신 사람 직접알림으로 옵니다."
  else
    echo "[cl] 안내: 이 경로에 대응하는 세션 유닛이 없어 세션명 각인 생략. Claude 는 정상 실행됩니다 — detach 잡 결과만 세션 자동주입 대신 사람 직접알림으로 옵니다."
  fi
  if systemctl --user list-unit-files "$_svc.service" 2>/dev/null | grep -q "$_svc"; then
    # failed 상태면 카운터 리셋 (반복 실패로 멈춰있을 수 있음)
    if systemctl --user is-failed --quiet "$_svc" 2>/dev/null; then
      echo "[cl] $_svc failed 상태 감지 → reset-failed"
      systemctl --user reset-failed "$_svc"
    fi
    if systemctl --user is-active --quiet "$_svc" 2>/dev/null; then
      echo "[cl] systemd $_svc stop"
      if ! systemctl --user stop "$_svc"; then
        echo "[cl] stop 실패 → SIGKILL"
        systemctl --user kill -s KILL "$_svc" 2>/dev/null
      fi
    fi
  fi

  # 같은 경로에서 돌아가는 다른 터미널 cl 세션이 있으면 강제 종료 (봇 토큰 경쟁 방지)
  local _p _cwd
  for _p in $(pgrep -f "^claude.*--remote-control" 2>/dev/null); do
    _cwd=$(readlink "/proc/$_p/cwd" 2>/dev/null)
    if [ "$_cwd" = "$_pwd" ]; then
      echo "[cl] 기존 claude 세션 $_p 종료 (같은 경로)"
      kill -TERM "$_p" 2>/dev/null
    fi
  done
  # TERM이 먹었는지 2초 대기 후 살아있으면 KILL
  sleep 2
  for _p in $(pgrep -f "^claude.*--remote-control" 2>/dev/null); do
    _cwd=$(readlink "/proc/$_p/cwd" 2>/dev/null)
    if [ "$_cwd" = "$_pwd" ]; then
      echo "[cl] $_p SIGKILL"
      kill -KILL "$_p" 2>/dev/null
    fi
  done

  # 세션 시작 알림 (텔레그램 봇이 설정된 프로젝트만)
  if [ -f ".claude/telegram/.env" ] && command -v claude-service-notify >/dev/null 2>&1; then
    TELEGRAM_STATE_DIR="$_pwd/.claude/telegram" claude-service-notify start "cl:$(basename "$_pwd")" &
  fi

  # claude 프로세스에만 실어보낼 환경변수(현재 셸 오염 없음).
  # 각인할 값이 없을 때는 그냥 생략하면 안 되고 반드시 -u 로 지워야 한다 — 호출한
  # 터미널에 이미 CLAUDE_SESSION_NAME/TELEGRAM_STATE_DIR 이 떠 있으면(중첩 실행, 예전
  # 셸에서 물려받은 값 등) 자식이 그 stale 값을 그대로 상속해, 각인 가드를 통과하지
  # 못한 경우에도 남의 세션 이름을 달게 된다. -u 옵션은 할당보다 앞에 와야 한다.
  local _envpfx=(env)
  [ -z "$_sess" ] && _envpfx+=(-u CLAUDE_SESSION_NAME)
  [ -z "$_tg_state" ] && _envpfx+=(-u TELEGRAM_STATE_DIR)
  [ -n "$_sess" ] && _envpfx+=("CLAUDE_SESSION_NAME=$_sess")
  [ -n "$_tg_state" ] && _envpfx+=("TELEGRAM_STATE_DIR=$_tg_state")

  # -c로 이어가기 시도
  "${_envpfx[@]}" claude -c $_base_args 2>/dev/null
  local _ec=$?
  # exit 1 (이전 세션 없음)일 때만 새 세션으로 fallback.
  # 시그널로 죽은 경우(128+)는 그대로 종료해야 다른 cl에게 뺏긴 뒤 여기서 또 살아나는 사고 방지.
  if [ "$_ec" -eq 1 ]; then
    "${_envpfx[@]}" claude $_base_args
    _ec=$?
  fi

  # claude 종료 직후 systemd 서비스 재기동. (이전 trap EXIT 방식은 함수 종료가
  # 아니라 셸 종료 시에만 발동되는 버그. cl 함수 끝나는 시점에 즉시 처리.)
  # 시그널로 죽은 경우(128+)는 다른 cl이 가로챈 가능성 → 재기동 생략.
  if systemctl --user list-unit-files "$_svc.service" 2>/dev/null | grep -q "$_svc" \
     && [ "$_ec" -lt 128 ]; then
    local _other=0
    for _p in $(pgrep -f "^claude.*--remote-control" 2>/dev/null); do
      _cwd=$(readlink "/proc/$_p/cwd" 2>/dev/null)
      if [ "$_cwd" = "$_pwd" ]; then _other=1; break; fi
    done
    if [ "$_other" -eq 0 ]; then
      echo "[cl] systemd $_svc restart"
      systemctl --user reset-failed "$_svc" 2>/dev/null
      systemctl --user start "$_svc"
    else
      echo "[cl] 다른 cl 세션 살아있음 → service 재기동 생략"
    fi
  fi
}
