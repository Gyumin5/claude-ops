#!/bin/bash
# dotfiles installer
# Usage:
#   Local:  ~/dotfiles/install.sh
#   Remote: CLAUDE_OPS_REPO=<저장소 주소> bash install.sh
set -euo pipefail

# 개별 링크·복사 실패가 스크립트 전체를 죽이지 않게 한다. install.sh 는 set -e 라
# 중간에 한 줄이 실패하면 뒷부분(유닛 동기화·드롭인·todo 배포·세션 enable)이 전부
# 유실된다 — 2026-07-27 에 스킬 블록과 유닛 블록에서 연달아 이 사고가 났다.
# 배포 단계 실패는 집계한다. warn-and-continue 로 "계속 진행"은 하되, 실패가 하나라도
# 있으면 끝에서 degraded 로 종결하고 rc 를 비제로로 낸다 — 그러지 않으면 부분 실패가
# 다시 성공처럼 보인다(2026-07-27 ai-debate run-20260727T094804Z 의 핵심 지적).
INSTALL_FAILURES=0
INSTALL_FAILED_STEPS=""
INSTALL_MANIFEST=""      # 이번 실행이 실제로 배포한 repo 상대경로 목록(단일 출처)
# 심링크로 배포한 것과 복사로 배포한 것은 감시 의미가 다르다. 심링크는 repo 를 고치는
# 순간 배포본도 같이 바뀌므로 install.sh 재실행이 필요 없다 — 이걸 구분하지 않아서
# claude/CLAUDE.md 한 줄 고칠 때마다 "install-stale" 오알림이 났다(2026-07-27).
# 재실행이 실제로 필요한 것은 copy 대상과 install.sh 자신뿐이다.
INSTALL_MANIFEST_COPY=""
warn()      { echo "[install] warn: $*"; }
fail_step() { INSTALL_FAILURES=$((INSTALL_FAILURES + 1)); INSTALL_FAILED_STEPS="${INSTALL_FAILED_STEPS}${INSTALL_FAILED_STEPS:+;}$1"; echo "[install] 실패: $1"; }
_manifest_add() {   # $1=원본 경로. repo 안이면 상대경로로 기록.
  case "$1" in
    "${DOTFILES_DIR:-/nonexistent}"/*) INSTALL_MANIFEST="${INSTALL_MANIFEST}${1#${DOTFILES_DIR}/}
";;
  esac
}
link() { if ln -sfn "$1" "$2"; then _manifest_add "$1"; else fail_step "link $2"; fi; }

# 배포본이 repo 본보다 새로우면 "누군가 배포 위치를 직접 고치고 repo 에 역반영을 안 한"
# 상태다. 2026-07-27 하루에 세 번 나왔다(guardian.txt·daily_worklog.txt·claude-new-loc.service)
# — 세 번 다 사람이 diff 로 우연히 잡았다. repo 가 source of truth 라는 계약은 유지하되
# (스킵하면 "install.sh 가 조용히 아무것도 안 한다"는 오늘의 그 실패로 되돌아간다),
# 덮기 전에 사본을 남기고 큰 소리로 경고해서 복구 가능하게 한다.
INSTALL_OVERWRITE_DIR="$HOME/.claude/state/install-overwrites"
_backup_if_newer() {   # $1=repo 원본 $2=배포 대상
    [ -f "$2" ] || return 0
    [ -L "$2" ] && return 0
    cmp -s "$1" "$2" && return 0
    local st dt
    st=$(stat -c %Y "$1" 2>/dev/null || echo 0)
    dt=$(stat -c %Y "$2" 2>/dev/null || echo 0)
    [ "$dt" -le "$st" ] && return 0
    local d="$INSTALL_OVERWRITE_DIR/$(date +%Y%m%dT%H%M%S)"
    mkdir -p "$d" 2>/dev/null || return 0
    cp -a "$2" "$d/$(basename "$2")" 2>/dev/null || return 0
    warn "배포본이 repo 보다 새로움 — 덮기 전 백업: $2 → $d/$(basename "$2")"
    warn "  (repo 에 역반영이 안 된 직접 수정일 수 있다. 확인 후 repo 를 갱신할지 판단할 것)"
}
copy() {
    _backup_if_newer "$1" "$2"
    if cp -f  "$1" "$2"; then
        _manifest_add "$1"
        case "$1" in
          "${DOTFILES_DIR:-/nonexistent}"/*) INSTALL_MANIFEST_COPY="${INSTALL_MANIFEST_COPY}${1#${DOTFILES_DIR}/}
";;
        esac
    else fail_step "copy $2"; fi
}

# 완주/중단/부분실패 마커. 진짜 교훈은 "install.sh 가 죽었다"가 아니라 "죽은 걸 3주 동안
# 아무도 몰랐다" 였다(2026-07-27). rc 를 아무도 안 보고, 앞부분 출력이 성공처럼 보였다.
# 상태는 셋 중 하나로 원자적(임시파일 후 rename)으로 남긴다: ok / degraded / aborted.
INSTALL_STATE_DIR="$HOME/.claude/state"
INSTALL_OK="$INSTALL_STATE_DIR/install-last-ok"
INSTALL_BAD="$INSTALL_STATE_DIR/install-last-fail"
INSTALL_SCHEMA=2   # 2: manifest_copy 추가(심링크/복사 구분). 감시자는 1·2 둘 다 읽는다.

_atomic_write() {   # $1=대상 파일. 내용은 stdin.
  local t="$1.tmp.$$"
  mkdir -p "$(dirname "$1")" 2>/dev/null || true
  if cat > "$t" 2>/dev/null; then mv -f "$t" "$1" 2>/dev/null || rm -f "$t" 2>/dev/null; fi
}
_install_head() { git -C "${DOTFILES_DIR:-$HOME/dotfiles}" rev-parse --short HEAD 2>/dev/null || echo unknown; }
_install_mid()  { tr -d '[:space:]' < "$HOME/.config/claude-machine-id" 2>/dev/null || echo unknown; }

# 상태 기록 시 install-last-ok 는 절대 전진시키지 않는다(마지막으로 성공한 지점 보존).
_write_bad() {   # $1=kind(aborted|degraded) $2=rc $3=line(또는 -)
  python3 - "$INSTALL_BAD" "$1" "$2" "$3" "$(_install_head)" "$(_install_mid)" \
           "$INSTALL_FAILURES" "$INSTALL_FAILED_STEPS" "$INSTALL_SCHEMA" <<'PY' 2>/dev/null || true
import json, os, sys, time
path, kind, rc, line, head, mid, nfail, steps, schema = sys.argv[1:10]
rec = {"schema": int(schema), "state": kind, "rc": int(rc), "line": (None if line == "-" else line),
       "host": os.uname().nodename, "machine_id": mid, "head": head,
       "at": time.strftime("%Y-%m-%d %H:%M:%S KST"),
       "failure_count": int(nfail), "failed_steps": [s for s in steps.split(";") if s]}
tmp = path + ".tmp.%d" % os.getpid()
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(tmp, "w") as f: json.dump(rec, f, ensure_ascii=False, indent=1)
os.replace(tmp, path)
PY
}

_install_abort() {
  local rc=$? line="${1:-?}"
  _write_bad aborted "$rc" "$line"
  echo "[install] 중단: ${line}행에서 exit=$rc — 이 아래 단계는 실행되지 않았다."
  echo "[install]   기록: $INSTALL_BAD (state=aborted)"
}
trap '_install_abort $LINENO' ERR

# 받아올 저장소 주소. 이 스크립트가 이미 받아 둔 저장소 안에서 돌면 안 쓴다 —
# 아무것도 없는 기계에 처음 심을 때만 쓰므로, 자기 저장소 주소를 환경변수로 넘긴다.
REPO="${CLAUDE_OPS_REPO:-}"
DOTFILES_DIR="$HOME/dotfiles"

# Resolve real path of this script (handles symlinks, works on macOS+Linux)
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SOURCE="${BASH_SOURCE[0]}"
  while [ -L "$SOURCE" ]; do
    DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
  done
  SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
fi

# Detect if run from local clone or via curl|bash
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/claude/settings.json" ]; then
  DOTFILES_DIR="$SCRIPT_DIR"
else
  echo "Cloning dotfiles..."
  if [ -d "$DOTFILES_DIR" ]; then
    echo "Updating existing dotfiles..."
    git -C "$DOTFILES_DIR" pull --ff-only
  elif [ -n "$REPO" ]; then
    git clone "$REPO" "$DOTFILES_DIR"
  else
    echo "받아올 주소를 모른다. CLAUDE_OPS_REPO 에 저장소 주소를 넣고 다시 돌리거나,"
    echo "저장소를 $DOTFILES_DIR 에 받은 뒤 그 안의 install.sh 를 돌려라."
    exit 1
  fi
fi

# Check dependencies
for cmd in jq git; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "WARNING: '$cmd' is not installed. Some features may not work."
  fi
done

mkdir -p ~/.claude/hooks ~/.local/bin

# Claude global config
link "$DOTFILES_DIR/claude/CLAUDE.md" ~/.claude/CLAUDE.md
# 규칙 근거 파일(claude/RATIONALE.md)은 연결하지 않는다. ~/.claude/rules/ 밑 마크다운은
# 클로드 코드가 매 세션·매 압축 뒤 자동으로 싣는다 — 거기 두면 근거 문서가 늘 맥락에 남아
# 압축이 반복된다. 예전 연결이 남아 있으면 걷어 낸다.
if [ -L ~/.claude/rules ] && [ "$(readlink ~/.claude/rules)" = "$DOTFILES_DIR/claude/rules" ]; then
  rm -f ~/.claude/rules
fi
# Claude settings.json — fail-closed: 실파일이 dotfiles 와 다르면 덮어쓰지 않고 중단
_SETTINGS_TARGET="$HOME/.claude/settings.json"
_SETTINGS_SRC="$DOTFILES_DIR/claude/settings.json"
if [ -L "$_SETTINGS_TARGET" ]; then
  link "$_SETTINGS_SRC" "$_SETTINGS_TARGET"
elif [ -e "$_SETTINGS_TARGET" ]; then
  if cmp -s "$_SETTINGS_TARGET" "$_SETTINGS_SRC"; then
    link "$_SETTINGS_SRC" "$_SETTINGS_TARGET"
  else
    _SETTINGS_BK="$_SETTINGS_TARGET.install-backup.$(date +%Y%m%d-%H%M%S)"
    cp -a "$_SETTINGS_TARGET" "$_SETTINGS_BK"
    echo "ERROR: $_SETTINGS_TARGET is a real file differing from $_SETTINGS_SRC." >&2
    echo "       Backed up to $_SETTINGS_BK. Refusing to overwrite; merge manually then re-run." >&2
    exit 1
  fi
else
  link "$_SETTINGS_SRC" "$_SETTINGS_TARGET"
fi
link "$DOTFILES_DIR/claude/mcp.json" ~/.claude/mcp.json

# Claude hooks (clean stale symlinks, then link current hooks)
find ~/.claude/hooks/ -maxdepth 1 -type l ! -exec test -e {} \; -delete 2>/dev/null
for hook in "$DOTFILES_DIR"/claude/hooks/*.sh; do
  link "$hook" ~/.claude/hooks/"$(basename "$hook")"
done

# Claude skills
# 주의: 이 블록은 install.sh 전체를 죽인 이력이 있다(2026-07-27 한 기계에서 보고). ~/.claude/skills/<name>
# 이 이미 repo 디렉토리를 가리키는 심링크면 ln 의 대상이 원본 자신으로 해석돼
# "are the same file" 로 실패하고, set -e 가 스크립트를 여기서 종료시켰다. 그 뒤의
# systemd 유닛 동기화·드롭인·todo-sync 배포가 전부 안 돌아 07-03 이후 사실상 무동작이었다.
# 그래서 (1) 디렉토리 심링크면 스킵, (2) 개별 ln 실패는 경고만 하고 계속.
if [ -d "$DOTFILES_DIR/claude/skills" ]; then
  for skill_dir in "$DOTFILES_DIR"/claude/skills/*/; do
    skill_name="$(basename "$skill_dir")"
    if [ -L "$HOME/.claude/skills/$skill_name" ]; then
      continue   # 이미 repo 를 가리키는 심링크 — 재링크 불필요
    fi
    mkdir -p ~/.claude/skills/"$skill_name"
    for f in "$skill_dir"*; do
      ln -sf "$f" ~/.claude/skills/"$skill_name/$(basename "$f")" \
        || echo "[install] warn: 스킬 링크 실패 $skill_name/$(basename "$f") — 계속 진행"
    done
  done
fi

# Custom scripts
link "$DOTFILES_DIR/bin/gemini-ask" ~/.local/bin/gemini-ask
link "$DOTFILES_DIR/bin/codex-ask" ~/.local/bin/codex-ask
link "$DOTFILES_DIR/bin/claude-userbot-login" ~/.local/bin/claude-userbot-login
link "$DOTFILES_DIR/bin/claude-userbot-send" ~/.local/bin/claude-userbot-send
link "$DOTFILES_DIR/bin/claude-token-audit" ~/.local/bin/claude-token-audit

# 아래는 지금까지 install.sh 가 배포하지 않았는데 ~/.local/bin 에 손으로 심링크가 만들어져
# 돌고 있던 것들이다(2026-07-27 실측: 31개 중 23개). 새 머신에서는 유닛만 배포되고
# ExecStart 가 가리키는 스크립트가 없어 조용히 실패했을 상태 — claude-service-exec(세션
# 런처)와 claude-session-healthcheck(감시자 본체)까지 빠져 있었다. repo 를 정본으로 되돌린다.
for _b in ai-debate ai-debate-detach ai-out-filter ai-telemetry-log ai-weekly-brief \
          claude-account-night-switch claude-account-timed-switch \
          claude-accounts claude-accounts-usage claude-bun-ensure claude-control-bot \
          claude-ctx-metrics claude-m365-read \
          claude-memory-audit claude-memory-lifecycle \
          claude-progress-updater claude-progress-updater-all \
          claude-quota-cache-adopt claude-quota-cache-clear claude-quota-observe \
          claude-rotate-archive \
          claude-service-exec claude-service-notify claude-service-template claude-soft-restart \
          claude-session-healthcheck claude-session-nudge claude-session-probe claude-stall-guard \
          claude-telegram-healthcheck claude-turn-audit \
          claude-usage-daily-report claude-watchdog \
          claude-weekly-stability-check codex-usage propose-skill-patch \
          rate-limit-recovery; do
    [ -f "$DOTFILES_DIR/bin/$_b" ] || { warn "bin/$_b 없음 — 링크 스킵"; continue; }
    link "$DOTFILES_DIR/bin/$_b" "$HOME/.local/bin/$_b"
done
unset _b

# 폐기한 명령의 잔재를 치운다. install.sh 는 링크를 덮어쓰기만 하고 지우지는 않아서,
# 배포 목록에서 뺀 이름은 ~/.local/bin 에 깨진 링크로 남는다 — 그러면 PATH 에서 먼저
# 잡혀 "명령은 있는데 안 돈다" 가 된다.
for _gone in claude-account-switch; do
    _l="$HOME/.local/bin/$_gone"
    if [ -L "$_l" ] && [ ! -e "$_l" ]; then
        rm -f "$_l" && echo "[install] 폐기된 링크 제거: $_l"
    fi
done
unset _gone _l

# Shell aliases (add source line to bashrc if not present)
if ! grep -q 'claude-aliases.sh' ~/.bashrc 2>/dev/null; then
  echo "" >> ~/.bashrc
  echo "# Claude Code aliases" >> ~/.bashrc
  echo "[ -f \"$DOTFILES_DIR/shell/claude-aliases.sh\" ] && source \"$DOTFILES_DIR/shell/claude-aliases.sh\"" >> ~/.bashrc
fi

# Claude Code 자체는 install.sh 가 설치하지 않는다 — 안내만 한다.
# 2026-07-27: 한 기계에서 이 블록이 실제로 `npm install -g` 를 시도했다. 비대화형 ssh 는
# PATH 에 ~/.local/bin 이 없어 `command -v claude` 가 실패했을 뿐이고, claude 는 네이티브
# 설치본으로 멀쩡히 있었다. npm 전역쓰기가 가능한 머신이었다면 세션 8개가 돌아가는 중에
# 두 번째(npm 판) claude 가 설치돼 PATH 순서에 따라 런처를 가로챘을 상황이다.
# 런타임 교체는 dotfiles 설치기가 무인으로 할 일이 아니다.
if ! command -v claude &>/dev/null \
   && [ ! -x "$HOME/.local/bin/claude" ] && [ ! -x "$HOME/.claude/local/claude" ]; then
  warn "claude CLI 를 찾지 못했다. 설치는 직접: curl -fsSL https://claude.ai/install.sh | bash"
  warn "  (npm 전역설치는 기존 네이티브 설치본과 충돌할 수 있어 여기서 자동 실행하지 않는다)"
fi

# Install uv (Python package manager for MCP servers) — pinned + sha256 verified
# 2026-05-14: replaced `curl ... | sh` supply-chain pattern with pinned release tarball + SHA256.
# Bump UV_VERSION when needed and refresh from GitHub releases.
if ! command -v uv &>/dev/null; then
  echo "Installing uv (pinned + sha256 verified)..."
  UV_VERSION="0.11.14"
  UV_ASSET="uv-x86_64-unknown-linux-gnu.tar.gz"
  UV_BASE="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}"
  UV_TMP="$(mktemp -d)"
  if curl -fsSL "${UV_BASE}/${UV_ASSET}" -o "${UV_TMP}/${UV_ASSET}" \
     && curl -fsSL "${UV_BASE}/${UV_ASSET}.sha256" -o "${UV_TMP}/${UV_ASSET}.sha256" \
     && ( cd "${UV_TMP}" && sha256sum -c "${UV_ASSET}.sha256" >/dev/null ); then
    tar -xzf "${UV_TMP}/${UV_ASSET}" -C "${UV_TMP}"
    mkdir -p "$HOME/.local/bin"
    install -m755 "${UV_TMP}"/uv-x86_64-unknown-linux-gnu/uv "$HOME/.local/bin/uv"
    install -m755 "${UV_TMP}"/uv-x86_64-unknown-linux-gnu/uvx "$HOME/.local/bin/uvx" 2>/dev/null || true
    echo "uv ${UV_VERSION} installed to ~/.local/bin"
  else
    echo "WARNING: uv ${UV_VERSION} install failed (download or sha256 mismatch)"
  fi
  rm -rf "${UV_TMP}"
  export PATH="$HOME/.local/bin:$PATH"
fi

# Install MCP servers
MCP_DIR="$HOME/.local/share/mcp"
mkdir -p "$MCP_DIR"

for mcp_repo in \
  "takashiishida/arxiv-latex-mcp" \
  "afrise/academic-search-mcp-server:academic-search-mcp"; do
  # Parse repo:dirname format
  REPO_URL="${mcp_repo%%:*}"
  DIR_NAME="${mcp_repo##*:}"
  [ "$DIR_NAME" = "$REPO_URL" ] && DIR_NAME="$(basename "$REPO_URL")"

  if [ ! -d "$MCP_DIR/$DIR_NAME" ]; then
    echo "Installing MCP: $DIR_NAME..."
    git clone "https://github.com/$REPO_URL.git" "$MCP_DIR/$DIR_NAME" 2>/dev/null && \
    (cd "$MCP_DIR/$DIR_NAME" && uv sync 2>/dev/null) || \
    echo "WARNING: $DIR_NAME install failed"
  fi
done

# cs = claude-sessions (세션 매니저: rotate/restart/list). claude-squad 제거 2026-06-18.
# claude-squad 가 cs 이름을 가로채 cs rotate 가 깨졌던 충돌 해소.
link "$DOTFILES_DIR/bin/claude-sessions" "$HOME/.local/bin/claude-sessions"
link "$DOTFILES_DIR/bin/claude-sessions" "$HOME/.local/bin/cs"

# Auto-install Claude Code plugins (if claude command available)
# CLAUDE_INSTALL_SKIP_PLUGINS=1 이면 건너뛴다. 살아있는 세션 안에서 install.sh 를 돌릴 때
# 필요하다 — claude 자식 프로세스는 telegram MCP long-poll 을 점유해 부모 세션의 MCP 를
# 끊는다(2026-05-29 dotfiles 사고). 플러그인은 이미 설치돼 있으면 재실행할 이유도 없다.
#   기본값도 자동으로 정한다 — 사람이 매번 변수를 기억해야 하면 언젠가 빠뜨린다.
#   Claude Code 세션 안에서는 CLAUDECODE 가 설정되므로 그걸 감지해 기본 스킵.
#   세션 안에서도 굳이 돌리려면 CLAUDE_INSTALL_SKIP_PLUGINS=0 으로 명시.
_SKIP_PLUGINS="${CLAUDE_INSTALL_SKIP_PLUGINS:-}"
if [ -z "$_SKIP_PLUGINS" ] && [ -n "${CLAUDECODE:-}" ]; then
  _SKIP_PLUGINS=1
  echo "[install] Claude Code 세션 내부 감지(CLAUDECODE) — 플러그인 블록 자동 스킵"
fi
if [ "$_SKIP_PLUGINS" = "1" ]; then
  echo "[install] 플러그인 설치 건너뜀(자식 claude 미생성)"
elif command -v claude &>/dev/null; then
  echo "Installing Claude Code plugins..."
  # Add plugin marketplaces
  claude plugin marketplace add https://github.com/K-Dense-AI/claude-scientific-writer 2>/dev/null || true
  # Install plugins
  claude plugin install claude-scientific-writer 2>/dev/null || echo "WARNING: claude-scientific-writer install failed"
else
  echo ""
  echo "NOTE: Claude Code CLI not found. Install plugins manually after installing Claude Code:"
  echo "  /plugin marketplace add https://github.com/K-Dense-AI/claude-scientific-writer"
  echo "  /plugin install claude-scientific-writer"
fi

# Check PATH
if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
  echo ""
  echo "WARNING: ~/.local/bin is not in your PATH."
  echo "  Add this to your shell rc file: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# systemd user units 배포 (git=source of truth, 항상 덮어씀) + 머신별 세션 enable.
#
# 머신 식별 (robust 순서):
#   1) ~/.config/claude-machine-id  (명시적 마커 = 진실원천, rename/clone 에도 불변)
#   2) tailscale 노드명               (마커 없을 때 기본값 제안 — 부트스트랩용)
# 식별 못하면 unit 복사만 하고 enable 은 건너뜀 (안전).
#
# 세션 enable 은 machines/<machine>.list 에 적힌 것만. 추가 가드: 그 세션 토큰
# (.claude/telegram/.env)이 이 머신에 실제 있을 때만 enable → 잘못된 머신에서
# 같은 봇 2중 inbound 사고 방지. (unit 자체에도 ConditionPathExists 로 2중 방어.)
# 머신 식별 (unit 복사 전에 필요 — 기계 전용 유닛 필터가 이 값을 쓴다)
MACHINE_ID=""
MARKER="$HOME/.config/claude-machine-id"
if [ -f "$MARKER" ]; then
  MACHINE_ID=$(tr -d '[:space:]' < "$MARKER")
elif command -v tailscale >/dev/null 2>&1; then
  TS_NAME=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["Self"]["HostName"])
