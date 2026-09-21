#!/bin/bash
# Combined Bash hook: block dangerous commands, warn on main push, auto-approve the rest
COMMAND=$(jq -r '.tool_input.command')

DANGEROUS=false
REASON=""

# rm with recursive/force flags
if echo "$COMMAND" | grep -qE '\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+|.*-r\s.*-f|.*-f\s.*-r)'; then
  DANGEROUS=true
  REASON="재귀적 삭제 명령 (rm -rf)"
elif echo "$COMMAND" | grep -qE '(^|\s|/)(sudo)\s'; then
  DANGEROUS=true
  REASON="sudo 명령"
elif echo "$COMMAND" | grep -qE '\bchmod\s+(-[a-zA-Z]*\s+)*(777|0777|a\+rwx|a=rwx)'; then
  DANGEROUS=true
  REASON="chmod 777 / a+rwx"
elif echo "$COMMAND" | grep -qE '(^|\s|/)chown\s'; then
  DANGEROUS=true
  REASON="파일 소유권 변경 (chown)"
elif echo "$COMMAND" | grep -qE '\b(mkfs|dd\s+if=)'; then
  DANGEROUS=true
  REASON="디스크/디바이스 파괴 명령"
elif echo "$COMMAND" | grep -qE '>\s*/dev/sd'; then
  DANGEROUS=true
  REASON="디바이스 직접 쓰기"
elif echo "$COMMAND" | grep -qE 'git\s+push\s+.*(-f|--force)'; then
  DANGEROUS=true
  REASON="git force push"
elif echo "$COMMAND" | grep -qE 'git\s+reset\s+--hard'; then
  DANGEROUS=true
  REASON="git reset --hard"
elif echo "$COMMAND" | grep -qE 'git\s+clean\s+-[a-zA-Z]*f'; then
  DANGEROUS=true
  REASON="git clean -f"
elif echo "$COMMAND" | grep -qE '(curl|wget)\s.*\|\s*(bash|sh|zsh)'; then
  DANGEROUS=true
  REASON="원격 스크립트 실행 (curl|bash)"
# main/master push check
elif echo "$COMMAND" | grep -q 'git push'; then
  CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "")
  if [ "$CURRENT_BRANCH" = "main" ] || [ "$CURRENT_BRANCH" = "master" ] || \
     echo "$COMMAND" | grep -qE 'git push\s+\S+\s+.*(main|master)(\s|$)'; then
    DANGEROUS=true
    REASON="main/master 브랜치에 직접 push"
  fi
fi

if [ "$DANGEROUS" = true ]; then
  jq -n --arg reason "위험한 명령 감지 ($REASON): 실행 전 확인이 필요합니다" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "ask",
      permissionDecisionReason: $reason
    }
  }'
else
  # Auto-approve non-dangerous commands (overrides built-in safety prompts)
  jq -n '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "allow",
      permissionDecisionReason: "Auto-approved (not dangerous)"
    }
  }'
fi
