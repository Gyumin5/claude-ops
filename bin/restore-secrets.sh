#!/bin/bash
# restore-secrets.sh — backup-secrets.sh 가 만든 암호화 번들을 되살린다.
#
# 무엇을 되살릴지는 이 스크립트가 정하지 않는다. 담을 때와 같은 파일
# ~/.config/claude-secrets/policy.conf 를 본다 — 거기서 ! 로 표시한 것은
# --with-sensitive 를 줘야 되살아난다. 그 파일이 없으면 아무것도 안 하고 멈춘다.
# 서식과 예시는 backup/policy.conf.example 에 있다.
#
# 다시 깔고 복구하는 차례: 저장소 clone → install.sh → (이 스크립트) → claude login
#   (로그인 자격은 번들에 안 담는다) → 세션 enable.
#
# 번들은 홈 기준 상대경로로 들어 있어 원래 자리로 풀린다(상위 폴더는 자동으로 만든다).
# 권한은 폴더 700, 파일 600 으로 맞춘다.
#
# 쓰는 법:
#   restore-secrets.sh [번들.tar.gz.gpg] [--keyword] [--auto-latest]
#                      [--passfile 경로] [--dry-run] [--force]
#                      [--with-sensitive] [--secrets-repo 폴더]
#   --keyword          키워드로 늘린 패스프레이즈를 쓴다(터미널 입력, 소금은 보관소에서)
#   --auto-latest      번들을 안 주면 보관소에서 이 기계의 최신 번들을 고른다
#   --dry-run          되살릴 목록만 보여 준다
#   --force            기존 파일을 백업 없이 덮어쓴다(기본은 .pre-restore 사본을 남긴다)
#   --with-sensitive   정책에서 ! 로 표시한 파일까지 되살린다
#   --secrets-repo     보관소 클론 경로(기본 ~/claude-secrets-repo)
set -uo pipefail

HOME_DIR="${HOME}"
BINDIR="$(cd "$(dirname "$0")" && pwd)"
MACHINE_FILE="$HOME_DIR/.config/claude-machine-id"
PASS_DIR="$HOME_DIR/.config/claude-secrets"
PASSFILE="$PASS_DIR/passphrase"
POLICY="$PASS_DIR/policy.conf"
SECRETS_REPO="$HOME_DIR/claude-secrets-repo"
SECRETS_URL_FILE="$PASS_DIR/repo-url"
DRYRUN=false
FORCE=false
WITH_SENSITIVE=false
KEYWORD_MODE=false
AUTO_LATEST=false
BUNDLE=""
TMPPASS=""

while [ $# -gt 0 ]; do
    case "$1" in
        --passfile) PASSFILE="$2"; shift 2 ;;
        --keyword) KEYWORD_MODE=true; shift ;;
        --auto-latest) AUTO_LATEST=true; shift ;;
        --secrets-repo) SECRETS_REPO="$2"; shift 2 ;;
        --dry-run) DRYRUN=true; shift ;;
        --force) FORCE=true; shift ;;
        --with-sensitive) WITH_SENSITIVE=true; shift ;;
        -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
        -*) echo "모르는 인자: $1" >&2; exit 2 ;;
        *) BUNDLE="$1"; shift ;;
    esac
done

command -v gpg >/dev/null || { echo "gpg 가 없다." >&2; exit 1; }

# 담을 때와 같은 정책을 본다. 없으면 여기서 멈춘다 — 무엇이 민감인지 모르는 채로
# 풀면, 안 되살려야 할 로그인 세션이 이 기계에 그대로 깔린다.
if [ ! -s "$POLICY" ]; then
    echo "되살릴 목록이 없다: $POLICY" >&2
    echo "backup/policy.conf.example 를 그 자리로 베끼고 쓸 줄만 남겨라." >&2
    exit 1
fi

MACHINE="$(cat "$MACHINE_FILE" 2>/dev/null || echo unknown)"

TARPLAIN="$(mktemp "${TMPDIR:-/tmp}/cs-r-XXXXXX.tar.gz")"
cleanup() { rm -f "$TARPLAIN" "${TMPPASS:-}"; }
trap cleanup EXIT

# 정책에서 민감 표시된 홈 기준 경로들
sensitive=()
while IFS= read -r line; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    case "$line" in
        !?*) sensitive+=("${line#!}") ;;
    esac
done < "$POLICY"

is_sensitive() {
    local e="$1" s
    for s in ${sensitive[@]+"${sensitive[@]}"}; do
        [ "$e" = "$s" ] && return 0
    done
    return 1
}

