from typing import Optional, cast

from .migrations import migrate

from llm.cli import logs_db_path
from llm.models import Response, Conversation

import click
import sqlite_utils

# Store original
original_log_to_db = Response.log_to_db
original_from_row = Response.from_row


def resolve_conversation_identifier(db, identifier):
    """Resolve a conversation identifier (number or ID) to a conversation ID
    
    Args:
        db: sqlite_utils.Database instance
        identifier: String containing either a conversation number or ID
        
    Returns:
        tuple of (conversation_id, latest_response_id, error_message)
        conversation_id and latest_response_id will be None if there was an error
    """
    if identifier.isdigit():
        # Get conversations in current sort order
        conversations = list(db.query("""
            SELECT c.id FROM conversations c
            LEFT JOIN responses r ON c.id = r.conversation_id
            GROUP BY c.id
            ORDER BY MAX(r.datetime_utc) DESC
        """))
        
        idx = int(identifier) - 1
        if idx < 0 or idx >= len(conversations):
            return None, None, f"Invalid conversation number: {identifier}"
        
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
        return None, f"No responses found in conversation {conversation_id}"
        
    return latest["id"], None


def print_formatted_conversation(db):
    """Print a formatted conversation with colors
    
    Args:
        formatted_text: The formatted conversation text
        error: Optional error message
    """
    formatted, error = format_conversation(db)
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


def print_conversation_list(db, sort='time'):
    """Print numbered list of all conversations with stats
    
    Args:
        db: sqlite_utils.Database instance
        sort: 'time' or 'length' to determine sort order
    """
    # Get conversation stats with dynamic sorting
    order_by = "last_active DESC" if sort == 'time' else "response_count DESC"
    conversations = list(db.query(f"""
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
    """))

    # Get current head conversation
    try:
        head_id = db["state"].get("head")["value"]
        head_conv = db["responses"].get(head_id)["conversation_id"]
    except (sqlite_utils.db.NotFoundError, TypeError):
        head_conv = None

    # Print conversations
    for i, conv in enumerate(conversations, 1):
        prefix = "→" if conv["id"] == head_conv else " "
        click.secho(
            f"\n{prefix} {i}. {conv['name']} -- {conv['id']}", 
            fg="green", bold=True
        )
        click.secho(f"    Model: {conv['model']}", fg="blue")
        click.secho(
            f"    Responses: {conv['response_count']} | "
            f"Last active: {conv['last_active'] or 'Never'}", 
            fg="yellow"
        )
    
    return conversations


def format_conversation(db):
    """Format the current conversation for display
    
    Args:
        db: sqlite_utils.Database instance
        
    Returns:
        tuple of (formatted_text, error_message)
        formatted_text will be None if there was an error
        error_message will be None if formatting succeeded
    """
    try:
        head_id = db["state"].get("head")["value"]
    except sqlite_utils.db.NotFoundError:
        return None, "No current head set"

    try:
        current = db["responses"].get(head_id)
    except sqlite_utils.db.NotFoundError:
        return None, f"Current head response {head_id} not found"

    conversation = new_load_conversation(current["conversation_id"])
    if not conversation:
        return None, "Could not load conversation"

    lines = []
    lines.append(f"\nConversation: {conversation.name} ({conversation.id})")
    lines.append(f"Model: {conversation.model}\n")

    for i, response in enumerate(conversation.responses, 1):
        is_head = response.id == head_id
        prefix = "→ " if is_head else ""
        
        lines.append(f"{prefix}Exchange {i} -- {response.id}")
        lines.append("Prompt:")
        lines.append(response.prompt.prompt)
        lines.append("\nResponse:")
        lines.append(response.text())
        lines.append("\n")

    return "\n".join(lines), None


def get_most_recent_active_conversation(db):
    return next(db.query("""
        SELECT conversation_id, MAX(datetime_utc) as last_active
        FROM responses
        GROUP BY conversation_id
        ORDER BY last_active DESC
        LIMIT 1
    """), {}).get("conversation_id")


def get_head(db):
    try:
        return db['state'].get('head')['value']
    except sqlite_utils.db.NotFoundError:
        return None


def get_parent_id(response, db):
    if response.parent_id:
        return next(db.query("SELECT * FROM responses WHERE id = ?", [response.parent_id]), None)['id']

    conversation_id = next(db.query("""
        SELECT conversation_id FROM responses WHERE id = ?
    """, [response.id]), {}).get("conversation_id", None)

    if conversation_id is None:
        return None

    parent = next(db.query("""
        SELECT * FROM responses
        WHERE conversation_id = ?
        AND datetime_utc < ?
        ORDER BY datetime_utc DESC
        LIMIT 1
    """, [conversation_id, response.datetime_utc()]), None)

    if parent:
        return parent['id']
    return None


def new_load_conversation(conversation_id: Optional[str]) -> Optional[Conversation]:
    db = sqlite_utils.Database(logs_db_path())
    migrate(db)
    if conversation_id is None:
        conversation_id = get_most_recent_active_conversation(db)
        if conversation_id is None:
            return None

    try:
        row = cast(sqlite_utils.db.Table, db["conversations"]).get(conversation_id)
    except sqlite_utils.db.NotFoundError:
        raise click.ClickException(
            "No conversation found with id={}".format(conversation_id)
        )

    # Get all responses for lookup purposes
    responses = {
        r["id"]: r for r in db["responses"].rows_where(
            "conversation_id = ?", [conversation_id]
        )
    }

    if not responses:
        return Conversation.from_row(row)

    # Start from head or most recent
    head = get_head(db) or max(responses.values(), key=lambda r: r["datetime_utc"])["id"]

    # Build the response chain by following parents
    response_chain = []
    while head and head in responses:
        current = Response.from_row(db, responses[head])
        response_chain.append(current)
        head = get_parent_id(current, db)

    # Create conversation and add responses in chronological order
    conversation = Conversation.from_row(row)
    conversation.responses = list(reversed(response_chain))
    return conversation


def patched_log_to_db(self, db):
    # Call original implementation from global
    original_log_to_db(self, db)

    # Get the most recent response
    response = Response.from_row(db, next(db.query('SELECT * FROM responses ORDER BY datetime_utc DESC LIMIT 1')))
    parent_id = get_parent_id(response, db)

    # Set the parent ID
    db['responses'].upsert({'id': response.id, 'parent_id': parent_id}, pk='id')

    # Add head tracking
    db['state'].upsert({'key': 'head', 'value': response.id}, pk='key')

    print(f"Logged response {response.id} with parent {parent_id}")


def patched_from_row(cls, db, row):
    # Call original implementation from global
    response = original_from_row(db, row)
    response.parent_id = row.get("parent_id", None)
    response.datetime_utc = lambda: row['datetime_utc']
    return response
