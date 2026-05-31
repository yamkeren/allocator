"""usbip attach/detach subprocess wrappers for the client side.

The client receives node_ip + bus_id from the manager's session response
and uses those to run usbip attach/detach locally.
"""

import asyncio
import re
import subprocess

import structlog

log = structlog.get_logger(__name__)


class UsbipAttachError(Exception):
    pass


class UsbipDetachError(Exception):
    pass


def attach(node_ip: str, bus_id: str, timeout: int = 15) -> int:
    """Run `usbip attach -r <node_ip> -b <bus_id>`.

    Returns the local vhci port number that was assigned, or raises.
    """
    cmd = ["usbip", "attach", "-r", node_ip, "-b", bus_id]
    log.info("usbip_attach", command=" ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise UsbipAttachError(
                f"usbip attach failed (rc={result.returncode}): {result.stderr.strip()}"
            )
        # Parse port from output: "usbip: info: using port 0 (0x0000)"
        port = _parse_port(result.stdout + result.stderr)
        log.info("usbip_attach_success", node_ip=node_ip, bus_id=bus_id, port=port)
        return port
    except subprocess.TimeoutExpired:
        raise UsbipAttachError(f"usbip attach timed out after {timeout}s")


def detach(port: int, timeout: int = 10) -> None:
    """Run `usbip detach -p <port>`."""
    cmd = ["usbip", "detach", "-p", str(port)]
    log.info("usbip_detach", port=port)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            err = result.stderr.strip()
            # "port not in use" is fine during cleanup
            if "port" in err.lower() and "not" in err.lower():
                log.warning("usbip_detach_port_not_in_use", port=port)
                return
            raise UsbipDetachError(
                f"usbip detach port {port} failed (rc={result.returncode}): {err}"
            )
        log.info("usbip_detach_success", port=port)
    except subprocess.TimeoutExpired:
        raise UsbipDetachError(f"usbip detach timed out after {timeout}s")


def _parse_port(output: str) -> int:
    match = re.search(r"using port (\d+)", output)
    if match:
        return int(match.group(1))
    return -1
