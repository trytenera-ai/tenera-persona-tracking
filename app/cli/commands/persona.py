"""Persona management commands."""

from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from app.cli.client import api

app = typer.Typer()
console = Console()


@app.command()
def create(
    distinct_id: str = typer.Argument(..., help="Unique identifier for the persona"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Display name"),
    description: Optional[str] = typer.Option(None, "--desc", "-d", help="Description"),
    avatar_url: Optional[str] = typer.Option(None, "--avatar-url", help="Avatar image URL"),
    entity: Optional[List[str]] = typer.Option(
        None, "--entity", "-e", help="Key=value entity pair (repeatable)"
    ),
):
    """Create a new persona."""
    body = {"distinct_id": distinct_id}
    if name:
        body["name"] = name
    if description:
        body["description"] = description
    if avatar_url:
        body["avatar_url"] = avatar_url
    if entity:
        entities = []
        for pair in entity:
            if "=" not in pair:
                console.print(f"[red]Invalid entity format:[/red] '{pair}' -- use key=value")
                raise typer.Exit(1)
            k, v = pair.split("=", 1)
            entities.append({"key": k.strip(), "value": v.strip()})
        body["entities"] = entities

    resp = api("POST", "/api/v1/personas", json=body)
    persona = resp.json()

    console.print(f"[green]Created persona[/green] [bold]{persona['distinct_id']}[/bold]")
    console.print(f"  ID: {persona['id']}")
    if persona.get("entities"):
        for e in persona["entities"]:
            console.print(f"  {e['key']}: {e['value']}")


@app.command("list")
def list_personas(
    search: Optional[str] = typer.Option(
        None, "--search", "-s", help="Search by distinct_id or name"
    ),
    limit: int = typer.Option(50, "--limit", "-l"),
):
    """List all personas."""
    params = {"limit": limit}
    if search:
        params["search"] = search

    resp = api("GET", "/api/v1/personas", params=params)
    data = resp.json()

    if not data["results"]:
        console.print("[dim]No personas found.[/dim]")
        return

    table = Table(title=f"Personas ({data['count']})")
    table.add_column("Distinct ID", style="bold")
    table.add_column("Name")
    table.add_column("Entities", style="dim")
    table.add_column("Created")

    for p in data["results"]:
        entity_count = str(len(p.get("entities", [])))
        created = p["created_at"][:10]
        table.add_row(p["distinct_id"], p.get("name") or "--", entity_count, created)

    console.print(table)


@app.command()
def get(
    distinct_id: str = typer.Argument(..., help="Persona distinct_id"),
):
    """Get a persona with all its entities."""
    resp = api("GET", "/api/v1/personas", params={"search": distinct_id, "limit": 1})
    results = resp.json()["results"]

    if not results:
        console.print(f"[red]Persona '{distinct_id}' not found[/red]")
        raise typer.Exit(1)

    persona = results[0]
    resp = api("GET", f"/api/v1/personas/{persona['id']}")
    persona = resp.json()

    console.print(f"[bold]{persona['distinct_id']}[/bold]")
    console.print(f"  ID:          {persona['id']}")
    console.print(f"  Name:        {persona.get('name') or '--'}")
    console.print(f"  Description: {persona.get('description') or '--'}")
    console.print(f"  Avatar URL:  {persona.get('avatar_url') or '--'}")
    console.print(f"  Created:     {persona['created_at']}")
    console.print(f"  Updated:     {persona['updated_at']}")

    entities = persona.get("entities", [])
    if entities:
        console.print(f"\n  [bold]Entities ({len(entities)}):[/bold]")
        for e in entities:
            console.print(f"    {e['key']}: {e['value']}")
    else:
        console.print("\n  [dim]No entities[/dim]")


@app.command()
def update(
    distinct_id: str = typer.Argument(..., help="Persona distinct_id"),
    name: Optional[str] = typer.Option(None, "--name", "-n"),
    description: Optional[str] = typer.Option(None, "--desc", "-d"),
    avatar_url: Optional[str] = typer.Option(None, "--avatar-url"),
    clear_avatar: bool = typer.Option(False, "--clear-avatar"),
):
    """Update a persona's name or description."""
    resp = api("GET", "/api/v1/personas", params={"search": distinct_id, "limit": 1})
    results = resp.json()["results"]
    if not results:
        console.print(f"[red]Persona '{distinct_id}' not found[/red]")
        raise typer.Exit(1)

    persona_id = results[0]["id"]
    body = {}
    if name is not None:
        body["name"] = name
    if description is not None:
        body["description"] = description
    if avatar_url is not None:
        body["avatar_url"] = avatar_url
    if clear_avatar:
        body["avatar_url"] = None

    if not body:
        console.print(
            "[yellow]Nothing to update -- provide --name, --desc, "
            "--avatar-url, or --clear-avatar[/yellow]"
        )
        raise typer.Exit(1)

    api("PATCH", f"/api/v1/personas/{persona_id}", json=body)
    console.print(f"[green]Updated persona[/green] [bold]{distinct_id}[/bold]")


@app.command()
def delete(
    distinct_id: str = typer.Argument(..., help="Persona distinct_id"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Delete a persona and all its data."""
    resp = api("GET", "/api/v1/personas", params={"search": distinct_id, "limit": 1})
    results = resp.json()["results"]
    if not results:
        console.print(f"[red]Persona '{distinct_id}' not found[/red]")
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(f"Delete persona '{distinct_id}' and all its data?")
        if not confirm:
            raise typer.Abort()

    api("DELETE", f"/api/v1/personas/{results[0]['id']}")
    console.print(f"[green]Deleted persona[/green] [bold]{distinct_id}[/bold]")
