#!/bin/bash
# auto-verify: Write/Edit 직후 파일 syntax 빠르게 점검.
# 에러만 출력 (성공은 조용히). 검사기 없거나 파일 타입 미지원이면 그냥 통과.

FILE_PATH=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -z "$FILE_PATH" ] && exit 0
[ ! -f "$FILE_PATH" ] && exit 0

err=""
case "$FILE_PATH" in
  *.py)
    command -v python3 >/dev/null && err=$(python3 -m py_compile "$FILE_PATH" 2>&1)
    ;;
  *.sh|*.bash)
    err=$(bash -n "$FILE_PATH" 2>&1)
    ;;
  *.json)
    command -v jq >/dev/null && err=$(jq empty "$FILE_PATH" 2>&1)
    ;;
  *.yaml|*.yml)
    command -v python3 >/dev/null && err=$(python3 -c "import yaml,sys; yaml.safe_load(open('$FILE_PATH'))" 2>&1)
    ;;
  *.ts|*.tsx|*.js|*.jsx)
    if command -v node >/dev/null && [[ "$FILE_PATH" == *.js || "$FILE_PATH" == *.jsx ]]; then
      err=$(node --check "$FILE_PATH" 2>&1)
    fi
    ;;
esac

if [ -n "$err" ]; then
  echo "[auto-verify] $FILE_PATH syntax check failed:" >&2
  echo "$err" >&2
  exit 2
fi
exit 0
