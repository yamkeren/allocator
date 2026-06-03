import typer
from rich.console import Console
from rich.table import Table

from allocator_client.cli.commands._common import get_client
from allocator_client.client import NameConflict

app = typer.Typer(help="View and name devices")
console = Console()


@app.command("list")
def list_devices(
    node: str = typer.Option(None, "--node", help="Filter by node name/ID"),
    status: str = typer.Option(None, "--status", help="Filter by status (FREE, ALLOCATED, ERROR)"),
    cls: str = typer.Option(None, "--class", help="Filter by device class"),
) -> None:
    """List all devices in the inventory."""
    with get_client() as client:
        data = client.list_devices(node_id=node, status=status, device_class=cls)
        t = Table("Logical Name", "Node", "VID:PID", "Class", "Status", "Bus ID")
        for d in data.items:
            t.add_row(
                d.logical_name,
                d.node_id[:8],
                f"{d.vendor_id}:{d.product_id}",
                d.device_class,
                d.status,
                d.usbip_bus_id or "",
            )
        console.print(t)


@app.command("show")
def show_device(
    node: str = typer.Argument(..., help="Node name or ID"),
    logical_name: str = typer.Argument(...),
) -> None:
    """Show device details (names are unique per node)."""
    with get_client() as client:
        data = client.get_device(node, logical_name)
        console.print_json(data=data.model_dump(mode="json"))


@app.command("name")
def name_device(
    node: str = typer.Argument(..., help="Node name or ID"),
    logical_name: str = typer.Argument(..., help="Current name of the device"),
    new_name: str = typer.Argument(..., help="New custom name (follows the device across nodes)"),
) -> None:
    """Give a device a custom name. The name travels with the physical device."""
    with get_client() as client:
        target = new_name
        while True:
            try:
                d = client.rename_device(node, logical_name, target)
                console.print(f"[green]Renamed:[/green] {d.logical_name} on node {d.node_id[:8]}")
                return
            except NameConflict as conflict:
                console.print(
                    f"[yellow]Name {conflict.name!r} is already used by "
                    f"{conflict.holder!r} on node {conflict.node!r}.[/yellow]"
                )
                choice = typer.prompt(
                    "  [1] make the other device generic  "
                    "[2] choose a different name  [3] abort",
                    default="3",
                )
                if choice == "1":
                    d = client.rename_device(node, logical_name, target, force=True)
                    console.print(f"[green]Renamed:[/green] {d.logical_name} on node {d.node_id[:8]}")
                    return
                if choice == "2":
                    target = typer.prompt("New name")
                    continue
                console.print("[red]Aborted[/red]")
                return
