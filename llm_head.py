import llm
import llm.cli as lcli
from llm.cli import logs_db_path
from llm.models import Response, Conversation
from llm.migrations import migration, migrate
import sqlite_utils
from typing import Optional
import click
from click_default_group import DefaultGroup


@migration
def m012_track_current_conversation(db):
    db["state"].create(
        {
            "key": str,
            "value": str,
        },
        pk="key"
    )
    db["responses"].add_column("parent_id", str)


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
        row = db["conversations"].get(conversation_id)
    except sqlite_utils.db.NotFoundError:
        raise click.ClickException(
            "No conversation found with id={}".format(conversation_id)
        )

    responses = {
        r["id"]: r for r in db["responses"].rows_where(
            "conversation_id = ?", [conversation_id]
        )
    }

    if not responses:
        return Conversation.from_row(row)

    head = get_head(db) or max(responses.values(), key=lambda r: r["datetime_utc"])["id"]

    response_chain = []
    while head and head in responses:
        current = Response.from_row(responses[head])
        response_chain.append(current)
        head = get_parent_id(current, db)

    conversation = Conversation.from_row(row)
    conversation.responses = list(reversed(response_chain))
    return conversation


def new_log_to_db(self, db, parent_id=None):
    conversation = self.conversation
    if not conversation:
        conversation = Conversation(model=self.model)
    
    if not conversation.id:
        conversation_data = {
            "model": conversation.model,
            "options": conversation.options,
            "title": conversation.title,
            "system": conversation.system,
        }
        db["conversations"].insert(conversation_data)

    self._force()
    response = {
        "prompt": self.prompt,
        "prompt_json": self._prompt_json,
        "response": self.response,
        "response_json": self.response_json,
        "model": self.model,
        "system": self.system,
        "conversation_id": conversation.id,
        "duration_ms": self.duration_ms(),
        "datetime_utc": self.datetime_utc(),
        "parent_id": parent_id,
    }
    db["responses"].insert(response)
    
    db['state'].upsert(
        {'key': 'head', 'value': response['id']},
        pk='key'
    )


@llm.hookimpl
def register_commands(cli):
    # Apply patches after command registration
    Response.log_to_db = new_log_to_db
    lcli.load_conversation = new_load_conversation
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
