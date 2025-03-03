from unittest.mock import patch
import pytest

from llm.cli import load_plugins
load_plugins()
from llm.plugins import pm
from llm_head.dag import format_conversation, new_load_conversation
from llm.migrations import migrate

import sqlite_utils
import click


def test_plugin_is_installed():
    names = [mod.__name__ for mod in pm.get_plugins()]
    assert "llm_head.main" in names


@pytest.fixture
def mock_db():
    db = sqlite_utils.Database(':memory:')
    migrate(db)
    
    # Add test conversation
    db["conversations"].insert({
        "id": "test-conv-1",
        "name": "Test Conversation",
        "model": "gpt-3.5-turbo"
    }, pk="id")
    
    return db


def test_load_conversation_by_id(mock_db):
    with patch('llm_head.dag.logs_db_path', return_value=':memory:'), \
         patch('llm_head.dag.sqlite_utils.Database', return_value=mock_db):
        
        conversation = new_load_conversation("test-conv-1")
        assert conversation is not None
        assert conversation.id == "test-conv-1"
        assert "gpt-3.5-turbo" in str(conversation.model)
        assert conversation.name == "Test Conversation"
        assert isinstance(conversation.responses, list)


def test_load_nonexistent_conversation(mock_db):
    with patch('llm_head.dag.logs_db_path', return_value=':memory:'), \
         patch('llm_head.dag.sqlite_utils.Database', return_value=mock_db):
        
        with pytest.raises(click.ClickException) as exc:
            new_load_conversation("nonexistent-id")
        assert "No conversation found with id=nonexistent-id" in str(exc.value)


def test_load_empty_conversation(mock_db):
    with patch('llm_head.dag.logs_db_path', return_value=':memory:'), \
         patch('llm_head.dag.sqlite_utils.Database', return_value=mock_db):
        
        # Empty conversation has no responses
        conversation = new_load_conversation("test-conv-1")
        assert len(conversation.responses) == 0


def test_load_most_recent_conversation(mock_db):
    with patch('llm_head.dag.logs_db_path', return_value=':memory:'), \
         patch('llm_head.dag.sqlite_utils.Database', return_value=mock_db):
        
        # Add another conversation and some responses
        mock_db["conversations"].insert({
            "id": "test-conv-2",
            "name": "More Recent Conv",
            "model": "gpt-4"
        })
        
        # Add responses with different timestamps
        mock_db["responses"].insert({
            "id": "r1",
            "conversation_id": "test-conv-1",
            "datetime_utc": "2024-01-01T10:00:00Z",
            "prompt": "old",
            "response": "response",
            "options_json": "{}",
            "model": "gpt-4"
        })
        
        mock_db["responses"].insert({
            "id": "r2", 
            "conversation_id": "test-conv-2",
            "datetime_utc": "2024-01-02T10:00:00Z",
            "prompt": "new",
            "response": "response",
            "options_json": "{}",
            "model": "gpt-4"
        })

        # Should load test-conv-2 as it's more recent
        conversation = new_load_conversation(None)
        assert conversation.id == "test-conv-2"
        assert conversation.name == "More Recent Conv"


def test_populate_parent_ids(mock_db):
    with patch('llm_head.dag.logs_db_path', return_value=':memory:'), \
         patch('llm_head.dag.sqlite_utils.Database', return_value=mock_db):
        
        # Add responses without parent IDs
        mock_db["responses"].insert({
            "id": "r1",
            "conversation_id": "test-conv-1",
            "datetime_utc": "2024-01-01T10:00:00Z",
            "prompt": "first",
            "response": "response 1",
            "options_json": "{}",
        })
        
        mock_db["responses"].insert({
            "id": "r2",
            "conversation_id": "test-conv-1",
            "datetime_utc": "2024-01-01T10:01:00Z",
            "prompt": "second",
            "response": "response 2",
            "options_json": "{}",
        })
        
        # Run the populate function
        from llm_head.migrations import populate_parent_ids
        populate_parent_ids(mock_db)
        
        # Check that parent IDs were set correctly
        r2 = mock_db["responses"].get("r2")
        assert r2["parent_id"] == "r1"