except: pass' 2>/dev/null)
  if [ -n "$TS_NAME" ] && [ -f "$DOTFILES_DIR/machines/${TS_NAME}.list" ]; then
    MACHINE_ID="$TS_NAME"
    echo "machine-id 마커 없음 → tailscale 노드명 '$TS_NAME' 사용. 고정하려면: echo $TS_NAME > $MARKER"
  fi
fi

# 어떤 유닛을 어느 기계에만 심을지는 설정 파일이 정한다. 코드에 기계 이름을 박으면
# 그 이름을 쓰지 않는 기계에서는 걸러낼 수가 없다.
#   ~/.config/claude-unit-hosts — 한 줄에 "<유닛 이름 앞가지> <기계 이름>".
# 그 앞가지로 시작하는 유닛은 적힌 기계에서만 심는다. 설정이 없으면 전부 심는다 —
# 기계가 하나면 걸러낼 것이 없다. 기계가 여럿인데 안 적으면, 한 기계만 재야 하는
# 유닛이 다른 기계에서도 돌면서 자기 값을 남의 이름으로 보고한다.
UNIT_HOSTS="$HOME/.config/claude-unit-hosts"

unit_allowed_here() {  # $1 = 유닛 파일 이름
  [ -f "$UNIT_HOSTS" ] || return 0
  local prefix host
  while read -r prefix host; do
    case "$prefix" in ''|'#'*) continue;; esac
    [ -n "$host" ] || continue
    case "$1" in
      "$prefix"*) [ "$MACHINE_ID" = "$host" ] && return 0 || return 1;;
    esac
  done < "$UNIT_HOSTS"
  return 0
}

SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
if [ -d "$DOTFILES_DIR/systemd/user" ]; then
  mkdir -p "$SYSTEMD_USER_DIR"
  for f in "$DOTFILES_DIR"/systemd/user/*.service "$DOTFILES_DIR"/systemd/user/*.timer; do
    [ -f "$f" ] || continue
    base=$(basename "$f")
    # 한 기계에서만 돌아야 하는 유닛은 여기서 걸러낸다. 무엇이 그런 유닛인지는
    # 위 설정 파일이 정한다. 이런 유닛이 두 기계에 심기면 둘 다 자기 값을 재고
    # 같은 이름으로 보고해서, 어느 쪽 값인지 사람이 가릴 수 없게 된다.
    #   claude-daily-report-* — 하루 보고를 만들고 백업하고 생성 여부를 확인한다.
    #     보고를 만드는 기계가 아닌 곳에 심으면 없는 보고를 찾아 오경보한다.
    #   home-claude-health, home-infra-watch — 한 기계의 로그인과 설치 상태를 재서
    #     다른 기계의 세션에 알린다. 재는 쪽에만 심는다.
    #   claude-coord-report — 여러 기계 몫을 한 보고로 묶어 보낸다. 묶는 기계에만.
    unit_allowed_here "$base" || continue
    # 이미 repo 를 가리키는 심링크로 심긴 유닛은 그 자체로 최신이다. cp 하면 원본=대상이
    # 되어 "are the same file" 로 실패하고 set -e 가 뒷부분을 전부 날린다(스킬 블록과 동일 구조).
    [ -L "$SYSTEMD_USER_DIR/$base" ] && continue
    copy "$f" "$SYSTEMD_USER_DIR/$base"
  done
  # 이 둘은 유닛과 같은 기계에서만 돌아야 하는 스크립트다. 위 공용 bin 루프에 넣었더니
  # 유닛 필터를 안 타서 다른 기계에도 심겼다(2026-08-07). 유닛이 없어 저절로 실행되진
  # 않았지만, 손으로 부르면 이 기계를 재고서 저쪽 이름표로 알린다 — 자기 상태를 남의
  # 이름으로 보고하는 꼴이다. MACHINE_ID 가 정해지는 이 지점 이후에서만 링크하고,
  # 걸러진 기계에 남은 링크는 걷는다.
  for b in home-claude-health home-infra-watch; do
    if unit_allowed_here "$b.service"; then
      link "$DOTFILES_DIR/bin/$b" "$HOME/.local/bin/$b"
    elif [ -L "$HOME/.local/bin/$b" ]; then
      rm -f "$HOME/.local/bin/$b"
      echo "$b 링크 제거(machine=$MACHINE_ID — 이 기계에서 돌 유닛이 아니다)"
    fi
  done

  # 부팅 직후 동시기동 분산 드롭인. 세션 유닛(ExecStart=claude-service-exec)에만,
  # 저장소에 없고 그 기계에서 직접 만든 세션 유닛까지 자동 커버.
  # 유닛 본문을 안 건드리는 드롭인 방식 — 되돌리려면 *.d/10-boot-stagger.conf 삭제 후 reload.
  # 이 드롭인은 install.sh 를 돌릴 때마다 통째로 다시 쓴다. 그래서 아래 본문을 고쳐도
  # 이미 깔린 드롭인은 그 자리에서 안 바뀌고 다음 install.sh 때 바뀐다. 돌고 있는
  # 유닛은 그때도 옛 값을 들고 있다 — 다시 시작해야 새 값으로 선다.
  # 지연 자체는 claude-boot-stagger 가 uptime 조건으로 판단(부팅 직후 아니면 0초).
  link "$DOTFILES_DIR/bin/claude-boot-stagger" "$HOME/.local/bin/claude-boot-stagger"
  chmod +x "$DOTFILES_DIR/bin/claude-boot-stagger"
  for u in "$SYSTEMD_USER_DIR"/claude-*.service; do
    [ -f "$u" ] || continue
    grep -q 'claude-service-exec' "$u" || continue
    dropin="${u}.d"
    mkdir -p "$dropin"
    cat > "$dropin/10-boot-stagger.conf" <<'DROPIN'
# 자동 생성(install.sh) — 직접 편집하지 말 것. 부팅 직후에만 기동을 흩뿌린다.
[Service]
ExecStartPre=%h/.local/bin/claude-boot-stagger
DROPIN
  done
  systemctl --user daemon-reload 2>/dev/null || true
  echo "Synced systemd units to $SYSTEMD_USER_DIR (+ boot-stagger 드롭인)"

  MANIFEST="$DOTFILES_DIR/machines/${MACHINE_ID}.list"
  if [ -n "$MACHINE_ID" ] && [ -f "$MANIFEST" ]; then
    echo "Machine='$MACHINE_ID' — enabling sessions from machines/${MACHINE_ID}.list"
    while read -r s; do
      case "$s" in ''|\#*) continue;; esac
      unit="claude-${s}.service"
      [ -f "$SYSTEMD_USER_DIR/$unit" ] || { echo "  skip $s (no unit)"; continue; }
      wd=$(sed -n 's/^WorkingDirectory=//p' "$SYSTEMD_USER_DIR/$unit" | head -1)
      # 유닛은 홈을 %h 로 적는다. 여기서 그 하나만 푼다 — systemd 의 지정자 전체를
      # 다시 구현하지 않는다(코드 원칙 2). %%h 는 systemd 에서 글자 그대로의 %h 라
      # 안 건드린다.
      case "$wd" in
        %h)   wd="$HOME" ;;
        %h/*) wd="$HOME${wd#%h}" ;;
      esac
      # 못 푼 지정자는 조용히 넘기지 않는다. 그대로 두면 아래 .env 검사가 실패해서
      # "이 머신 세션 아님" 으로 건너뛰는데, 그건 줄 자체가 없는 상태와 같은 모양이라
      # 왜 안 섰는지가 안 보인다. 둘은 다른 상태다.
      if [ -n "$wd" ] && case "${wd//%%/}" in *%*) true;; *) false;; esac; then
        fail_step "WorkingDirectory 지정자를 못 푼다 ($unit): $wd"
        continue
      fi
      if [ -n "$wd" ] && [ -f "$wd/.claude/telegram/.env" ]; then
        # 이미 enable 돼 있는데 꺼져 있는 세션은 운영자가 일부러 멈춘 것이다.
        # install.sh 가 --now 로 깨우면 의도하지 않은 세션이 살아나
        # quota 를 먹는다. 그래서 부팅 자동기동 설정(enable)만 갱신하고 start 는 하지 않는다.
        # 처음 설치(enable 이력 없음)일 때만 --now 로 즉시 기동한다.
        if [ "$(systemctl --user is-enabled "$unit" 2>/dev/null)" = "enabled" ] \
           && [ "$(systemctl --user is-active "$unit" 2>/dev/null)" != "active" ]; then
          systemctl --user enable "$unit" 2>/dev/null \
            && echo "  enabled(정지 상태 유지) $unit" || echo "  WARN enable failed: $unit"
        else
          systemctl --user enable --now "$unit" 2>/dev/null \
            && echo "  enabled $unit" || echo "  WARN enable failed: $unit"
        fi
      else
        echo "  skip $s (token .env 없음: ${wd}/.claude/telegram/.env) — 이 머신 세션 아님"
      fi
    done < "$MANIFEST"
    loginctl enable-linger "$USER" 2>/dev/null || true
  else
    echo "NOTE: machine-id 미식별. 'echo <기계 이름> > $MARKER' 후 재실행하면 그 머신 세션 자동 enable."
    echo "      또는 SETUP.md 따라 수동 enable. 토큰 .env / control-bot/.env 선행 필요."
  fi
fi

# 비밀 복원 (봇토큰/유저봇/컨트롤봇).
# 권장(키워드 모드): CLAUDE_SECRETS_KEYWORD=1 → PRIVATE repo claude-secrets 에서
#   최신 번들 자동선택 + 키워드 터미널 입력으로 복원(restore 가 repo 자동 clone).
# legacy(번들 경로): CLAUDE_SECRETS_BUNDLE 에 번들 경로. 패스프레이즈는
#   CLAUDE_SECRETS_PASSFILE 또는 비번관리자에서.
# 없으면 생략(실패 안 함).
if [ -n "${CLAUDE_SECRETS_KEYWORD:-}" ] && [ -x "$DOTFILES_DIR/bin/restore-secrets.sh" ]; then
  echo "[install] 비밀 복원(키워드 모드, private repo claude-secrets 최신 번들):"
  "$DOTFILES_DIR/bin/restore-secrets.sh" --keyword --auto-latest \
    || echo "[install] 비밀 복원 실패 — restore-secrets.sh --keyword --auto-latest 수동 실행 필요"
elif [ -n "${CLAUDE_SECRETS_BUNDLE:-}" ] && [ -f "${CLAUDE_SECRETS_BUNDLE}" ]; then
  if [ -x "$DOTFILES_DIR/bin/restore-secrets.sh" ]; then
    echo "[install] 비밀 번들 복원: $CLAUDE_SECRETS_BUNDLE"
    "$DOTFILES_DIR/bin/restore-secrets.sh" "$CLAUDE_SECRETS_BUNDLE" \
      ${CLAUDE_SECRETS_PASSFILE:+--passfile "$CLAUDE_SECRETS_PASSFILE"} \
      || echo "[install] 비밀 복원 실패 — restore-secrets.sh 수동 실행 필요"
  fi
else
  echo "[install] 비밀 미복원 — 세션 봇/유저봇 토큰 없음."
  echo "          복구(권장): CLAUDE_SECRETS_KEYWORD=1 ~/dotfiles/install.sh  (키워드 입력)"
  echo "          또는: bin/restore-secrets.sh --keyword --auto-latest → claude login."
fi

echo ""
echo "Dotfiles installed successfully!"
echo "  ~/.claude/CLAUDE.md -> $DOTFILES_DIR/claude/CLAUDE.md"
echo "  ~/.claude/settings.json -> $DOTFILES_DIR/claude/settings.json"
echo "  ~/.claude/mcp.json -> $DOTFILES_DIR/claude/mcp.json"
echo "  ~/.claude/hooks/ -> $DOTFILES_DIR/claude/hooks/"
echo "  ~/.claude/skills/ -> $DOTFILES_DIR/claude/skills/"
echo "  ~/.local/bin/gemini-ask -> $DOTFILES_DIR/bin/gemini-ask"
echo "  ~/.local/bin/codex-ask -> $DOTFILES_DIR/bin/codex-ask"
echo ""
echo "Tools: cs (claude-sessions), npx ccusage (usage tracking)"
echo "Plugins: claude-scientific-writer"
echo ""
if ! claude --version &>/dev/null 2>&1; then
  echo "Next step: Install Node.js, then re-run this script"
else
  echo "Next step: claude login"
fi

# 종결 처리. 마지막 줄까지 왔어도 배포 실패가 있었으면 성공이 아니다(degraded, rc 비제로).
# install-last-ok 는 "전 단계가 실제로 성공한 마지막 지점"만 가리켜야 하므로 그 경우 보존한다.
if [ "$INSTALL_FAILURES" -gt 0 ]; then
  _write_bad degraded 1 -
  echo "[install] 부분 실패 ${INSTALL_FAILURES}건 — 상태=degraded, install-last-ok 는 갱신하지 않음."
  echo "[install]   실패 단계: $INSTALL_FAILED_STEPS"
  echo "[install]   기록: $INSTALL_BAD"
  trap - ERR
  exit 1
fi

# 정상 완주. host/HEAD/schema/완료시각 + 이번에 실제 배포한 경로 manifest 를 원자적으로 기록.
# manifest 가 단일 출처다 — 감시자가 경로 목록을 따로 들고 있으면 새 배포 경로가 생길 때 미탐이 난다.
_MANIFEST_TMP="$INSTALL_STATE_DIR/.install-manifest.$$"
_MANIFEST_COPY_TMP="$INSTALL_STATE_DIR/.install-manifest-copy.$$"
mkdir -p "$INSTALL_STATE_DIR" 2>/dev/null || true
printf '%s' "$INSTALL_MANIFEST" > "$_MANIFEST_TMP" 2>/dev/null || true
printf '%s' "$INSTALL_MANIFEST_COPY" > "$_MANIFEST_COPY_TMP" 2>/dev/null || true
# 주의: python3 - 는 heredoc 으로 프로그램을 stdin 에서 읽는다. manifest 를 파이프로 넘기면
# heredoc 에 덮여 조용히 빈 목록이 된다(2026-07-27 자체 테스트에서 실제로 걸림) → 파일로 전달.
python3 - "$INSTALL_OK" "$(_install_head)" "$(_install_mid)" "$INSTALL_SCHEMA" "$_MANIFEST_TMP" "$_MANIFEST_COPY_TMP" <<'PY' 2>/dev/null || true
import json, os, sys, time
path, head, mid, schema, mpath, cpath = sys.argv[1:7]
def read(p):
    with open(p) as f:
        return sorted({l.strip() for l in f if l.strip()})
manifest, manifest_copy = read(mpath), read(cpath)
rec = {"schema": int(schema), "state": "ok", "host": os.uname().nodename, "machine_id": mid,
       "head": head, "finished_at": time.strftime("%Y-%m-%d %H:%M:%S KST"),
       "failure_count": 0, "manifest": manifest, "manifest_copy": manifest_copy}
tmp = path + ".tmp.%d" % os.getpid()
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(tmp, "w") as f: json.dump(rec, f, ensure_ascii=False, indent=1)
os.replace(tmp, path)
PY
rm -f "$_MANIFEST_TMP" "$_MANIFEST_COPY_TMP" "$INSTALL_BAD" 2>/dev/null || true
echo "[install] 완주(실패 0건). 마커: $INSTALL_OK"
