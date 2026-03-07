"""Tests for watcher.py"""
import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import watcher


# ── helpers ───────────────────────────────────────────────────────────────────

def make_config(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    return {
        "vault_path": str(vault),
        "repo_dir": str(tmp_path / "repo"),
    }


def make_proc(returncode=0, stdout=b"", stderr=b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


# ── parse_frontmatter ─────────────────────────────────────────────────────────

def test_parse_frontmatter_returns_dict_and_body():
    text = "---\nstatus: pending\ntitle: My Note\n---\n\nBody text here.\n"
    fm, body = watcher.parse_frontmatter(text)
    assert fm == {"status": "pending", "title": "My Note"}
    assert body == "\nBody text here.\n"


def test_parse_frontmatter_no_frontmatter_returns_empty_dict():
    text = "# Just a heading\n\nSome content.\n"
    fm, body = watcher.parse_frontmatter(text)
    assert fm == {}
    assert body == text


def test_parse_frontmatter_preserves_body_content():
    text = "---\nstatus: done\n---\n\n# Heading\n\nParagraph.\n"
    _, body = watcher.parse_frontmatter(text)
    assert body == "\n# Heading\n\nParagraph.\n"


def test_parse_frontmatter_unclosed_block_returns_empty():
    text = "---\nstatus: pending\n"
    fm, body = watcher.parse_frontmatter(text)
    assert fm == {}
    assert body == text


# ── update_frontmatter ────────────────────────────────────────────────────────

def test_update_frontmatter_merges_new_fields():
    text = "---\ntitle: My Note\n---\n\nBody.\n"
    result = watcher.update_frontmatter(text, {"status": "done"})
    fm, body = watcher.parse_frontmatter(result)
    assert fm["title"] == "My Note"
    assert fm["status"] == "done"
    assert body == "\nBody.\n"


def test_update_frontmatter_overwrites_existing_field():
    text = "---\nstatus: pending\n---\n\nBody.\n"
    result = watcher.update_frontmatter(text, {"status": "done"})
    fm, _ = watcher.parse_frontmatter(result)
    assert fm["status"] == "done"


def test_update_frontmatter_creates_frontmatter_when_absent():
    text = "# Heading\n\nBody.\n"
    result = watcher.update_frontmatter(text, {"status": "error"})
    assert result.startswith("---\n")
    fm, _ = watcher.parse_frontmatter(result)
    assert fm["status"] == "error"


def test_update_frontmatter_preserves_body():
    text = "---\nstatus: pending\n---\n\n# My Topic\n\nThe question.\n"
    result = watcher.update_frontmatter(text, {"status": "done"})
    _, body = watcher.parse_frontmatter(result)
    assert body == "\n# My Topic\n\nThe question.\n"


# ── get_note_status ───────────────────────────────────────────────────────────

def test_get_note_status_returns_status_field(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("---\nstatus: done\n---\n\nBody.\n")
    assert watcher.get_note_status(note) == "done"


def test_get_note_status_returns_none_when_absent(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("# No frontmatter\n")
    assert watcher.get_note_status(note) is None


def test_get_note_status_returns_none_for_empty_frontmatter(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("---\ntitle: Only Title\n---\n\nBody.\n")
    assert watcher.get_note_status(note) is None


# ── handle_request: success path ──────────────────────────────────────────────

def test_handle_request_success_moves_to_done_dir(tmp_path):
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    request_file = vault / "my-topic.md"
    request_file.write_text("Research question here")

    with patch("watcher.asyncio.create_subprocess_exec", AsyncMock(return_value=make_proc(returncode=0))):
        asyncio.run(watcher.handle_request(request_file, config))

    done_dir = vault / "Claude" / "Research" / "Requests" / "Done"
    assert not request_file.exists()
    assert (done_dir / "my-topic.md").exists()


def test_handle_request_success_sets_status_done(tmp_path):
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    request_file = vault / "my-topic.md"
    request_file.write_text("Research question here")

    with patch("watcher.asyncio.create_subprocess_exec", AsyncMock(return_value=make_proc())):
        asyncio.run(watcher.handle_request(request_file, config))

    moved = vault / "Claude" / "Research" / "Requests" / "Done" / "my-topic.md"
    fm, _ = watcher.parse_frontmatter(moved.read_text())
    assert fm["status"] == "done"


def test_handle_request_success_sets_completed_timestamp(tmp_path):
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    request_file = vault / "my-topic.md"
    request_file.write_text("Research question here")

    with patch("watcher.asyncio.create_subprocess_exec", AsyncMock(return_value=make_proc())):
        asyncio.run(watcher.handle_request(request_file, config))

    moved = vault / "Claude" / "Research" / "Requests" / "Done" / "my-topic.md"
    fm, _ = watcher.parse_frontmatter(moved.read_text())
    assert "completed" in fm
    assert fm["completed"]  # non-empty


def test_handle_request_success_sets_output_wikilink(tmp_path):
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    request_file = vault / "my-topic.md"
    request_file.write_text("Research question here")

    with patch("watcher.asyncio.create_subprocess_exec", AsyncMock(return_value=make_proc())):
        asyncio.run(watcher.handle_request(request_file, config))

    moved = vault / "Claude" / "Research" / "Requests" / "Done" / "my-topic.md"
    fm, _ = watcher.parse_frontmatter(moved.read_text())
    assert fm["output"] == "[[my-topic]]"


def test_handle_request_success_preserves_existing_frontmatter(tmp_path):
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    request_file = vault / "my-topic.md"
    request_file.write_text("---\ndate: 2026-01-01\ntags: [research]\n---\n\nQuestion.\n")

    with patch("watcher.asyncio.create_subprocess_exec", AsyncMock(return_value=make_proc())):
        asyncio.run(watcher.handle_request(request_file, config))

    moved = vault / "Claude" / "Research" / "Requests" / "Done" / "my-topic.md"
    fm, _ = watcher.parse_frontmatter(moved.read_text())
    import datetime
    assert fm["date"] == datetime.date(2026, 1, 1)
    assert fm["status"] == "done"


def test_handle_request_success_builds_correct_command(tmp_path):
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    request_file = vault / "my-topic.md"
    request_file.write_text("Research question here")

    with patch("watcher.asyncio.create_subprocess_exec", AsyncMock(return_value=make_proc())) as mock_exec:
        asyncio.run(watcher.handle_request(request_file, config))

    args = mock_exec.call_args.args
    assert args[0] == "claude"
    assert "--mcp-config" in args
    assert "-p" in args
    assert args[args.index("-p") + 1] == "Research question here"


def test_handle_request_success_passes_mcp_config_from_repo_dir(tmp_path):
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    request_file = vault / "topic.md"
    request_file.write_text("Q")

    with patch("watcher.asyncio.create_subprocess_exec", AsyncMock(return_value=make_proc())) as mock_exec:
        asyncio.run(watcher.handle_request(request_file, config))

    args = mock_exec.call_args.args
    mcp_config_arg = args[args.index("--mcp-config") + 1]
    assert mcp_config_arg == str(Path(config["repo_dir"]) / "mcp_config.json")


# ── handle_request: failure path ──────────────────────────────────────────────

def test_handle_request_failure_moves_to_error_dir(tmp_path):
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    request_file = vault / "my-topic.md"
    request_file.write_text("Research question")

    proc = make_proc(returncode=1, stderr=b"crash")
    with patch("watcher.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        asyncio.run(watcher.handle_request(request_file, config))

    error_requests_dir = vault / "Claude" / "Research" / "Requests" / "Error"
    assert not request_file.exists()
    assert (error_requests_dir / "my-topic.md").exists()


def test_handle_request_failure_sets_status_error_in_request_note(tmp_path):
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    request_file = vault / "my-topic.md"
    request_file.write_text("Research question")

    proc = make_proc(returncode=1)
    with patch("watcher.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        asyncio.run(watcher.handle_request(request_file, config))

    moved = vault / "Claude" / "Research" / "Requests" / "Error" / "my-topic.md"
    fm, _ = watcher.parse_frontmatter(moved.read_text())
    assert fm["status"] == "error"


def test_handle_request_failure_still_writes_error_note(tmp_path):
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    request_file = vault / "my-topic.md"
    request_file.write_text("Research question")

    proc = make_proc(returncode=1, stdout=b"some output", stderr=b"some error")
    with patch("watcher.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        asyncio.run(watcher.handle_request(request_file, config))

    error_note = vault / "Claude" / "Research" / "Errors" / "my-topic.md"
    assert error_note.exists()
    content = error_note.read_text()
    assert "status: error" in content
    assert "exited with code 1" in content
    assert "some output" in content
    assert "some error" in content


def test_handle_request_failure_error_note_has_frontmatter_and_sections(tmp_path):
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    request_file = vault / "my-topic.md"
    request_file.write_text("Q")

    proc = make_proc(returncode=3, stdout=b"out", stderr=b"err")
    with patch("watcher.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        asyncio.run(watcher.handle_request(request_file, config))

    content = (vault / "Claude" / "Research" / "Errors" / "my-topic.md").read_text()
    assert content.startswith("---\n")
    assert "# Error: my-topic" in content
    assert "## stdout" in content
    assert "## stderr" in content


def test_handle_request_failure_error_note_includes_request_path(tmp_path):
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    request_file = vault / "my-topic.md"
    request_file.write_text("Q")

    proc = make_proc(returncode=1)
    with patch("watcher.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        asyncio.run(watcher.handle_request(request_file, config))

    content = (vault / "Claude" / "Research" / "Errors" / "my-topic.md").read_text()
    assert str(request_file) in content


# ── startup scan: get_note_status filtering ───────────────────────────────────

def test_startup_scan_skips_done_notes(tmp_path):
    """Notes with status: done must not be passed to handle_request."""
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    watch_dir = vault / "Claude" / "Research" / "Requests"
    watch_dir.mkdir(parents=True)

    done_note = watch_dir / "already-done.md"
    done_note.write_text("---\nstatus: done\n---\n\nQuestion.\n")
    pending_note = watch_dir / "pending.md"
    pending_note.write_text("---\nstatus: pending\n---\n\nQuestion.\n")

    processed = []

    async def fake_handle(path, cfg):
        processed.append(path.name)

    with (
        patch("watcher.load_config", return_value=config),
        patch("watcher.handle_request", side_effect=fake_handle),
        patch("watcher.Observer") as mock_observer_cls,
    ):
        mock_observer = MagicMock()
        mock_observer_cls.return_value = mock_observer
        # Make loop.run_forever() stop immediately
        loop = asyncio.new_event_loop()
        with patch("watcher.asyncio.new_event_loop", return_value=loop):
            loop.call_soon(loop.stop)
            watcher.main()

    assert "already-done.md" not in processed
    assert "pending.md" in processed


def test_startup_scan_skips_error_notes(tmp_path):
    """Notes with status: error must not be passed to handle_request."""
    config = make_config(tmp_path)
    vault = Path(config["vault_path"])
    watch_dir = vault / "Claude" / "Research" / "Requests"
    watch_dir.mkdir(parents=True)

    error_note = watch_dir / "failed.md"
    error_note.write_text("---\nstatus: error\n---\n\nQuestion.\n")

    processed = []

    async def fake_handle(path, cfg):
        processed.append(path.name)

    with (
        patch("watcher.load_config", return_value=config),
        patch("watcher.handle_request", side_effect=fake_handle),
        patch("watcher.Observer") as mock_observer_cls,
    ):
        mock_observer = MagicMock()
        mock_observer_cls.return_value = mock_observer
        loop = asyncio.new_event_loop()
        with patch("watcher.asyncio.new_event_loop", return_value=loop):
            loop.call_soon(loop.stop)
            watcher.main()

    assert "failed.md" not in processed


# ── RequestHandler: event filtering ───────────────────────────────────────────

def _make_handler():
    config = {"vault_path": "/vault", "repo_dir": "/repo"}
    loop = asyncio.new_event_loop()
    return watcher.RequestHandler(config, loop), loop


def test_handler_schedules_md_file_on_created():
    handler, loop = _make_handler()
    try:
        with patch.object(handler, "_schedule") as mock_schedule:
            event = MagicMock(is_directory=False, src_path="/vault/Requests/topic.md")
            handler.on_created(event)
            mock_schedule.assert_called_once_with(Path("/vault/Requests/topic.md"))
    finally:
        loop.close()


def test_handler_schedules_md_file_on_closed():
    handler, loop = _make_handler()
    try:
        with patch.object(handler, "_schedule") as mock_schedule:
            event = MagicMock(is_directory=False, src_path="/vault/Requests/topic.md")
            handler.on_closed(event)
            mock_schedule.assert_called_once_with(Path("/vault/Requests/topic.md"))
    finally:
        loop.close()


def test_handler_ignores_non_md_files():
    handler, loop = _make_handler()
    try:
        with patch.object(handler, "_schedule") as mock_schedule:
            event = MagicMock(is_directory=False, src_path="/vault/Requests/note.txt")
            handler.on_created(event)
            mock_schedule.assert_not_called()
    finally:
        loop.close()


def test_handler_ignores_directories():
    handler, loop = _make_handler()
    try:
        with patch.object(handler, "_schedule") as mock_schedule:
            event = MagicMock(is_directory=True, src_path="/vault/Requests/subdir")
            handler.on_created(event)
            mock_schedule.assert_not_called()
    finally:
        loop.close()


# ── RequestHandler: debounce ──────────────────────────────────────────────────

def test_debounce_cancels_previous_timer_for_same_path():
    handler, loop = _make_handler()
    path = Path("/vault/Requests/topic.md")

    timer1 = MagicMock(spec=threading.Timer)
    timer2 = MagicMock(spec=threading.Timer)

    try:
        with patch("watcher.threading.Timer", side_effect=[timer1, timer2]):
            handler._schedule(path)
            handler._schedule(path)

        timer1.start.assert_called_once()
        timer1.cancel.assert_called_once()
        timer2.start.assert_called_once()
        timer2.cancel.assert_not_called()
    finally:
        loop.close()


def test_debounce_independent_paths_do_not_interfere():
    handler, loop = _make_handler()
    path_a = Path("/vault/Requests/topic-a.md")
    path_b = Path("/vault/Requests/topic-b.md")

    timer_a = MagicMock(spec=threading.Timer)
    timer_b = MagicMock(spec=threading.Timer)

    try:
        with patch("watcher.threading.Timer", side_effect=[timer_a, timer_b]):
            handler._schedule(path_a)
            handler._schedule(path_b)

        timer_a.cancel.assert_not_called()
        timer_b.cancel.assert_not_called()
        timer_a.start.assert_called_once()
        timer_b.start.assert_called_once()
    finally:
        loop.close()