def test_response_chain_building(mock_db):
    with patch('llm_head.dag.logs_db_path', return_value=':memory:'), \
         patch('llm_head.dag.sqlite_utils.Database', return_value=mock_db):
        
        # Add responses with parent relationships
        mock_db["responses"].insert({
            "id": "r1",
            "conversation_id": "test-conv-1",
            "datetime_utc": "2024-01-01T10:00:00Z",
            "prompt": "first",
            "response": "response 1",
            "options_json": "{}",
            "model": "gpt-4",
        })
        
        mock_db["responses"].insert({
            "id": "r2",
            "conversation_id": "test-conv-1",
            "datetime_utc": "2024-01-01T10:01:00Z",
            "prompt": "second",
            "response": "response 2",
            "parent_id": "r1",
            "options_json": "{}",
            "model": "gpt-4",
        })
        
        mock_db["responses"].insert({
            "id": "r3",
            "conversation_id": "test-conv-1", 
            "datetime_utc": "2024-01-01T10:02:00Z",
            "prompt": "third",
            "response": "response 3",
            "parent_id": "r2",
            "options_json": "{}",
            "model": "gpt-4",
        })

        # Set head to most recent response
        mock_db["state"].insert({"key": "head", "value": "r3"})
        conversation = new_load_conversation("test-conv-1")
        assert len(conversation.responses) == 3
        assert [r.prompt.prompt for r in conversation.responses] == ["first", "second", "third"]

        # Set head to previous response
        mock_db["state"].upsert({"key": "head", "value": "r2"}, pk="key")
        conversation = new_load_conversation("test-conv-1")
        assert len(conversation.responses) == 2
        assert [r.prompt.prompt for r in conversation.responses] == ["first", "second"]

        # Set head to previous response
        mock_db["state"].upsert({"key": "head", "value": "r1"}, pk="key")
        conversation = new_load_conversation("test-conv-1")
        assert len(conversation.responses) == 1
        assert [r.prompt.prompt for r in conversation.responses] == ["first"]

        # Start a new branch 
        mock_db["responses"].insert({
            "id": "r4",
            "conversation_id": "test-conv-1", 
            "datetime_utc": "2024-01-01T10:02:00Z",
            "prompt": "fourth",
            "response": "response 4",
            "parent_id": "r2",
            "options_json": "{}",
            "model": "gpt-4",
        })

        mock_db["responses"].insert({
            "id": "r5",
            "conversation_id": "test-conv-1", 
            "datetime_utc": "2024-01-01T10:02:00Z",
            "prompt": "fifth",
            "response": "response 5",
            "parent_id": "r4",
            "options_json": "{}",
            "model": "gpt-4",
        })

        # Set head to end of first branch
        mock_db["state"].upsert({"key": "head", "value": "r3"}, pk="key")
        conversation = new_load_conversation("test-conv-1")
        assert len(conversation.responses) == 3
        assert [r.prompt.prompt for r in conversation.responses] == ["first", "second", "third"]

        # Set head to end of second branch
        mock_db["state"].upsert({"key": "head", "value": "r5"}, pk="key")
        conversation = new_load_conversation("test-conv-1")
        assert len(conversation.responses) == 4
        assert [r.prompt.prompt for r in conversation.responses] == ["first", "second", "fourth", "fifth"]



def test_format_conversation(mock_db):
    with patch('llm_head.dag.logs_db_path', return_value=':memory:'), \
         patch('llm_head.dag.sqlite_utils.Database', return_value=mock_db):

        # Add test response to old conv
        mock_db["responses"].insert({
            "id": "r1",
            "conversation_id": "test-conv-1",
            "datetime_utc": "2023-01-01T10:00:00Z",
            "prompt": "test prompt",
            "response": "test response",
            "options_json": "{}",
            "model": "gpt-4",
        })
        
        _, error = format_conversation(mock_db)
        assert error == 'No current head set'

        # Add test conversation
        mock_db["conversations"].insert({
            "id": "test-conv-2",
            "name": "Test Conversation 2",
            "model": "gpt-3.5-turbo"
        }, pk="id")
        
        # Add test response to new conv
        mock_db["responses"].insert({
            "id": "r2",
            "conversation_id": "test-conv-2",
            "datetime_utc": "2024-01-01T10:00:00Z",
            "prompt": "test prompt",
            "response": "test response 2:1",
            "options_json": "{}",
            "model": "gpt-4",
        })
        
        # Test with head ID
        mock_db["state"].insert({"key": "head", "value": "r1"})
        formatted, _ = format_conversation(mock_db)
        assert "Test Conversation" in formatted
        assert "test prompt" in formatted
        assert "test response" in formatted
        assert "-- r1" in formatted
        assert "→ Exchange 1" in formatted

        # Test with missing head
        mock_db["state"].upsert({"key": "head", "value": "nonexistent"}, pk="key")
        formatted, error = format_conversation(mock_db)
        assert error == "Current head response nonexistent not found"

        # Add test response to new conv
        mock_db["responses"].insert({
            "id": "r3",
            "conversation_id": "test-conv-2",
            "datetime_utc": "2024-01-02T10:00:00Z",
            "prompt": "test prompt",
            "parent_id": "r2",
            "response": "test response 2:2",
            "options_json": "{}",
            "model": "gpt-4",
        })
        
        # Test with head ID
        mock_db["state"].upsert({"key": "head", "value": "r3"}, pk="key")
        formatted, _ = format_conversation(mock_db)
        assert "Test Conversation" in formatted
        assert "test prompt" in formatted
        assert "test response" in formatted
        assert "-- r2" in formatted
        assert "-- r3" in formatted
        assert "Exchange 1" in formatted
        assert "→ Exchange 2" in formatted
