import time

import click

from .context import AppContext


@click.group()
def vault_group() -> None:
    """Manage the encrypted sensitive vault."""


@vault_group.command("init")
@click.pass_context
def vault_init(ctx: click.Context) -> None:
    """Initialize the vault with a new passphrase."""
    app: AppContext = ctx.obj["app"]
    if app.vault.is_initialized():
        click.echo("Vault is already initialized.")
        return
    pw = click.prompt("New vault passphrase", hide_input=True, confirmation_prompt=True)
    app.vault.initialize(pw)
    app.vault._write_sentinel()
    click.echo("Vault initialized.")


@vault_group.command("unlock")
@click.pass_context
def vault_unlock(ctx: click.Context) -> None:
    """Unlock the vault for this session."""
    app: AppContext = ctx.obj["app"]
    if not app.vault.is_initialized():
        click.echo("Vault not initialized. Run: corenous vault init")
        return
    pw = click.prompt("Vault passphrase", hide_input=True)
    if app.vault.unlock(pw):
        click.echo("Vault unlocked.")
    else:
        click.echo("Wrong passphrase.", err=True)


@vault_group.command("list")
@click.pass_context
def vault_list(ctx: click.Context) -> None:
    """List vault entries (metadata only, no decryption)."""
    app: AppContext = ctx.obj["app"]
    entries = app.vault.list_entries()
    if not entries:
        click.echo("Vault is empty.")
        return
    for e in entries:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(e["created_at"]))
        click.echo(f"  id={e['id']}  {ts}")


@vault_group.command("show")
@click.argument("vault_id", type=int)
@click.pass_context
def vault_show(ctx: click.Context, vault_id: int) -> None:
    """Decrypt and display a single vault entry."""
    app: AppContext = ctx.obj["app"]
    if not app.vault.is_initialized():
        click.echo("Vault not initialized.")
        return
    pw = click.prompt("Vault passphrase", hide_input=True)
    if not app.vault.unlock(pw):
        click.echo("Wrong passphrase.", err=True)
        return
    try:
        data = app.vault.retrieve(vault_id)
        click.echo(f"Source  : {data['source']}")
        click.echo(f"App     : {data['app']}")
        click.echo(f"Time    : {time.strftime('%Y-%m-%d %H:%M', time.localtime(data['ts']))}")
        click.echo(f"Reasons : {', '.join(data.get('reasons', []))}")
        click.echo(f"Text    :\n{data['text']}")
    except KeyError:
        click.echo(f"Vault entry {vault_id} not found.", err=True)
    finally:
        app.vault.lock()
