import json
import os
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from allocator_client import usbip as usbip_helper
from allocator_client.cli.commands._common import get_client
from allocator_contract.session import SessionCreate

app = typer.Typer(help="Manage sessions")
console = Console()


def _need_sudo() -> bool:
    return getattr(os, "geteuid", lambda: 0)() != 0


def _state_file(session_id: str) -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    d = Path(base) / "allocator" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{session_id}.json"


def _attach_devices(session_id: str, devices) -> None:
    """usbip attach each device locally and record the assigned ports.

    Detach later uses the recorded (device, port) list directly — never guessing
    from `usbip port` — so an attached session is always fully detachable.
    """
    sudo = _need_sudo()
    usbip_helper.ensure_vhci(sudo=sudo)
    console.print("[bold]Attaching devices…[/bold]")
    attached: list[dict] = []
    for d in devices:
        if not (d.node_ip and d.usbip_bus_id):
            continue
        try:
            port = usbip_helper.attach(d.node_ip, d.usbip_bus_id, sudo=sudo)
            attached.append({"logical_name": d.logical_name, "port": port})
            console.print(f"  [green]✓[/green] {d.logical_name} → local port {port}")
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [red]✗[/red] {d.logical_name}: {exc}")
            console.print("    [yellow]hint: run with sudo and ensure 'usbip' + vhci-hcd are available[/yellow]")
    if attached:
        _state_file(session_id).write_text(json.dumps(attached))


def _detach_recorded(session_id: str) -> None:
    """Detach exactly the ports recorded for this session at attach time.

    No-op (silent) if this session was never attached locally — so it's safe to
    call unconditionally on every release.
    """
    sf = _state_file(session_id)
    if not sf.exists():
        return
    sudo = _need_sudo()
    entries = json.loads(sf.read_text())
    for e in entries:
        try:
            usbip_helper.detach(int(e["port"]), sudo=sudo)
            console.print(f"  [green]✓[/green] detached {e['logical_name']} (port {e['port']})")
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [yellow]![/yellow] {e['logical_name']} (port {e['port']}): {exc}")
    sf.unlink(missing_ok=True)


@app.command("create")
def create_session(
    devices: str = typer.Option(..., "--devices", "-d", help="Comma-separated logical device names to allocate together"),
    node: str = typer.Option(None, "--node", "-n", help="Pin the session to a specific node"),
    attach: bool = typer.Option(False, "--attach", help="Also usbip-attach each device locally (needs root/sudo)"),
) -> None:
    """Request a session for a list of devices (allocated atomically on a single node)."""
    device_list = [d.strip() for d in devices.split(",") if d.strip()]
    # SessionCreate is the single source of truth for device-list rules
    # (non-empty, no duplicates, name pattern). Validate at the boundary so a
    # bad value is a clean CLI error before we open a client or hit the network.
    try:
        SessionCreate(devices=device_list, node=node)
    except ValidationError as exc:
        msg = "; ".join(e["msg"] for e in exc.errors())
        raise typer.BadParameter(msg, param_hint="--devices") from exc
    with get_client() as client:
        s = client.create_session(device_list, node=node)
        on = f"  node={s.node_name}" if s.node_name else ""
        console.print(f"[green]Session:[/green] {s.session_id}  status={s.status}{on}")
        if s.status == "PENDING":
            console.print("[yellow]Queued[/yellow] — waiting for a node that can satisfy the request.")
        if s.devices:
            t = Table("Device", "Node IP", "Bus ID", "Attach Command")
            for dev in s.devices:
                t.add_row(
                    dev.logical_name,
                    dev.node_ip or "",
                    dev.usbip_bus_id or "",
                    dev.usbip_attach_command or "",
                )
            console.print(t)
        if s.failure_reason:
            console.print(f"[red]Failure:[/red] {s.failure_reason}")
        if attach and s.status == "ACTIVE":
            _attach_devices(s.session_id, s.devices)


@app.command("freeze")
def freeze_session_node(session_id: str = typer.Argument(...)) -> None:
    """Freeze the node this active session is using (stays frozen after release)."""
    with get_client() as client:
        client.freeze_session_node(session_id)
        console.print(f"[cyan]Node frozen[/cyan] for session {session_id}")


@app.command("list")
def list_sessions(
    status: str = typer.Option(None, "--status", help="Filter by status"),
) -> None:
    """List your sessions."""
    with get_client() as client:
        data = client.list_sessions(status=status)
        t = Table("Session ID", "Devices", "Status", "Node", "Created")
        for s in data.items:
            t.add_row(
                s.session_id,
                ", ".join(s.requested_devices),
                s.status,
                s.node_name or "",
                str(s.created_at),
            )
        console.print(t)


@app.command("show")
def show_session(session_id: str = typer.Argument(...)) -> None:
    """Show session details including attach commands."""
    with get_client() as client:
        data = client.get_session(session_id)
        console.print_json(data=data.model_dump(mode="json"))


@app.command("release")
def release_session(session_id: str = typer.Argument(...)) -> None:
    """Release a session and return all devices to the pool.

    If this session's devices were attached locally (via `create --attach`),
    they are usbip-detached automatically first.
    """
    with get_client() as client:
        _detach_recorded(session_id)
        client.release_session(session_id)
        console.print(f"[yellow]Released:[/yellow] {session_id}")
