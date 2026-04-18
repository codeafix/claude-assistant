#!/usr/bin/env python3
"""
Watches Claude/Research/Requests/ for new .md files and spawns Claude Code
subprocesses to handle each request concurrently.
"""

import asyncio
import json
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import yaml
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
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

_in_flight: set[Path] = set()

# Semaphore limiting concurrent Claude subprocesses. Playwright attaches to a
# persistent Chrome profile, so only one browser session can be active at a
# time. Increase max_concurrency in config.yaml only if you have multiple
# dedicated Chrome profiles configured for Playwright.
_semaphore: asyncio.Semaphore | None = None


def _get_semaphore(config: dict) -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        limit = config.get("max_concurrency", 1)
        _semaphore = asyncio.Semaphore(limit)
    return _semaphore


async def handle_request(request_path: Path, config: dict) -> None:
    if request_path in _in_flight:
        log.debug("Skipping %s (already in flight)", request_path.name)
        return
    _in_flight.add(request_path)
    try:
        async with _get_semaphore(config):
            await _do_handle_request(request_path, config)
    finally:
        _in_flight.discard(request_path)


_TRANSIENT_ERROR_PATTERNS = [
    "stream idle timeout",
    "partial response received",
    "connection reset",
    "overloaded",
]


def _is_transient_error(stdout: str, stderr: str) -> bool:
    combined = (stdout + stderr).lower()
    return any(p in combined for p in _TRANSIENT_ERROR_PATTERNS)


def _log_stream_event(topic: str, line: str) -> None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        if line.strip():
            log.info("[%s] %s", topic, line)
        return

    etype = event.get("type", "")
    if etype == "system" and event.get("subtype") == "init":
        tools = [t.get("name", "?") if isinstance(t, dict) else str(t) for t in event.get("tools", [])]
        log.info("[%s] session init — tools: %s", topic, ", ".join(tools) or "none")
    elif etype == "assistant":
        for block in event.get("message", {}).get("content", []):
            btype = block.get("type")
            if btype == "text":
                text = block["text"].strip()
                if text:
                    log.info("[%s] %s", topic, text[:300])
            elif btype == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                detail = next(iter(inp.values()), "") if inp else ""
                if isinstance(detail, str):
                    detail = detail[:120]
                log.info("[%s] → %s  %s", topic, name, detail)
    elif etype == "result":
        subtype = event.get("subtype", "?")
        cost = event.get("cost_usd")
        cost_str = f"  cost=${cost:.4f}" if cost is not None else ""
        log.info("[%s] result: %s%s", topic, subtype, cost_str)


def _extract_result_text(stdout_lines: list[str]) -> str:
    """Return the final result text from stream-json output, or join raw lines."""
    for line in reversed(stdout_lines):
        try:
            event = json.loads(line)
            if event.get("type") == "result" and event.get("subtype") == "success":
                return event.get("result", "").strip()
        except json.JSONDecodeError:
            continue
    return "\n".join(stdout_lines).strip()


async def _drain_stderr(stream: asyncio.StreamReader) -> str:
    chunks = []
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        chunks.append(chunk.decode(errors="replace"))
    return "".join(chunks)


