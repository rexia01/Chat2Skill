# OpenChat2Skill

Automatically learn reusable skills from your coding-agent conversations.

After each session, Chat2Skill analyzes the conversation for corrections,
preferences, and constraints, distills them into `SKILL.md` files, and
injects the relevant ones into your future sessions — so your agent stops
repeating the same mistakes.

Works with **Claude Code**, **Codex**, and any agent that supports
prompt/stop hooks or can run a CLI.

## How it works

```
your machine                                Chat2Skill cloud
─────────────────────────────────────       ─────────────────────────
Stop hook ──► queue ──► worker ───────────► POST /v1/extract
                          │                 (stateless algorithm,
   ~/.chat2skill/ ◄───────┘                  your own LLM api key)
   skills + profile + history     ◄──────── skill + profile + replay
                                            POST /v1/project-skill
UserPromptSubmit hook ◄── local retrieval   (project summary)
```

- **Your data stays local.** Skills, profile, and history live in
  `~/.chat2skill/` (SQLite + markdown files). The cloud runs the
  extraction algorithm statelessly and stores nothing.
- **Bring your own key.** Extraction LLM calls use *your* api key
  (OpenAI-compatible, e.g. OpenAI/DeepSeek). The key is sent with each
  request, used in memory, never persisted or logged server-side.
  Without a key, the server falls back to lower-quality heuristics.
- **Cost.** A typical extraction makes ~4 LLM calls on your key
  (detect, analyze, generate, judge); replay validation against your
  history adds up to 5 more. Conversations are windowed (last ~40
  messages) so long sessions stay cheap. Extraction only triggers when
  a correction/constraint signal is detected, not on every session.

## Install

### 1. Configure

```bash
mkdir -p ~/.chat2skill
cp config.example.json ~/.chat2skill/config.json
# edit ~/.chat2skill/config.json: set llm.api_key (and base_url/model)
```

Environment variables override the file: `CHAT2SKILL_API_URL`,
`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `CHAT2SKILL_MODEL`,
`CHAT2SKILL_USER_ID`, `CHAT2SKILL_HOME`.

### 2a. Claude Code

Install as a plugin (marketplace or local path). The manifest at
`.claude-plugin/plugin.json` registers the hooks automatically via
`${CLAUDE_PLUGIN_ROOT}` — no path setup needed.

### 2b. Codex

```bash
git clone https://github.com/chat2skill/openchat2skill ~/plugins/chat2skill
cd ~/plugins/chat2skill && ./install.sh
```

`install.sh` writes `hooks.json` with absolute paths for your clone
location and creates the config file if missing.

### 2c. Other agents

If your agent supports hooks, point them at:
- prompt-submit: `python3 <plugin-root>/scripts/hook_user_prompt_submit.py`
- session-end: `python3 <plugin-root>/scripts/hook_stop.py`

No hooks? Use the CLIs:

```bash
# after a session: learn from the newest transcript
python3 scripts/update_from_transcript.py --latest

# before a task: print a prompt snippet with relevant skills
python3 scripts/retrieve_for_prompt.py "refactor the auth module"
```

## Requirements

- Python 3.10+ (standard library only — no pip installs)
- A Chat2Skill API endpoint (`api_url` in config)
- Optional: an OpenAI-compatible LLM api key for high-quality extraction

## Data layout

```
~/.chat2skill/
├── config.json                  # endpoint + your LLM credentials
├── chat2skill.db                # conversations, skills, profile
├── skills/<user>/<name>/SKILL.md
├── skills/<user>/PROJECT_SKILL.md   # injected before each conversation
└── hook-events.log
```

Skills are namespaced per project (`<user>__project__<slug>`), so what
you learn in one repo doesn't leak into another.

## Privacy

- Conversations are sent to the Chat2Skill API for analysis, processed
  in memory, and not persisted server-side. Server logs contain metadata
  only (session id, error type) — never message content or api keys.
- Agent system prompts, environment banners, and tool noise are stripped
  locally before upload (see `scripts/chat2skill/transcripts.py`).
- To stop all uploads, remove the Stop hook or unset `api_url`.

## License

MIT
