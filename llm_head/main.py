from .migrations import populate_parent_ids, migrate
from .dag import format_conversation, patched_from_row, patched_log_to_db, new_load_conversation

import llm
import llm.cli as lcli
from llm.cli import logs_db_path
from llm.models import Response

import sqlite_utils
import click
from click_default_group import DefaultGroup


# Apply patches
Response.log_to_db = patched_log_to_db
Response.from_row = classmethod(patched_from_row)
Response.parent_id = None
lcli.load_conversation = new_load_conversation


@llm.hookimpl
def register_commands(cli):
    @cli.group(
        cls=DefaultGroup,
        default="show",
        default_if_no_args=True,
    )
    def head():
        "Manage the current response (head) in a conversation"
        pass


    @head.command(name="set")
    @click.argument("response_id")
    def head_set(response_id):
        "Set the current head to a specific response ID"
        db = sqlite_utils.Database(logs_db_path())
        migrate(db)

        # Verify the response exists
        try:
            _ = db["responses"].get(response_id)
        except sqlite_utils.db.NotFoundError:
            raise click.ClickException(f"Response {response_id} not found")

        # Set or update the head in current_state
        db["state"].upsert(
            {"key": "head", "value": response_id},
            pk="key"
        )
        click.echo(f"Head is now at response {response_id}")


    @head.command(name="back")
    def head_back():
        "Move head to parent of current response"
        db = sqlite_utils.Database(logs_db_path())
        migrate(db)

        # Get current head
        try:
            head_id = db["state"].get("head")["value"]
        except sqlite_utils.db.NotFoundError:
            raise click.ClickException("No current head set")

        try:
            current = db["responses"].get(head_id)
        except sqlite_utils.db.NotFoundError:
            raise click.ClickException(f"Current head response {head_id} not found")

        # Try to get parent_id, fall back to chronological if needed
        parent_id = current.get("parent_id")
        if not parent_id:
            # Find the most recent response before this one
            parent = next(db.query("""
                SELECT id FROM responses
                WHERE conversation_id = ?
                AND datetime_utc < ?
                ORDER BY datetime_utc DESC
                LIMIT 1
            """, [current["conversation_id"], current["datetime_utc"]]), None)

            if parent:
                parent_id = parent["id"]
            else:
                raise click.ClickException("No parent response found")

        # Update head
        db["state"].upsert(
            {"key": "head", "value": parent_id},
            pk="key"
        )
        click.echo(f"Head moved back to response {parent_id}")


    @head.command(name="show")
    def head_show():
        "Show the current head response"
        db = sqlite_utils.Database(logs_db_path())
        migrate(db)

        try:
            head_id = db["state"].get("head")["value"]
            response = db["responses"].get(head_id)
            click.echo(f"Current head is at response {head_id}")
            click.echo("\nPrompt:")
            click.echo(response["prompt"])
            click.echo("\nResponse:")
            click.echo(response["response"])
        except sqlite_utils.db.NotFoundError:
            click.echo("No head currently set")

    @head.command(name="populate")
    def head_populate():
        "Populate parent IDs for all existing conversations"
        db = sqlite_utils.Database(logs_db_path())
        migrate(db)
        
        click.echo("Starting parent ID population")
        populate_parent_ids(db)
        click.echo("Finished populating parent IDs")

    @head.command(name="print")
    @click.argument("identifier", required=False)
    def head_print(identifier):
        "Print a conversation neatly. Takes conversation ID or number from list. Defaults to current conversation."
        db = sqlite_utils.Database(logs_db_path())
        migrate(db)

        # Store original head
        original_head = None
        try:
            original_head = db["state"].get("head")["value"]
        except sqlite_utils.db.NotFoundError:
            pass

        try:
            if identifier:
                # Check if identifier is a number
                try:
                    if identifier.isdigit():
                        # Get conversations in current sort order
                        conversations = list(db.query("""
                            SELECT id FROM conversations c
                            LEFT JOIN responses r ON c.id = r.conversation_id
                            GROUP BY c.id
                            ORDER BY MAX(r.datetime_utc) DESC
                        """))
                        
                        idx = int(identifier) - 1
                        if idx < 0 or idx >= len(conversations):
                            raise click.ClickException(f"Invalid conversation number: {identifier}")
                        
                        conversation_id = conversations[idx]["id"]
                    else:
                        conversation_id = identifier

                    # Get latest response for requested conversation
                    latest = next(db.query("""
                        SELECT id FROM responses 
                        WHERE conversation_id = ? 
                        ORDER BY datetime_utc DESC 
                        LIMIT 1
                    """, [conversation_id]), None)
                    
                    if not latest:
                        raise click.ClickException(f"No responses found in conversation {conversation_id}")
                except ValueError:
                    raise click.ClickException(f"Invalid conversation identifier: {identifier}")
                
                # Temporarily set head to this conversation's latest response
                db["state"].upsert({"key": "head", "value": latest["id"]}, pk="key")
            
            # Format using current head
            formatted, error = format_conversation(db)
            if error:
                raise click.ClickException(error)

        finally:
            # Restore original head if we had one
            if original_head:
                db["state"].upsert({"key": "head", "value": original_head}, pk="key")
        if error:
            raise click.ClickException(error)

        # Print with colors
        for line in formatted.split("\n"):
            if line.startswith("Conversation:") or line.startswith("Model:"):
                click.secho(line, fg="green", bold=True)
            elif line.startswith(("→ Exchange", "Exchange")):
                click.secho(line, fg="blue", bold=True)
            elif line.startswith("Prompt:") or line.startswith("Response:"):
                click.secho(line, fg="yellow")
            elif line.startswith("[ID:"):
                click.secho(line, fg="cyan")
            else:
                click.echo(line)

    @head.command(name="list")
    @click.option('--sort', type=click.Choice(['time', 'length']), default='time',
                 help='Sort by last active time (default) or conversation length')
    def head_list(sort):
        "List all conversations and their response counts"
        db = sqlite_utils.Database(logs_db_path())
        migrate(db)

        # Get conversation stats with dynamic sorting
        order_by = "last_active ASC" if sort == 'time' else "response_count ASC"
        conversations = db.query(f"""
            SELECT 
                c.id,
                c.name,
                c.model,
                COUNT(r.id) as response_count,
                MAX(r.datetime_utc) as last_active
            FROM conversations c
            LEFT JOIN responses r ON c.id = r.conversation_id
            GROUP BY c.id
            ORDER BY {order_by}
        """)

        # Get current head conversation
        try:
            head_id = db["state"].get("head")["value"]
            head_conv = db["responses"].get(head_id)["conversation_id"]
        except (sqlite_utils.db.NotFoundError, TypeError):
            head_conv = None

        # Print conversations
        for conv in conversations:
            prefix = "→" if conv["id"] == head_conv else " "
            click.secho(
                f"\n{prefix} {conv['name']} -- {conv['id']}",
                fg="green", bold=True
            )
            click.secho(f"    Model: {conv['model']}", fg="blue")
            click.secho(
                f"    Responses: {conv['response_count']} | "
                f"Last active: {conv['last_active'] or 'Never'}",
                fg="yellow"
            )