ensure_secrets_repo() {
    local url=""
    [ -s "$SECRETS_URL_FILE" ] && url="$(cat "$SECRETS_URL_FILE")"
    if [ -z "$url" ] && [ ! -d "$SECRETS_REPO/.git" ]; then
        echo "보관소 주소가 없다: $SECRETS_URL_FILE" >&2
        echo "비공개 저장소 주소를 그 파일에 한 줄로 적어라." >&2
        return 1
    fi
    if [ ! -d "$SECRETS_REPO/.git" ]; then
        echo "보관소를 clone 한다: $url → $SECRETS_REPO"
        git clone "$url" "$SECRETS_REPO" || { echo "clone 실패." >&2; return 1; }
    else
        git -C "$SECRETS_REPO" pull --ff-only 2>&1 | tail -1 || true
    fi
}

if [ "$AUTO_LATEST" = true ] && [ -z "$BUNDLE" ]; then
    ensure_secrets_repo || exit 1
    BUNDLE="$(ls -t "$SECRETS_REPO"/bundles/claude-secrets-"${MACHINE}"-*.tar.gz.gpg 2>/dev/null | head -1)"
    [ -n "$BUNDLE" ] || { echo "보관소에 ${MACHINE} 번들이 없다." >&2; exit 1; }
    echo "고른 번들: $BUNDLE"
fi

[ -n "$BUNDLE" ] && [ -f "$BUNDLE" ] || {
    echo "번들 경로가 필요하다(있는 파일이어야 한다): '$BUNDLE'  (--auto-latest 로 고를 수도 있다)" >&2
    exit 2; }

if [ "$KEYWORD_MODE" = true ]; then
    ensure_secrets_repo || exit 1
    SALT="$SECRETS_REPO/salt"
    [ -s "$SALT" ] || { echo "소금이 없다: $SALT" >&2; exit 1; }
    TMPPASS="$(mktemp "${TMPDIR:-/tmp}/cs-rpass-XXXXXX")"; chmod 600 "$TMPPASS"
    echo "키워드로 패스프레이즈를 만든다(터미널 입력):"
    "$BINDIR/secrets-keyderive" --salt "$SALT" --out "$TMPPASS" \
        || { echo "키워드 파생 실패." >&2; exit 1; }
    PASSFILE="$TMPPASS"
fi
[ -s "$PASSFILE" ] || { echo "패스프레이즈가 없다: $PASSFILE (--passfile 또는 --keyword)" >&2; exit 1; }

gpg --batch --yes --pinentry-mode loopback --passphrase-file "$PASSFILE" \
    --decrypt -o "$TARPLAIN" "$BUNDLE" 2>/dev/null \
    || { echo "복호화 실패(패스프레이즈가 다른가?)." >&2; exit 1; }

mapfile -t entries < <(tar -tzf "$TARPLAIN")

echo "되살릴 것 (기계=$MACHINE, dry-run=$DRYRUN, force=$FORCE):"
to_restore=()
for e in "${entries[@]}"; do
    [ -z "$e" ] && continue
    case "$e" in */) continue ;; esac
    if is_sensitive "$e" && [ "$WITH_SENSITIVE" != true ]; then
        echo "  건너뜀  $e  (민감 표시 — 되살리려면 --with-sensitive)"
        continue
    fi
    echo "  되살림  $e"
    to_restore+=("$e")
done

if [ "$DRYRUN" = true ]; then
    echo "(dry-run: 아무것도 안 바꿨다)"
    exit 0
fi
[ ${#to_restore[@]} -eq 0 ] && { echo "되살릴 것이 없다."; exit 0; }

TS="$(date '+%Y%m%d-%H%M%S')"
restored=0
for e in "${to_restore[@]}"; do
    target="$HOME_DIR/$e"
    parent="$(dirname "$target")"
    mkdir -p "$parent"
    if [ -f "$target" ] && [ "$FORCE" != true ]; then
        cp -p "$target" "${target}.pre-restore.${TS}"
    fi
    tar -C "$HOME_DIR" -xzf "$TARPLAIN" "$e"
    chmod 600 "$target" 2>/dev/null || true
    restored=$((restored+1))
done

# 자격이 든 폴더는 700 으로 맞춘다.
while IFS= read -r f; do
    d="$(dirname "$HOME_DIR/$f")"
    [ -d "$d" ] && chmod 700 "$d"
done < <(printf '%s\n' "${to_restore[@]}")

echo "되살리기 끝: ${restored}개."
echo "다음: claude login (로그인 자격은 번들에 없다) → systemctl --user daemon-reload → 세션 enable."
