import asyncio

import typer
from rich.console import Console
from rich.table import Table

from allocator_client.cli.commands._common import get_client

app = typer.Typer(help="Manage sessions")
console = Console()


@app.command("create")
def create_session(
    group: str = typer.Option(..., "--group", "-g", help="Group name to allocate"),
    lease: int = typer.Option(3600, "--lease", help="Lease duration in seconds"),
) -> None:
    """Request a session for a group (allocates all devices atomically)."""
    async def _run():
        async with get_client() as client:
            data = await client.create_session(group, lease_duration=lease)
            console.print(f"[green]Session created:[/green] {data['session_id']}")
            console.print(f"Status: {data['status']}")
            if data.get("devices"):
                t = Table("Device", "Node IP", "Bus ID", "Attach Command")
                for dev in data["devices"]:
                    t.add_row(
                        dev["logical_name"],
                        dev.get("node_ip", ""),
                        dev.get("usbip_bus_id", ""),
                        dev.get("usbip_attach_command", ""),
                    )
                console.print(t)
    asyncio.run(_run())


@app.command("list")
def list_sessions(
    status: str = typer.Option(None, "--status", help="Filter by status"),
) -> None:
    """List your sessions."""
    async def _run():
        async with get_client() as client:
            data = await client.list_sessions(status=status)
            t = Table("Session ID", "Group", "Status", "Lease Expires")
            for s in data.get("items", []):
                t.add_row(s["session_id"], s["group_name"], s["status"], str(s.get("lease_expires_at", "")))
            console.print(t)
    asyncio.run(_run())


@app.command("show")
def show_session(session_id: str = typer.Argument(...)) -> None:
    """Show session details including attach commands."""
    async def _run():
        async with get_client() as client:
            data = await client.get_session(session_id)
            console.print_json(data=data)
    asyncio.run(_run())


@app.command("release")
def release_session(session_id: str = typer.Argument(...)) -> None:
    """Release a session and return all devices to the pool."""
    async def _run():
        async with get_client() as client:
            data = await client.release_session(session_id)
            console.print(f"[yellow]Session releasing:[/yellow] {data}")
    asyncio.run(_run())
