#!/bin/bash
# backup-secrets.sh — 이 기계에만 있는 자격 파일을 암호화 번들 하나로 묶는다.
#
# 담을 것은 이 스크립트가 정하지 않는다. ~/.config/claude-secrets/policy.conf 가
# 정본이고, 되살리는 쪽(restore-secrets.sh)도 같은 파일을 본다. 그 파일이 없으면
# 아무것도 안 하고 멈춘다 — 서식과 예시는 backup/policy.conf.example 에 있다.
#
# 패스프레이즈는 두 갈래다.
#   (기본)       ~/.config/claude-secrets/passphrase — 처음 돌릴 때 임의로 만든다.
#   (--keyword)  사람이 외우는 키워드를 scrypt 로 늘린다. 키워드는 터미널에서만
#                받고 어디에도 안 적는다. 소금은 비공개 보관소에 둔다.
#
# --keyword 로 돌리면 번들을 비공개 보관소(git 저장소)에 commit 하고 push 까지 한다.
# 그 주소는 ~/.config/claude-secrets/repo-url 에 적는다 — 안 적혀 있으면 멈춘다.
# 이 저장소에는 자격이 한 줄도 안 들어간다.
#
# 암호화: gpg --symmetric --cipher-algo AES256 (키링 없이 패스프레이즈만).
#
# 쓰는 법: backup-secrets.sh [--keyword] [--with-sensitive] [--no-push]
#                           [-o 출력폴더] [--secrets-repo 폴더]
#   --keyword         키워드로 늘린 패스프레이즈를 쓰고 비공개 보관소로 올린다
#   --with-sensitive  정책에서 ! 로 표시한 파일까지 담는다
#   --no-push         --keyword 라도 push 는 안 한다(보관소에 commit 만)
#   -o                번들을 둘 폴더(기본 ~/claude-secrets-backup)
#   --secrets-repo    비공개 보관소 클론 경로(기본 ~/claude-secrets-repo)
set -uo pipefail

HOME_DIR="${HOME}"
DOTDIR="$(cd "$(dirname "$0")/.." && pwd)"
BINDIR="$(cd "$(dirname "$0")" && pwd)"
MACHINE_FILE="$HOME_DIR/.config/claude-machine-id"
PASS_DIR="$HOME_DIR/.config/claude-secrets"
PASSFILE="$PASS_DIR/passphrase"
POLICY="$PASS_DIR/policy.conf"
OUTDIR="$HOME_DIR/claude-secrets-backup"
SECRETS_REPO="$HOME_DIR/claude-secrets-repo"
SECRETS_URL_FILE="$PASS_DIR/repo-url"
KEYWORD_MODE=false
WITH_SENSITIVE=false
PUSH=true

while [ $# -gt 0 ]; do
    case "$1" in
        --keyword) KEYWORD_MODE=true; shift ;;
        --with-sensitive) WITH_SENSITIVE=true; shift ;;
        --no-push) PUSH=false; shift ;;
        -o) OUTDIR="$2"; shift 2 ;;
        --secrets-repo) SECRETS_REPO="$2"; shift 2 ;;
        -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
        *) echo "모르는 인자: $1" >&2; exit 2 ;;
    esac
done

command -v gpg >/dev/null || { echo "gpg 가 없다. 먼저 설치해라." >&2; exit 1; }

# 정책이 없으면 여기서 멈춘다. 뒤로 한 걸음도 더 가지 않는다 — 기본 목록으로
# 대신하면 이 기계에 맞는지 아무도 안 본 목록으로 자격을 담게 된다.
if [ ! -s "$POLICY" ]; then
    echo "담을 목록이 없다: $POLICY" >&2
    echo "backup/policy.conf.example 를 그 자리로 베끼고 쓸 줄만 남겨라." >&2
    exit 1
fi

MACHINE="$(cat "$MACHINE_FILE" 2>/dev/null || echo unknown)"
mkdir -p "$PASS_DIR"; chmod 700 "$PASS_DIR"

