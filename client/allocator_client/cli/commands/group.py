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
    """Create a new group (a template of logical names resolved at session time)."""
    with get_client() as client:
        device_list = [d.strip() for d in devices.split(",") if d.strip()]
        g = client.create_group(name, device_list, description=description)
        console.print(f"[green]Group created:[/green] {g.name} ({g.device_count} devices)")


@app.command("list")
def list_groups() -> None:
    """List all groups with availability."""
    with get_client() as client:
        data = client.list_groups()
        t = Table("Name", "Devices", "Available", "Active Sessions")
        for g in data.items:
            t.add_row(
                g.name,
                str(g.device_count),
                "[green]yes[/green]" if g.available else "[red]no[/red]",
                str(g.active_sessions),
            )
        console.print(t)


@app.command("show")
def show_group(name: str = typer.Argument(...)) -> None:
    """Show group details."""
    with get_client() as client:
        data = client.get_group(name)
        console.print_json(data=data.model_dump(mode="json"))


@app.command("delete")
def delete_group(
    name: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a group (blocked if active sessions exist)."""
    if not yes:
        typer.confirm(f"Delete group {name!r}?", abort=True)
    with get_client() as client:
        client.delete_group(name)
        console.print(f"[red]Deleted:[/red] {name}")