async def _do_handle_request(request_path: Path, config: dict) -> None:
    vault_path = Path(config["vault_path"])
    repo_dir = Path(config["repo_dir"])
    topic = request_path.stem

    content = request_path.read_text()
    fm, _ = parse_frontmatter(content)
    if fm.get("status") != "ready":
        log.debug("Skipping %s (status: %s)", request_path.name, fm.get("status"))
        return

    log.info("Handling request: %s", request_path.name)

    max_retries = config.get("max_retries", 3)
    retry_delay = config.get("retry_delay_seconds", 10)

    system_prompt = (
        "You are a research assistant. Your job is to fully research the topic in the request note below "
        "and write a detailed, well-structured Markdown report.\n\n"
        "You have two categories of tools:\n"
        "1. mcp__playwright__* — a real browser. Use it to search the web (e.g. navigate to "
        "https://www.google.com or https://search.brave.com, search for the topic, follow promising "
        "links, and extract the content you need). Always do web research unless the request "
        "explicitly says not to.\n"
        "2. mcp__markdown-rag__search_notes — semantic search over the user's personal Obsidian vault. "
        "Use this to find relevant personal notes, past research, or context before or alongside web search.\n\n"
        "Write your final report to the vault using mcp__markdown-rag__create_note or "
        "mcp__markdown-rag__update_note at path Claude/Research/<topic>.md. "
        "The report should synthesise what you found, cite sources with URLs, and be ready to read.\n\n"
        "Follow the vault's Markdown conventions: use the Claude/conventions.md note in the vault root if present, "
        "and refer to any vault-level style or formatting guidelines found there when structuring your output."
    )

    cmd = [
        "claude",
        "--mcp-config", str(repo_dir / "mcp_config.json"),
        "--allowedTools", "mcp__markdown-rag__*,mcp__playwright__*",
        "--system-prompt", system_prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--print",
    ]

    stdout = ""
    stderr = ""
    returncode = 1
    for attempt in range(1, max_retries + 1):
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(repo_dir),
            limit=2 ** 23,  # 8 MB — stream-json lines can be large
        )

        proc.stdin.write(content.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        stderr_task = asyncio.create_task(_drain_stderr(proc.stderr))

        stdout_lines: list[str] = []
        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").rstrip()
            stdout_lines.append(line)
            try:
                _log_stream_event(topic, line)
            except Exception as exc:
                log.warning("[%s] log error: %s", topic, exc)

        await proc.wait()
        stderr = await stderr_task
        stdout = "\n".join(stdout_lines)
        returncode = proc.returncode

        if returncode == 0:
            break
        if _is_transient_error(stdout, stderr) and attempt < max_retries:
            log.warning(
                "Transient error on attempt %d/%d for %s — retrying in %ds",
                attempt, max_retries, topic, retry_delay,
            )
            await asyncio.sleep(retry_delay)
        else:
            break

    now = datetime.now(timezone.utc).isoformat()

    if returncode == 0:
        log.info("Request succeeded: %s", topic)
        done_dir = vault_path / "Claude" / "Research" / "Requests" / "Done"
        done_dir.mkdir(parents=True, exist_ok=True)
        updated = update_frontmatter(content, {
            "status": "done",
            "completed": now,
            "output": f"[[{topic}]]",
        })
        done_path = done_dir / request_path.name
        request_path.write_text(updated)
        request_path.rename(done_path)
        result_text = _extract_result_text(stdout_lines)
        if result_text:
            with done_path.open("a") as f:
                f.write(f"\n---\n\n{result_text}\n")
    else:
        log.error("Request failed: %s (exit %d)", topic, returncode)

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
            f"Claude Code exited with code {returncode}.\n\n"
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

    def on_modified(self, event) -> None:
        if not event.is_directory and event.src_path.endswith(".md"):
            self._schedule(Path(event.src_path))

    def on_closed(self, event) -> None:
        if not event.is_directory and event.src_path.endswith(".md"):
            self._schedule(Path(event.src_path))

    def on_moved(self, event) -> None:
        if not event.is_directory and event.dest_path.endswith(".md"):
            self._schedule(Path(event.dest_path))


def main() -> None:
    config = load_config()
    vault_path = Path(config["vault_path"])
    watch_dir = vault_path / "Claude" / "Research" / "Requests"
    watch_dir.mkdir(parents=True, exist_ok=True)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Startup scan — queue any notes with status: ready.
    for note_path in sorted(watch_dir.glob("*.md")):
        status = get_note_status(note_path)
        if status == "ready":
            log.info("Startup: queuing pending note: %s", note_path.name)
            loop.create_task(handle_request(note_path, config))
        else:
            log.debug("Startup: skipping %s (status: %s)", note_path.name, status)

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
