from typing import Optional, cast

from .migrations import migrate

from llm.cli import logs_db_path
from llm.models import Response, Conversation

import click
import sqlite_utils

# Store original
original_log_to_db = Response.log_to_db
original_from_row = Response.from_row


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


def get_response_parent(response, db):
    if response.parent_id:
        return next(db.query("SELECT * FROM responses WHERE id = ?", [response.parent_id]), None)

    if not response.conversation:
        return {'id': None}

    return next(db.query("""
        SELECT * FROM responses
        WHERE conversation_id = ?
        AND datetime_utc < ?
        ORDER BY datetime_utc DESC
        LIMIT 1
    """, [response.conversation.id, response.datetime_utc()]), None)


def get_parent_id(response, db):
    parent = get_response_parent(response, db)
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


def patched_from_row(cls, db, row):
    # Call original implementation from global
    response = original_from_row(db, row)
    response.parent_id = row.get("parent_id", None)
    return response
