#!/usr/bin/env bash
# Claude Code statusLine command
# Converted from PS1: \[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$

input=$(cat)
cwd=$(echo "$input" | jq -r '.cwd // .workspace.current_dir // ""')

printf '\033[01;32m%s@%s\033[00m:\033[01;34m%s\033[00m' \
  "$(whoami)" "$(hostname -s)" "$cwd"
