"""Small JSON-in/JSON-out filesystem worker run inside an OS sandbox."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_MAX_READ_BYTES = 10 * 1024 * 1024
_MAX_SEARCH_BYTES = 8 * 1024 * 1024
_STREAM_CHUNK_BYTES = 64 * 1024


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
    except PermissionError as exc:
        # The target search happens inside rg. A PermissionError raised here
        # therefore means the worker could not launch the rg executable; it
        # is not evidence that the requested search path sits outside the
        # filesystem boundary. Misclassifying it as a path violation creates
        # an exact permission request that cannot fix the underlying runtime
        # restriction (dogfood: allowed workspace Grep parked forever).
        return _failure(f"failed to launch ripgrep inside the sandbox: {exc}")
    output, byte_truncated = _collect_bounded_output(process, max_bytes=_MAX_SEARCH_BYTES)
    returncode = process.wait()
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


def _collect_bounded_output(
    process: subprocess.Popen[bytes], *, max_bytes: int
) -> tuple[bytes, bool]:
    """Drain a child pipe completely while retaining at most ``max_bytes``."""
    if process.stdout is None:
        raise subprocess.SubprocessError("search process has no output pipe")
    kept = bytearray()
    truncated = False
    while chunk := process.stdout.read(_STREAM_CHUNK_BYTES):
        remaining = max_bytes - len(kept)
        if remaining > 0:
            kept.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return bytes(kept), truncated


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
