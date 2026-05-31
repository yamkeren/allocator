"""Agent CLI: allocator-agent <command>"""

import asyncio

import typer
import uvicorn

app = typer.Typer(help="Allocator Node Agent")


@app.command()
def start(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(5000, help="Bind port"),
    reload: bool = typer.Option(False, help="Enable auto-reload (dev only)"),
) -> None:
    """Start the node agent HTTP server."""
    uvicorn.run(
        "allocator_agent.main:app",
        host=host,
        port=port,
        reload=reload,
        log_config=None,
    )


@app.command()
def onboard() -> None:
    """Interactively onboard pending USB devices."""
    from allocator_agent.components.onboarding import run_interactive_onboarding

    async def _run():
        count = await run_interactive_onboarding()
        raise typer.Exit(0 if count >= 0 else 1)

    asyncio.run(_run())


@app.command()
def status() -> None:
    """Show current device inventory status."""
    import asyncio
    from rich.console import Console
    from rich.table import Table

    async def _run():
        from allocator_agent.components.inventory_manager import InventoryManager
        mgr = await InventoryManager.get_instance()
        await mgr.initialize()
        devices = await mgr.list_all()

        console = Console()
        table = Table(title="Local Device Inventory")
        table.add_column("Logical Name", style="cyan")
        table.add_column("VID:PID", style="yellow")
        table.add_column("Bus ID")
        table.add_column("Class")
        table.add_column("Status", style="green")

        for d in devices:
            table.add_row(
                d.logical_name or "(unassigned)",
                f"{d.vendor_id}:{d.product_id}",
                d.usbip_bus_id or "?",
                d.device_class,
                d.status.value if hasattr(d.status, "value") else str(d.status),
            )

        console.print(table)

    asyncio.run(_run())
