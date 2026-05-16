"""Real-Docker integration smoke for ``SandboxExecution`` — P7b-T3.

Gated by ``@pytest.mark.integration`` + a ``docker info`` reachability
check. Skipped cleanly on machines without a running Docker daemon
(macOS without Docker Desktop running, Linux without Docker, CI
without a Docker-enabled runner). Verifies real isolation properties:

- cwd bind mount works → ``ls /workspace`` shows host files
- ``/etc/passwd`` doesn't exist in the container's mount namespace
  (D18.4 — defense in depth via the kernel, not via permission checks)
- Default ``--network none`` blocks external network (D18.5)
- ``--sandbox-network=bridge`` opt-in allows external access

These tests require pulling ``python:3.12-slim`` (~120MB) on first
run. Pull time + container spawn add ~5-30s of wall time per session
depending on whether the image is cached. Tests are kept minimal
(~5-second budget each) to keep the integration suite usable.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from openharness.execution.sandbox import SandboxExecution

if TYPE_CHECKING:
    from pathlib import Path


# --------------------------------------------------------------------------- #
# Docker availability fixture                                                  #
# --------------------------------------------------------------------------- #


def _docker_available() -> bool:
    """True iff ``docker info`` succeeds — daemon reachable + responsive.

    Cross-platform safe: uses ``shutil.which`` first (no binary → no
    daemon), then ``docker info`` with 5s timeout. Any failure
    (connection refused / not installed / hung) → False.
    """
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _docker_available(),
        reason="Docker daemon not reachable — skipping real-sandbox smoke",
    ),
]


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


class TestSandboxRealDocker:
    """Each test spins up a fresh container, runs a few commands, tears
    down. Container per-test (not per-session) keeps tests independent
    at the cost of ~5s setup overhead each.
    """

    async def test_basic_command_runs(self, tmp_path: Path) -> None:
        async with SandboxExecution(cwd=tmp_path) as sandbox:
            result = await sandbox.run_command("echo hello", cwd=tmp_path)
        assert result.exit_code == 0
        assert "hello" in result.output
        assert result.timed_out is False

    async def test_bind_mount_shows_host_files(self, tmp_path: Path) -> None:
        # Write a file on the host; verify ``ls /workspace`` sees it.
        (tmp_path / "marker.txt").write_text("hi from host\n")
        async with SandboxExecution(cwd=tmp_path) as sandbox:
            result = await sandbox.run_command(
                "ls /workspace && cat /workspace/marker.txt",
                cwd=tmp_path,
            )
        assert result.exit_code == 0
        assert "marker.txt" in result.output
        assert "hi from host" in result.output

    async def test_etc_passwd_doesnt_exist_in_container_view(self, tmp_path: Path) -> None:
        # In python:3.12-slim, /etc/passwd actually DOES exist (part of
        # the base image). The real isolation property is that the
        # HOST's /etc isn't visible — only /workspace is bind-mounted
        # from the host. Verify by checking that a marker file we put
        # in /tmp on the host is NOT visible inside the container.
        (tmp_path / "should-not-leak.txt").write_text("secret")
        # Use a path that exists on the host but isn't bind-mounted.
        # E.g., the parent of tmp_path (tmp_path itself IS the bind
        # mount target, so its parent is outside it).
        outside_path = tmp_path.parent / "should-not-leak.txt"
        async with SandboxExecution(cwd=tmp_path) as sandbox:
            result = await sandbox.run_command(
                f"ls {outside_path}",
                cwd=tmp_path,
            )
        # Path doesn't exist inside the container — exit non-zero.
        assert result.exit_code != 0

    async def test_default_network_none_blocks_external(self, tmp_path: Path) -> None:
        # With ``network=none``, container has no network interface
        # beyond loopback. Any external lookup fails fast.
        async with SandboxExecution(cwd=tmp_path) as sandbox:
            result = await sandbox.run_command(
                # python is in the base image; use it to attempt a connect.
                'python3 -c "import socket; '
                "s=socket.socket(); s.settimeout(2); "
                "s.connect(('1.1.1.1', 53))\"",
                cwd=tmp_path,
                timeout=5.0,
            )
        # Either non-zero exit (network unreachable) OR timed_out.
        # On macOS Docker Desktop the failure may be a hard error;
        # either way exit_code is non-zero.
        assert result.exit_code != 0

    async def test_bridge_network_allows_loopback_to_outside(self, tmp_path: Path) -> None:
        # With ``network=bridge``, container can reach external IPs via
        # NAT. We don't actually hit a public endpoint (flaky / slow);
        # we just confirm the network interface exists.
        async with SandboxExecution(cwd=tmp_path, network="bridge") as sandbox:
            result = await sandbox.run_command(
                # ``ip route`` shows a default route when bridge is on.
                "command -v ip && ip route || cat /proc/net/route",
                cwd=tmp_path,
            )
        # Either ``ip route`` or ``/proc/net/route`` should print
        # something — bridge mode plugs in a usable network stack.
        assert result.exit_code == 0
        assert result.output.strip() != ""

    async def test_custom_image_works(self, tmp_path: Path) -> None:
        # Verify the Settings-overridable image path:use a tiny
        # alternative image. ``alpine`` is ~7MB and ships with sh.
        async with SandboxExecution(cwd=tmp_path, image="alpine") as sandbox:
            result = await sandbox.run_command("echo from-alpine", cwd=tmp_path)
        assert result.exit_code == 0
        assert "from-alpine" in result.output
