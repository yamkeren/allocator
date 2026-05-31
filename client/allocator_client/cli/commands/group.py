import asyncio

import typer
from rich.console import Console
from rich.table import Table

from allocator_client.cli.commands._common import get_client

app = typer.Typer(help="Manage groups")
console = Console()


@app.command("create")
def create_group(
    name: str = typer.Argument(...),
    devices: str = typer.Option(..., "--devices", "-d", help="Comma-separated logical device names"),
    description: str = typer.Option("", "--desc"),
) -> None:
    """Create a new group (atomic bundle of devices)."""
    async def _run():
        async with get_client() as client:
            device_list = [d.strip() for d in devices.split(",") if d.strip()]
            data = await client.create_group(name, device_list, description=description)
            console.print(f"[green]Group created:[/green] {data['name']} ({data['device_count']} devices)")
    asyncio.run(_run())


@app.command("list")
def list_groups() -> None:
    """List all groups with availability."""
    async def _run():
        async with get_client() as client:
            data = await client.list_groups()
            t = Table("Name", "Devices", "Available", "Active Sessions")
            for g in data.get("items", []):
                t.add_row(
                    g["name"],
                    str(g["device_count"]),
                    "[green]yes[/green]" if g["available"] else "[red]no[/red]",
                    str(g["active_sessions"]),
                )
            console.print(t)
    asyncio.run(_run())


@app.command("show")
def show_group(name: str = typer.Argument(...)) -> None:
    """Show group details."""
    async def _run():
        async with get_client() as client:
            data = await client.get_group(name)
            console.print_json(data=data)
    asyncio.run(_run())


@app.command("delete")
def delete_group(
    name: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a group (blocked if active sessions exist)."""
    if not yes:
        typer.confirm(f"Delete group {name!r}?", abort=True)

    async def _run():
        async with get_client() as client:
            await client.delete_group(name)
            console.print(f"[red]Deleted:[/red] {name}")
    asyncio.run(_run())
