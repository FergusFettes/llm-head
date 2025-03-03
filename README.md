# llm-head WIP DO NOT USE

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/fergusfettes/llm-head/blob/main/LICENSE)

Track and navigate LLM conversation history with branchable conversations.

NB: This monkey patches a lot of the llm internals and adds stuff to the db, probably not wise to use it unless you have backups.

## Installation

Install this plugin in the same environment as [LLM](https://llm.datasette.io/).
```bash
llm install llm-head
```

## Usage

Manage conversation branching and backtracking with these commands:

```bash
# Show current position in conversation
llm head show

# Move back to previous response
llm head back

# Jump to specific response ID
llm head set <response_id>

# Start a new branch from a specific response
llm head set <response_id> && llm "continue from here"
```

Example workflow:
```bash
$ llm "What's 2+2?"
... (response ID: abc123)

$ llm head back
Moved back to response xyz789 (parent of abc123)

$ llm "But what about three-valued logic?"
... (new branch with ID def456)
```

## Features

- Track conversation "head" state between commands
- Navigate response history chronologically or via parent relationships
- Branch conversations by setting new head positions
- Full conversation history maintained in SQLite
- Works with existing LLM conversation commands

## Development

To set up this plugin locally:
```bash
cd llm-head
python3 -m venv venv
source venv/bin/activate
llm install -e '.[test]'
pytest
```
