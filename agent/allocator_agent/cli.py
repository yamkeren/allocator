"""Agent CLI: allocator-agent start"""

import uvicorn
import typer

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
