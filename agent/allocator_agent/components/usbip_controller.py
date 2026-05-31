"""Wraps usbip bind/unbind subprocess calls.

All operations are async (asyncio.create_subprocess_exec).
On startup, reconciles bound devices against local inventory to detect zombies.
"""

import asyncio
from dataclasses import dataclass

import structlog

from allocator_agent.config import settings

log = structlog.get_logger(__name__)


@dataclass
class BindResult:
    bus_id: str
    bound: bool = True


@dataclass
class UnbindResult:
    unbound: bool = True


class UsbipError(Exception):
    pass


class UsbipTimeoutError(UsbipError):
    pass


class UsbipController:
    async def bind(self, bus_id: str) -> BindResult:
        """Bind a USB device for export via usbip.

        Runs: usbip bind -b <bus_id>
        Requires root or usbip sudoers rule.
        """
        stdout, stderr, rc = await self._run(
            "usbip", "bind", "-b", bus_id,
            timeout=settings.usbip_bind_timeout,
        )
        if rc != 0:
            err = stderr.strip() or stdout.strip()
            raise UsbipError(f"usbip bind -b {bus_id} failed (rc={rc}): {err}")
        log.info("usbip_bind_success", bus_id=bus_id)
        return BindResult(bus_id=bus_id)

    async def unbind(self, bus_id: str) -> UnbindResult:
        """Unbind a USB device from usbip export."""
        stdout, stderr, rc = await self._run(
            "usbip", "unbind", "-b", bus_id,
            timeout=settings.usbip_bind_timeout,
        )
        if rc != 0:
            err = stderr.strip() or stdout.strip()
            # "not bound" is not an error during cleanup
            if "not bound" in err.lower() or "no such" in err.lower():
                log.warning("usbip_unbind_not_bound", bus_id=bus_id)
                return UnbindResult(unbound=True)
            raise UsbipError(f"usbip unbind -b {bus_id} failed (rc={rc}): {err}")
        log.info("usbip_unbind_success", bus_id=bus_id)
        return UnbindResult()

    async def list_bound(self) -> list[str]:
        """Return list of currently-bound bus IDs via `usbip list --local`."""
        stdout, _, rc = await self._run("usbip", "list", "--local", timeout=5)
        if rc != 0:
            return []
        bound: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            # Format: " - busid 1-1.2 (0bda:8812)"
            if line.startswith("-") and "busid" in line:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == "busid" and i + 1 < len(parts):
                        bound.append(parts[i + 1])
        return bound

    @staticmethod
    async def check_available() -> bool:
        """Return True if usbip command is available on PATH."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "usbip", "version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=3)
            return proc.returncode == 0
        except (FileNotFoundError, asyncio.TimeoutError):
            return False

    @staticmethod
    async def _run(
        *args: str,
        timeout: int = 10,
    ) -> tuple[str, str, int]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            return stdout_b.decode(), stderr_b.decode(), proc.returncode or 0
        except asyncio.TimeoutError:
            raise UsbipTimeoutError(f"usbip command timed out after {timeout}s: {' '.join(args)}")
        except FileNotFoundError:
            raise UsbipError(f"usbip not found in PATH (command: {args[0]})")
