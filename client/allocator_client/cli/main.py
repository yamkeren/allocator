"""allocator CLI — all commands go through the manager REST API."""

import typer

from allocator_client.cli.commands import config, device, node, session

app = typer.Typer(
    name="allocator",
    help="Distributed USB resource orchestrator client",
    no_args_is_help=True,
)

app.add_typer(session.app, name="session")
app.add_typer(device.app, name="device")
app.add_typer(node.app, name="node")
app.add_typer(config.app, name="config")


if __name__ == "__main__":
    app()
