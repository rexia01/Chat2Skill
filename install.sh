#!/usr/bin/env bash
# Generate hooks.json with absolute paths for agents that do not expand
# a plugin-root variable (e.g. Codex). Claude Code users do not need this:
# hooks/hooks.json uses ${CLAUDE_PLUGIN_ROOT} and works as-is.
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cat > "${PLUGIN_ROOT}/hooks.json" <<EOF
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${PLUGIN_ROOT}/scripts/hook_user_prompt_submit.py"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${PLUGIN_ROOT}/scripts/hook_stop.py"
          }
        ]
      }
    ]
  }
}
EOF

echo "Wrote ${PLUGIN_ROOT}/hooks.json"

CONFIG_DIR="${CHAT2SKILL_HOME:-$HOME/.chat2skill}"
CONFIG_FILE="${CONFIG_DIR}/config.json"
if [ ! -f "${CONFIG_FILE}" ]; then
    mkdir -p "${CONFIG_DIR}"
    cp "${PLUGIN_ROOT}/config.example.json" "${CONFIG_FILE}"
    echo "Created ${CONFIG_FILE} — edit it to set your LLM api key."
else
    echo "Config already exists: ${CONFIG_FILE}"
fi