TMPPASS=""
cleanup() { rm -f "${TARPLAIN:-}" "${TMPPASS:-}"; }
trap cleanup EXIT

# 비공개 보관소 준비(없으면 clone, 있으면 ff-only pull)
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
    mkdir -p "$SECRETS_REPO/bundles"
}

if [ "$KEYWORD_MODE" = true ]; then
    ensure_secrets_repo || exit 1
    SALT="$SECRETS_REPO/salt"
    [ -s "$SALT" ] || { echo "소금이 없다: $SALT (보관소가 깨졌나?)" >&2; exit 1; }
    TMPPASS="$(mktemp "${TMPDIR:-/tmp}/cs-pass-XXXXXX")"; chmod 600 "$TMPPASS"
    echo "키워드로 패스프레이즈를 만든다(터미널에 직접 입력):"
    "$BINDIR/secrets-keyderive" --salt "$SALT" --out "$TMPPASS" --confirm \
        || { echo "키워드 파생 실패." >&2; exit 1; }
    PASSFILE="$TMPPASS"
else
    if [ ! -s "$PASSFILE" ]; then
        head -c 32 /dev/urandom | base64 | tr -d '\n' > "$PASSFILE"
        chmod 600 "$PASSFILE"
        echo "!! 새 패스프레이즈를 만들었다: $PASSFILE"
        echo "!! 비밀번호 관리자에 반드시 옮겨 적어라(잃으면 복호화 불가):"
        echo "!!   cat $PASSFILE"
    fi
fi

# 정책을 읽어 담을 절대경로를 모은다.
abs_list=()
skipped_sensitive=0
sessions=()
add() { [ -f "$1" ] && abs_list+=("$1"); }

collect_sessions() {
    local list="$DOTDIR/machines/${MACHINE}.list" s wd
    [ -f "$list" ] || { echo "경고: 세션 목록이 없다: $list" >&2; return 0; }
    while IFS= read -r s; do
        case "$s" in ''|\#*) continue ;; esac
        wd="$(systemctl --user show -p WorkingDirectory --value "claude-${s}.service" 2>/dev/null)"
        [ -n "$wd" ] && add "$wd/.claude/telegram/.env" && sessions+=("$s")
    done < "$list"
}

