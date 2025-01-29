import click

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
