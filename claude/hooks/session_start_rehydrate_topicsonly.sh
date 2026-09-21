#!/usr/bin/env bash
export REHYDRATE_TOPICS_ONLY=1
exec $HOME/.claude/hooks/session_start_rehydrate.sh "$@"
