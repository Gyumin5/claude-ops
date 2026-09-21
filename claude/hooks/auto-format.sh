#!/bin/bash
# Auto-format files after Write/Edit using prettier (if available)
FILE_PATH=$(jq -r '.tool_input.file_path // empty')

# Skip if no file path or prettier not available
[ -z "$FILE_PATH" ] && exit 0
! command -v npx &>/dev/null && exit 0

# Only format supported file types
case "$FILE_PATH" in
  *.js|*.jsx|*.ts|*.tsx|*.json|*.css|*.scss|*.html|*.md|*.yaml|*.yml)
    npx prettier --write "$FILE_PATH" 2>/dev/null || true
    ;;
esac
exit 0
