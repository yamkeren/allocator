import asyncio

import typer
from rich.console import Console
from rich.table import Table

from allocator_client.cli.commands._common import get_client

app = typer.Typer(help="View devices")
console = Console()


@app.command("list")
def list_devices(
    node: str = typer.Option(None, "--node", help="Filter by node name/ID"),
    status: str = typer.Option(None, "--status", help="Filter by status (FREE, BOUND, etc.)"),
    cls: str = typer.Option(None, "--class", help="Filter by device class"),
) -> None:
    """List all devices in the inventory."""
    async def _run():
        async with get_client() as client:
            data = await client.list_devices(node_id=node, status=status, device_class=cls)
            t = Table("Logical Name", "Node", "VID:PID", "Class", "Status", "Bus ID")
            for d in data.get("items", []):
                t.add_row(
                    d["logical_name"],
                    d["node_id"][:8],
                    f"{d['vendor_id']}:{d['product_id']}",
                    d["device_class"],
                    d["status"],
                    d.get("usbip_bus_id") or "",
                )
            console.print(t)
    asyncio.run(_run())


@app.command("show")
def show_device(logical_name: str = typer.Argument(...)) -> None:
    """Show device details."""
    async def _run():
        async with get_client() as client:
            data = await client.get_device(logical_name)
            console.print_json(data=data)
    asyncio.run(_run())
