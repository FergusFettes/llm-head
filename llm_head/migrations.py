import click
from llm.migrations import migration, migrate


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
    db["conversations"].add_column("tags", str)


def populate_parent_ids(db):
    """Populate parent_ids for all responses in the database"""
    # Get all conversations
    conversations = db["conversations"].rows
    
    for conv in conversations:
        conv_id = conv["id"]
        # Get all responses for this conversation ordered by time
        responses = list(db["responses"].rows_where(
            "conversation_id = ? ORDER BY datetime_utc ASC",
            [conv_id]
        ))
        
        click.echo(f"Processing conversation {conv_id} with {len(responses)} responses")
        
        # Skip if no responses
        if not responses:
            continue
            
        # For each response except the first
        for i in range(1, len(responses)):
            current = responses[i]
            parent = responses[i-1]
            
            # Update parent_id if not already set
            if not current.get("parent_id"):
                db["responses"].update(
                    current["id"],
                    {"parent_id": parent["id"]},
                    alter=True
                )
