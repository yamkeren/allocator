import asyncio

import typer
from rich.console import Console
from rich.table import Table

from allocator_client.cli.commands._common import get_client

app = typer.Typer(help="View nodes")
console = Console()


@app.command("list")
def list_nodes() -> None:
    """List all registered nodes."""
    async def _run():
        async with get_client() as client:
            data = await client.list_nodes()
            t = Table("Name", "IP", "Status", "Last Heartbeat", "Agent Version")
            for n in data.get("items", []):
                status_str = (
                    "[green]ONLINE[/green]" if n["status"] == "ONLINE"
                    else "[red]OFFLINE[/red]" if n["status"] == "OFFLINE"
                    else n["status"]
                )
                t.add_row(
                    n["name"],
                    n["ip_address"],
                    status_str,
                    str(n.get("last_heartbeat", ""))[:19],
                    n.get("agent_version") or "",
                )
            console.print(t)
    asyncio.run(_run())


@app.command("show")
def show_node(node_id: str = typer.Argument(...)) -> None:
    """Show node details."""
    async def _run():
        async with get_client() as client:
            data = await client.get_node(node_id)
            console.print_json(data=data)
    asyncio.run(_run())