while IFS= read -r line; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -z "$line" ] && continue
    case "$line" in
        @sessions) collect_sessions ;;
        !*)
            if [ "$WITH_SENSITIVE" = true ]; then
                add "$HOME_DIR/${line#!}"
            else
                [ -f "$HOME_DIR/${line#!}" ] && skipped_sensitive=$((skipped_sensitive + 1))
            fi
            ;;
        /*) echo "경고: 홈 밖 경로는 안 담는다: $line" >&2 ;;
        *) add "$HOME_DIR/$line" ;;
    esac
done < "$POLICY"

if [ ${#abs_list[@]} -eq 0 ]; then
    echo "담을 자격 파일이 없다(기계=$MACHINE). 멈춘다." >&2
    exit 1
fi

# 홈 기준 상대경로로 바꾼다.
rel_list=()
for f in "${abs_list[@]}"; do
    case "$f" in
        "$HOME_DIR"/*) rel_list+=("${f#"$HOME_DIR"/}") ;;
        *) echo "경고: 홈 밖 경로 건너뜀: $f" >&2 ;;
    esac
done

mkdir -p "$OUTDIR"; chmod 700 "$OUTDIR"
TS="$(date '+%Y%m%d-%H%M%S')"
BASE="claude-secrets-${MACHINE}-${TS}"
TARPLAIN="$(mktemp "${TMPDIR:-/tmp}/cs-XXXXXX.tar.gz")"
BUNDLE="$OUTDIR/${BASE}.tar.gz.gpg"
META="$OUTDIR/${BASE}.meta"

tar -C "$HOME_DIR" -czf "$TARPLAIN" "${rel_list[@]}"
gpg --batch --yes --pinentry-mode loopback --passphrase-file "$PASSFILE" \
    --symmetric --cipher-algo AES256 -o "$BUNDLE" "$TARPLAIN"
chmod 600 "$BUNDLE"

# 메타에는 파일 이름과 해시만 적는다 — 내용은 한 줄도 안 적는다.
{
    echo "created: $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "machine: $MACHINE"
    echo "hostname: $(hostname)"
    echo "passphrase_mode: $([ "$KEYWORD_MODE" = true ] && echo keyword-scrypt || echo random-file)"
    echo "sensitive: $([ "$WITH_SENSITIVE" = true ] && echo included || echo excluded)"
    echo "files: ${#rel_list[@]}"
    echo "sha256: $(sha256sum "$BUNDLE" | awk '{print $1}')"
    echo "manifest:"
    for r in "${rel_list[@]}"; do echo "  - $r"; done
} > "$META"
chmod 600 "$META"

# 직전 번들과 목록이 달라졌으면 알린다(세션이 늘거나 줄었을 때).
PREV_META="$(ls -t "$OUTDIR"/claude-secrets-"${MACHINE}"-*.meta 2>/dev/null | sed -n 2p)"
if [ -n "$PREV_META" ]; then
    if ! diff -q <(grep '^  - ' "$PREV_META") <(grep '^  - ' "$META") >/dev/null 2>&1; then
        echo "ⓘ 직전 백업과 담긴 목록이 다르다(세션이 늘거나 줄었다)."
    fi
fi

echo "백업 끝:"
echo "  번들: $BUNDLE"
echo "  메타: $META"
echo "  파일 ${#rel_list[@]}개 (세션 ${#sessions[@]}개 + 공통)"
if [ "$skipped_sensitive" -gt 0 ]; then
    echo "  민감 표시 ${skipped_sensitive}개는 안 담았다 — 담으려면 --with-sensitive."
fi

if [ "$KEYWORD_MODE" = true ]; then
    cp -p "$BUNDLE" "$SECRETS_REPO/bundles/"
    cp -p "$META"   "$SECRETS_REPO/bundles/"
    git -C "$SECRETS_REPO" add bundles/ >/dev/null 2>&1
    if git -C "$SECRETS_REPO" diff --cached --quiet 2>/dev/null; then
        echo "→ 보관소에 바뀐 것이 없다(내용 같음)."
    else
        # 누구 이름으로 적을지는 환경변수로만 받는다 — 메일 주소는 저장소에 안 담는다.
        # 안 주면 그 저장소의 git 설정을 그대로 쓴다.
        IDENT=()
        [ -n "${GIT_AUTHOR_NAME:-}" ]  && IDENT+=(-c "user.name=$GIT_AUTHOR_NAME")
        [ -n "${GIT_AUTHOR_EMAIL:-}" ] && IDENT+=(-c "user.email=$GIT_AUTHOR_EMAIL")
        git -C "$SECRETS_REPO" "${IDENT[@]}" \
            commit -q -m "backup: ${BASE} (${MACHINE})" || true
        if [ "$PUSH" = true ]; then
            if git -C "$SECRETS_REPO" push 2>&1 | tail -1; then
                echo "→ 보관소로 올렸다: bundles/${BASE}.tar.gz.gpg"
            else
                echo "→ push 실패. 나중에 'git -C $SECRETS_REPO push' 로 다시 해라." >&2
            fi
        else
            echo "→ commit 만 했다(--no-push). 'git -C $SECRETS_REPO push' 로 올려라."
        fi
    fi
    echo "키워드는 어디에도 안 적힌다. 잊으면 복호화가 안 된다 — 키워드만 따로 적어 둬라."
else
    echo "→ 이 번들을 저장소 밖(비밀번호 관리자·외장)으로 옮겨라. 또는 --keyword 로 보관소 자동화."
fi
