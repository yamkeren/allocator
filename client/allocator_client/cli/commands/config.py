import typer
from rich.console import Console

from allocator_client.config import Config

app = typer.Typer(help="Persistent client config (~/.config/allocator/config.json)")
console = Console()


def _check(key: str) -> None:
    if key not in Config.KEYS:
        raise typer.BadParameter(f"unknown key {key!r} (valid: {', '.join(Config.KEYS)})")


@app.command("set")
def set_value(
    key: str = typer.Argument(..., help="url | client_id"),
    value: str = typer.Argument(...),
) -> None:
    """Persist a config value."""
    _check(key)
    cfg = Config()
    cfg.set(key, value)
    console.print(f"[green]set[/green] {key} = {value}")
    console.print(f"[dim]{cfg.path}[/dim]")


@app.command("get")
def get_value(key: str = typer.Argument(..., help="url | client_id")) -> None:
    """Print the effective value (stored, or default)."""
    _check(key)
    console.print(Config().get(key))


@app.command("unset")
def unset_value(key: str = typer.Argument(..., help="url | client_id")) -> None:
    """Remove a stored value (falls back to the default)."""
    _check(key)
    Config().unset(key)
    console.print(f"[red]unset[/red] {key}")


@app.command("show")
def show() -> None:
    """Show the effective config and the file it lives in."""
    cfg = Config()
    console.print_json(data=cfg.resolved())
    console.print(f"[dim]file:[/dim] {cfg.path}")
