#!/bin/bash
# PostToolUse(Write|Edit): 방금 쓴 .html 에 charset 선언이 없으면 넣는다.
#
# 왜 훅인가: charset 이 없으면 브라우저가 인코딩을 추측하고, 그 추측은 머신마다
# 다르다. 같은 파일이 home 크롬에서는 멀쩡하고 회사 PC 에서는 한글이 깨졌다.
# 규칙(CLAUDE.md)만으로는 계속 샜다 — peer 에서 만든 179개 파일 전부 누락이었고,
# 그중 head 태그가 있는 파일은 0개였다(본문 조각만 생성되고 있었다).
# 판단이 필요한 값이 아니라 항상 같은 한 줄이라 결정론적으로 넣는 편이 맞다.
#
# 안 하는 것: 파일 바이트가 utf-8 이 아니면 손대지 않는다. 그건 생성 단계가
# 깨진 것이고 여기서 고치면 원인을 덮는다. 경고만 남긴다.
set -uo pipefail

FILE_PATH=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -z "$FILE_PATH" ] && exit 0
case "$FILE_PATH" in
    *.html|*.htm) ;;
    *) exit 0 ;;
esac
[ -f "$FILE_PATH" ] || exit 0

FILE_PATH="$FILE_PATH" python3 - <<'PY'
import os
import re
import sys

LOG = os.path.expanduser("~/.claude/logs/html-charset-inject.log")
META = b'<meta charset="utf-8">'
path = os.environ["FILE_PATH"]

try:
    raw = open(path, "rb").read()
except OSError:
    sys.exit(0)

try:
    raw.decode("utf-8")
except UnicodeDecodeError:
    # 바이트 자체가 깨진 경우. charset 을 붙이면 오히려 원인을 감춘다.
    print("[html-charset] %s: 파일이 utf-8 이 아니다 — charset 을 넣지 않았다. "
          "생성 단계를 확인하라." % path, file=sys.stderr)
    sys.exit(0)

# 앞 1024바이트 안에 선언이 있어야 브라우저가 재파싱하지 않는다.
# 뒤쪽에만 있으면 없는 것과 같게 취급해 앞에 하나 더 넣는다(먼저 나온 게 이긴다).
if b"charset" in raw[:1024].lower():
    sys.exit(0)

low = raw.lower()
m = re.search(rb"<head[^>]*>", low)
if m:
    pos, where = m.end(), "head 뒤"
else:
    m = re.search(rb"<html[^>]*>", low)
    if m:
        # head 가 없으면 파서가 만들어주고, 이 meta 는 거기로 올라간다.
        pos, where = m.end(), "html 뒤"
    else:
        # 조각 문서. doctype 도 html 도 head 도 없다 — peer 이 만들던 형태다.
        pos, where = 0, "맨 앞"

out = raw[:pos] + b"\n" + META if pos else META + b"\n"
out += raw[pos:]

tmp = path + ".charset.tmp"
try:
    with open(tmp, "wb") as fh:
        fh.write(out)
    os.replace(tmp, path)
except OSError as e:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    print("[html-charset] %s: 쓰기 실패 %s" % (path, e), file=sys.stderr)
    sys.exit(0)

print("[html-charset] %s 에 charset 을 넣었다(%s)." % (path, where), file=sys.stderr)
try:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write("%s\t%s\n" % (where, path))
except OSError:
    pass
PY
exit 0
