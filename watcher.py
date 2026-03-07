#!/usr/bin/env python3
"""
Watches Claude/Research/Requests/ for new .md files and spawns Claude Code
subprocesses to handle each request concurrently.
"""

import asyncio
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import yaml
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 2.0


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with config_path.open() as f:
        return yaml.safe_load(f)


# ── frontmatter helpers ────────────────────────────────────────────────────────

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter. Returns (frontmatter_dict, body_text)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    try:
        fm = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        fm = {}
    body = text[end + 5:]  # everything after the closing "\n---\n"
    return fm, body


def update_frontmatter(text: str, updates: dict) -> str:
    """Merge updates into the note's frontmatter and return the full updated text."""
    fm, body = parse_frontmatter(text)
    fm.update(updates)
    fm_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True).rstrip()
    return f"---\n{fm_yaml}\n---\n{body}"


def get_note_status(path: Path) -> str | None:
    """Return the value of the status frontmatter field, or None if absent."""
    fm, _ = parse_frontmatter(path.read_text())
    return fm.get("status")


# ── request handler ────────────────────────────────────────────────────────────

async def handle_request(request_path: Path, config: dict) -> None:
    vault_path = Path(config["vault_path"])
    repo_dir = Path(config["repo_dir"])
    topic = request_path.stem

    log.info("Handling request: %s", request_path.name)

    content = request_path.read_text()

    cmd = [
        "claude",
        "--mcp-config", str(repo_dir / "mcp_config.json"),
        "--allowedTools", "mcp__markdown-rag__*,mcp__playwright__*",
        "-p", content,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(repo_dir),
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")

    now = datetime.now(timezone.utc).isoformat()

    if proc.returncode == 0:
        log.info("Request succeeded: %s", topic)
        done_dir = vault_path / "Claude" / "Research" / "Requests" / "Done"
        done_dir.mkdir(parents=True, exist_ok=True)
        updated = update_frontmatter(content, {
            "status": "done",
            "completed": now,
            "output": f"[[{topic}]]",
        })
        request_path.write_text(updated)
        request_path.rename(done_dir / request_path.name)
    else:
        log.error("Request failed: %s (exit %d)", topic, proc.returncode)

        # Update request note frontmatter and move to Requests/Error/
        error_requests_dir = vault_path / "Claude" / "Research" / "Requests" / "Error"
        error_requests_dir.mkdir(parents=True, exist_ok=True)
        updated = update_frontmatter(content, {"status": "error"})
        request_path.write_text(updated)
        request_path.rename(error_requests_dir / request_path.name)

        # Write a detailed error note to Claude/Research/Errors/
        error_dir = vault_path / "Claude" / "Research" / "Errors"
        error_dir.mkdir(parents=True, exist_ok=True)
        error_note = error_dir / f"{topic}.md"
        error_note.write_text(
            f"---\n"
            f"date: {now}\n"
            f"status: error\n"
            f"request: {request_path}\n"
            f"---\n\n"
            f"# Error: {topic}\n\n"
            f"Claude Code exited with code {proc.returncode}.\n\n"
            f"## stdout\n\n{stdout}\n\n"
            f"## stderr\n\n{stderr}\n"
        )


# ── file watcher ───────────────────────────────────────────────────────────────

class RequestHandler(FileSystemEventHandler):
    def __init__(self, config: dict, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._config = config
        self._loop = loop
        self._timers: dict[Path, threading.Timer] = {}
        self._lock = threading.Lock()

    def _schedule(self, path: Path) -> None:
        with self._lock:
            existing = self._timers.get(path)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(
                DEBOUNCE_SECONDS,
                lambda: asyncio.run_coroutine_threadsafe(
                    handle_request(path, self._config), self._loop
                ),
            )
            self._timers[path] = timer
            timer.start()

    def on_created(self, event) -> None:
        if not event.is_directory and event.src_path.endswith(".md"):
            self._schedule(Path(event.src_path))

    def on_closed(self, event) -> None:
        if not event.is_directory and event.src_path.endswith(".md"):
            self._schedule(Path(event.src_path))


def main() -> None:
    config = load_config()
    vault_path = Path(config["vault_path"])
    watch_dir = vault_path / "Claude" / "Research" / "Requests"
    watch_dir.mkdir(parents=True, exist_ok=True)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Startup scan — queue any pending notes that were not yet processed.
    # Skip notes that already have status: done or status: error.
    for note_path in sorted(watch_dir.glob("*.md")):
        status = get_note_status(note_path)
        if status in ("done", "error"):
            log.info("Skipping already-processed note: %s (status: %s)", note_path.name, status)
            continue
        log.info("Startup: queuing pending note: %s", note_path.name)
        loop.create_task(handle_request(note_path, config))

    handler = RequestHandler(config, loop)
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=False)
    observer.start()
    log.info("Watching %s", watch_dir)

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        loop.close()


if __name__ == "__main__":
    main()
