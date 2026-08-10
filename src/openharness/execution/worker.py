"""Small JSON-in/JSON-out filesystem worker run inside an OS sandbox."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

_MAX_READ_BYTES = 10 * 1024 * 1024
_MAX_SEARCH_BYTES = 8 * 1024 * 1024
_MAX_SEARCH_FILE_BYTES = 10 * 1024 * 1024
_STREAM_CHUNK_BYTES = 64 * 1024
_RG_SEARCH_TIMEOUT_SECONDS = 0.5
_PYTHON_SEARCH_TIMEOUT_SECONDS = 5.0


class _SearchFallbackTimedOut(Exception):
    """The in-process search exceeded its bounded recovery window."""


def _success(output: str, **metadata: object) -> dict[str, object]:
    return {"output": output, "is_error": False, "metadata": metadata}


def _failure(output: str) -> dict[str, object]:
    return {"output": output, "is_error": True, "metadata": {}}


def _permission_violation(kind: str, request: dict[str, Any]) -> dict[str, object]:
    dimension = {
        "read": "filesystem.read",
        "search": "filesystem.search",
        "write": "filesystem.write",
        "edit": "filesystem.write",
    }[kind]
    return {
        "output": f"sandbox boundary violation ({dimension}): {request.get('path', '')}",
        "is_error": True,
        "metadata": {
            "boundary_violation": {
                "dimension": dimension,
                "requested": str(request.get("path", "")),
                "evidence": "OS sandbox denied the filesystem operation",
                "hard_deny": False,
            }
        },
    }


def _read(request: dict[str, Any]) -> dict[str, object]:
    path = Path(request["path"])
    if not path.exists():
        return _failure(f"file not found: {path}")
    if not path.is_file():
        return _failure(f"not a regular file: {path}")
    size = path.stat().st_size
    if size > _MAX_READ_BYTES:
        return _failure(
            f"file too large: {size} bytes (limit {_MAX_READ_BYTES} bytes); "
            "use Grep for large files"
        )
    if size == 0:
        return _success("(empty)", size_bytes=0)
    text = path.read_text(encoding="utf-8", errors="replace")
    offset = request.get("offset")
    limit = request.get("limit")
    if offset is not None or limit is not None:
        lines = text.splitlines(keepends=True)
        start = int(offset) - 1 if offset is not None else 0
        end = start + int(limit) if limit is not None else len(lines)
        text = "".join(lines[start:end])
    return _success(text, size_bytes=size)


def _write(request: dict[str, Any]) -> dict[str, object]:
    path = Path(request["path"])
    if path.exists() and path.is_dir():
        return _failure(f"cannot overwrite directory: {path}")
    if not path.parent.exists():
        return _failure(f"parent directory does not exist: {path.parent}")
    encoded = str(request["content"]).encode("utf-8")
    path.write_bytes(encoded)
    return _success(
        f"wrote {len(encoded)} bytes to {path}",
        bytes_written=len(encoded),
        path=str(path),
    )


def _atomic_write(path: Path, content: str, *, temp_path: Path | None = None) -> None:
    if temp_path is None:
        fd, raw_temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temp = Path(raw_temp)
    else:
        temp = temp_path
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _edit(request: dict[str, Any]) -> dict[str, object]:
    path = Path(request["path"])
    if not path.exists():
        return _failure(f"file not found: {path}")
    if not path.is_file():
        return _failure(f"not a regular file: {path}")
    try:
        original = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _failure(f"file is not valid UTF-8: {path}")
    old = str(request["old_str"])
    if old not in original:
        return _failure(f"old_str not found in {path}; no replacement made")
    replace_all = bool(request.get("replace_all", False))
    count = original.count(old) if replace_all else 1
    updated = original.replace(old, str(request["new_str"]), -1 if replace_all else 1)
    raw_temp_path = request.get("temp_path")
    temp_path = Path(str(raw_temp_path)) if raw_temp_path is not None else None
    _atomic_write(path, updated, temp_path=temp_path)
    return _success(
        f"replaced {count} occurrence(s) in {path}",
        replacements=count,
        path=str(path),
    )


def _search(request: dict[str, Any]) -> dict[str, object]:
    command = ["rg", "--line-number", "--color=never"]
    if request.get("ignore_case"):
        command.append("-i")
    if request.get("hidden"):
        command.append("--hidden")
    if request.get("glob") is not None:
        command.extend(["--glob", str(request["glob"])])
    command.extend([str(request["pattern"]), str(request["path"])])
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except PermissionError:
        # The target search happens inside rg. A PermissionError raised here
        # therefore means the worker could not launch the rg executable; it
        # is not evidence that the requested search path sits outside the
        # filesystem boundary. Misclassifying it as a path violation creates
        # an exact permission request that cannot fix the underlying runtime
        # restriction (dogfood: allowed workspace Grep parked forever).
        return _search_with_python(request)
    try:
        output, byte_truncated, returncode = _collect_bounded_output(
            process,
            max_bytes=_MAX_SEARCH_BYTES,
            timeout_seconds=_RG_SEARCH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return _search_with_python(request)
    if returncode == 1:
        return _success("(no matches)", match_count=0)
    if returncode != 0:
        error = output.decode("utf-8", errors="replace").strip()
        return _failure(f"rg failed: {error}")
    lines = output.decode("utf-8", errors="replace").splitlines()
    total = len(lines)
    line_cap = int(request["line_cap"])
    if total > line_cap:
        lines = [
            *lines[:line_cap],
            f"... [truncated to {line_cap} lines; total matches {total}]",
        ]
    if byte_truncated:
        lines.append(f"... [output truncated at {_MAX_SEARCH_BYTES} bytes]")
    return _success(
        "\n".join(lines),
        match_count=total,
        byte_truncated=byte_truncated,
    )


def _search_with_python(request: dict[str, Any]) -> dict[str, object]:
    """Preserve Grep when a nested sandbox cannot launch ``rg``.

    Files are still opened by this already-sandboxed worker, so the OS
    boundary remains authoritative. A target-path denial continues to surface
    as ``filesystem.search`` through ``run``'s PermissionError handler.
    """
    try:
        expression = re.compile(
            str(request["pattern"]),
            re.IGNORECASE if request.get("ignore_case") else 0,
        )
    except re.error as exc:
        return _failure(f"search pattern is not a valid regular expression: {exc}")

    root = Path(request["path"])
    glob = str(request["glob"]) if request.get("glob") is not None else None
    include_hidden = bool(request.get("hidden"))
    line_cap = int(request["line_cap"])
    deadline = time.monotonic() + _PYTHON_SEARCH_TIMEOUT_SECONDS
    ignored_directories = _gitignored_directory_patterns(root)
    retained: list[str] = []
    retained_bytes = 0
    byte_truncated = False
    total = 0

    try:
        paths = _iter_search_files(
            root,
            include_hidden=include_hidden,
            ignored_directories=ignored_directories,
            deadline=deadline,
        )
        for path in paths:
            _raise_if_search_expired(deadline)
            relative = path.name if root.is_file() else path.relative_to(root).as_posix()
            if glob is not None and not _glob_matches(relative, glob):
                continue
            try:
                if path.stat().st_size > _MAX_SEARCH_FILE_BYTES:
                    continue
            except FileNotFoundError:
                continue
            with path.open("rb") as stream:
                raw = stream.read()
            if b"\0" in raw:
                continue
            for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                if expression.search(line) is None:
                    continue
                total += 1
                if len(retained) >= line_cap:
                    continue
                rendered = f"{path}:{number}:{line}"
                encoded_size = len(rendered.encode("utf-8")) + 1
                if retained_bytes + encoded_size > _MAX_SEARCH_BYTES:
                    byte_truncated = True
                    continue
                retained.append(rendered)
                retained_bytes += encoded_size
    except _SearchFallbackTimedOut:
        return _failure(
            f"search timed out after {_PYTHON_SEARCH_TIMEOUT_SECONDS:g}s "
            "after ripgrep could not complete inside the sandbox"
        )

    if total == 0:
        return _success(
            "(no matches)",
            match_count=0,
            byte_truncated=False,
            search_engine="python-fallback",
        )
    if total > line_cap:
        retained.append(f"... [truncated to {line_cap} lines; total matches {total}]")
    if byte_truncated:
        retained.append(f"... [output truncated at {_MAX_SEARCH_BYTES} bytes]")
    return _success(
        "\n".join(retained),
        match_count=total,
        byte_truncated=byte_truncated,
        search_engine="python-fallback",
    )


def _iter_search_files(
    root: Path,
    *,
    include_hidden: bool,
    ignored_directories: tuple[str, ...],
    deadline: float,
) -> Iterator[Path]:
    if root.is_file():
        if include_hidden or not root.name.startswith("."):
            yield root
        return

    pending = [root]
    while pending:
        _raise_if_search_expired(deadline)
        directory = pending.pop()
        child_directories: list[Path] = []
        with os.scandir(directory) as entries:
            for entry in entries:
                _raise_if_search_expired(deadline)
                if not include_hidden and entry.name.startswith("."):
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    relative = path.relative_to(root).as_posix()
                    if not _matches_ignored_directory(relative, ignored_directories):
                        child_directories.append(path)
                elif entry.is_file(follow_symlinks=False):
                    yield path
        pending.extend(reversed(child_directories))


def _gitignored_directory_patterns(root: Path) -> tuple[str, ...]:
    if not root.is_dir():
        return ()
    ignore_file = root / ".gitignore"
    try:
        lines = ignore_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ()
    return tuple(
        line.strip()
        for line in lines
        if line.strip().endswith("/") and not line.lstrip().startswith(("#", "!"))
    )


def _matches_ignored_directory(relative: str, patterns: tuple[str, ...]) -> bool:
    name = relative.rsplit("/", 1)[-1]
    for raw_pattern in patterns:
        pattern = raw_pattern.lstrip("/").rstrip("/")
        if "/" not in pattern:
            if fnmatch.fnmatchcase(name, pattern):
                return True
        elif fnmatch.fnmatchcase(relative, pattern) or relative.startswith(f"{pattern}/"):
            return True
    return False


def _raise_if_search_expired(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise _SearchFallbackTimedOut


def _glob_matches(relative: str, pattern: str) -> bool:
    if fnmatch.fnmatchcase(relative, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatchcase(relative, pattern[3:])


def _collect_bounded_output(
    process: subprocess.Popen[bytes],
    *,
    max_bytes: int,
    timeout_seconds: float | None = None,
) -> tuple[bytes, bool, int]:
    """Drain a child concurrently while enforcing a wall-clock deadline.

    Waiting and draining must happen in parallel: waiting first can deadlock
    when the pipe fills, while reading first can block forever when ``rg`` is
    alive but parked by the nested sandbox. Only ``max_bytes`` are retained;
    the reader continues draining excess output so the child can exit.
    """
    stdout = process.stdout
    if stdout is None:
        raise subprocess.SubprocessError("search process has no output pipe")
    kept = bytearray()
    truncated = False
    reader_errors: list[BaseException] = []

    def _drain() -> None:
        nonlocal truncated
        try:
            while chunk := stdout.read(_STREAM_CHUNK_BYTES):
                remaining = max_bytes - len(kept)
                if remaining > 0:
                    kept.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated = True
        except BaseException as exc:  # pragma: no cover - defensive thread handoff
            reader_errors.append(exc)

    reader = threading.Thread(target=_drain, name="openharness-rg-drain", daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join()
        raise
    reader.join()
    if reader_errors:
        raise reader_errors[0]
    return bytes(kept), truncated, returncode


def run(request: dict[str, Any]) -> dict[str, object]:
    handlers = {"read": _read, "write": _write, "edit": _edit, "search": _search}
    kind = str(request.get("kind", ""))
    if kind not in handlers:
        return _failure(f"unknown worker operation: {kind}")
    try:
        return handlers[kind](request)
    except PermissionError:
        return _permission_violation(kind, request)
    except (OSError, subprocess.SubprocessError) as exc:
        return _failure(f"{kind} failed: {exc}")


def main() -> None:
    request = json.loads(sys.stdin.buffer.read())
    response = run(request)
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
