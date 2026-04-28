#!/usr/bin/env bash
# One-shot diagnostic for the WSL claude CLI auth setup.
# Run via: wsl -d Ubuntu-22.04 -- bash -l /mnt/c/Users/abero/dev/Alpha4Gate/scripts/check_claude_auth.sh
set -u
echo "=== ~/.profile last 6 lines ==="
tail -6 ~/.profile
echo
echo "=== env vars in this login shell ==="
echo "  CLAUDE_CODE_OAUTH_TOKEN length: ${#CLAUDE_CODE_OAUTH_TOKEN}"
echo "  SC2_WSL_DETECT: ${SC2_WSL_DETECT:-<unset>}"
echo "  PATH contains ~/.local/bin: $(echo "$PATH" | grep -c '\.local/bin')"
echo
echo "=== claude binary ==="
which claude
claude --version
echo
echo "=== claude -p smoke (cheap call to verify auth) ==="
echo "say hi in 5 words" | claude -p --model haiku --output-format text --no-session-persistence 2>&1 | head -5
