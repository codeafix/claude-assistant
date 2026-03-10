# claude-assistant

Orchestration layer for an autonomous research assistant running on a local machine. Drop a Markdown note into your Obsidian vault and Claude researches it autonomously, writing results back to the vault.

## How it works

1. You drop a `.md` file into `Claude/Research/Requests/` in your Obsidian vault
2. `watcher.py` picks it up within ~2 seconds and passes the file content to Claude Code as a prompt
3. Claude Code runs with MCP access to the vault (`markdown-rag`) and a browser (`playwright`)
4. On success: the request note's frontmatter is stamped with `status: done`, `completed`, and `output: [[topic]]`, then moved to `Claude/Research/Requests/Done/`
5. On failure: the request note's frontmatter is stamped with `status: error` and moved to `Claude/Research/Requests/Error/`; a detailed error note (exit code, stdout, stderr) is written to `Claude/Research/Errors/<topic>.md`

The watcher runs as a persistent launchd agent. By default it processes one request at a time (Playwright requires exclusive access to a Chrome profile); increase `max_concurrency` in `config.yaml` if you configure multiple dedicated profiles.

## Prerequisites

- Python 3.11+
- Node.js 18+
- [Claude Code](https://claude.ai/claude-code) (`claude` available on PATH)
- An Obsidian vault
- The RAG stack running locally (provides the `search_notes` tool)

## Setup

```sh
make bootstrap
```

Or equivalently:

```sh
sh bootstrap.sh
```

`bootstrap.sh` is idempotent — safe to re-run after moving the repo or changing config.

It will:

1. Create `.venv` and install Python dependencies
2. Run `npm install` to pin the Playwright MCP version
3. Prompt for config values (vault path, RAG URL, Chrome profile)
4. Generate `mcp_config.json` and the launchd plist
5. Register and start the watcher as a launchd agent

## Configuration

Values are stored in `config.yaml` (not committed to git). Edit it directly or re-run `make bootstrap` to be prompted again.


| Field            | Description                               | Default                 |
| ---------------- | ----------------------------------------- | ----------------------- |
| `vault_path`     | Absolute path to your Obsidian vault root | —                       |
| `write_vault`    | Vault name for write operations           | `Claude`                |
| `rag_url`        | RAG HTTP API base URL                     | `http://localhost:8000` |
| `chrome_profile` | Chrome profile directory name             | `Profile 2`             |
| `repo_dir`       | Set automatically by bootstrap            | —                       |


After editing `config.yaml`, regenerate the derived configs:

```sh
make bootstrap
```

## Usage

Create a `.md` file in `Claude/Research/Requests/` in your vault with `status: ready` in the YAML frontmatter and your research question or task as the body. Claude Code will pick it up automatically.

```markdown
---
status: ready
---
Research question or task here.
```

Any other frontmatter fields (e.g. `date`, `tags`) are preserved when the note is moved to `Done/` or `Error/`.

On startup, the watcher scans `Requests/` for any notes with `status: ready` in their frontmatter and queues them automatically.

## Makefile targets


| Target           | Description                                                |
| ---------------- | ---------------------------------------------------------- |
| `make bootstrap` | First-time setup: venv, deps, config, launchd registration |
| `make build`     | Install/update Python and Node dependencies only           |
| `make test`      | Run the test suite                                         |
| `make start`     | Load the launchd agent (start the watcher)                 |
| `make stop`      | Unload the launchd agent (stop the watcher)                |
| `make restart`   | Stop then start the watcher                                |
| `make logs`      | Tail both watcher log files                                |
| `make clean`     | Remove venv, node_modules, logs, and generated files       |


## Architecture

```
Obsidian vault
  Claude/Research/Requests/        ← drop request notes here
  Claude/Research/Requests/Done/   ← completed requests (status: done)
  Claude/Research/Requests/Error/  ← failed requests (status: error)
  Claude/Research/<topic>.md       ← Claude writes output here
  Claude/Research/Errors/<topic>.md← detailed error log on failure

watcher.py
  startup scan → queue notes with status: ready
  watchdog Observer → debounce 2s → asyncio.create_subprocess_exec
                                      ↳ claude --mcp-config mcp_config.json ...
  on exit 0:  stamp frontmatter (status/completed/output) → move to Requests/Done/
  on non-zero: stamp frontmatter (status: error) → move to Requests/Error/
               write detailed error note to Claude/Research/Errors/

mcp_server.py  (MCP stdio server — one instance per Claude Code subprocess)
  search_notes         — semantic search via RAG HTTP API
  read/list/create/
  update/delete/lint   — vault file operations via obsidian-mcp-guard
```

## Logs

```
logs/watcher.stdout.log
logs/watcher.stderr.log
```

Tail live:

```sh
make logs
```

## Development

Run tests:

```sh
make test
```

Run the watcher manually (foreground, for debugging):

```sh
.venv/bin/python watcher.py
```

Stop/start the background service:

```sh
make stop
make start
```

