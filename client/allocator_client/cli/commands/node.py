import typer
from rich.console import Console
from rich.table import Table

from allocator_client.cli.commands._common import get_client

app = typer.Typer(help="View nodes")
console = Console()


@app.command("list")
def list_nodes() -> None:
    """List all registered nodes."""
    with get_client() as client:
        data = client.list_nodes()
        t = Table("Name", "IP", "Status", "Frozen", "Last Heartbeat", "Agent Version")
        for n in data.items:
            status_str = (
                "[green]ONLINE[/green]" if n.status == "ONLINE"
                else "[red]OFFLINE[/red]" if n.status == "OFFLINE"
                else n.status
            )
            t.add_row(
                n.name,
                n.ip_address,
                status_str,
                "[cyan]frozen[/cyan]" if n.frozen else "",
                str(n.last_heartbeat or "")[:19],
                n.agent_version or "",
            )
        console.print(t)


@app.command("show")
def show_node(node_id: str = typer.Argument(...)) -> None:
    """Show node details."""
    with get_client() as client:
        data = client.get_node(node_id)
        console.print_json(data=data.model_dump(mode="json"))


@app.command("unfreeze")
def unfreeze_node(node: str = typer.Argument(..., help="Node name or ID")) -> None:
    """Unfreeze a node so it can be selected for sessions again."""
    with get_client() as client:
        data = client.unfreeze_node(node)
        console.print(f"[green]Unfrozen:[/green] {data.name}")
