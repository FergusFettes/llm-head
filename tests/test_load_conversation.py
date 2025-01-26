import pytest
from unittest.mock import Mock, patch
from llm.models import Response, Conversation
import sqlite_utils
import click
from llm_head import new_load_conversation, get_parent_id, get_most_recent_active_conversation

@pytest.fixture
def mock_db():
    db = Mock()
    db.query = Mock()
    return db

@pytest.fixture
def mock_response():
    return {
        "id": "resp1",
        "conversation_id": "conv1",
        "datetime_utc": "2024-01-01T00:00:00",
        "prompt": "test prompt",
        "response": "test response",
        "model": "test-model",
        "system": None,
        "parent_id": None
    }

@pytest.fixture
def mock_conversation():
    return {
        "id": "conv1",
        "name": "Test Conversation"
    }

def test_load_conversation_no_conversations(mock_db):
    with patch('sqlite_utils.Database') as mock_db_class:
        mock_db_class.return_value = mock_db
        mock_db.query.return_value = iter([])
        
        result = new_load_conversation(None)
        assert result is None

def test_load_conversation_not_found(mock_db):
    with patch('sqlite_utils.Database') as mock_db_class:
        mock_db_class.return_value = mock_db
        mock_db["conversations"].get.side_effect = sqlite_utils.db.NotFoundError
        
        with pytest.raises(click.ClickException) as exc:
            new_load_conversation("nonexistent")
        assert "No conversation found with id=nonexistent" in str(exc.value)

def test_load_conversation_empty(mock_db, mock_conversation):
    with patch('sqlite_utils.Database') as mock_db_class:
        mock_db_class.return_value = mock_db
        mock_db["conversations"].get.return_value = mock_conversation
        mock_db["responses"].rows_where.return_value = []
        
        result = new_load_conversation("conv1")
        assert isinstance(result, Conversation)
        assert result.id == "conv1"
        assert len(result.responses) == 0

def test_load_conversation_with_responses(mock_db, mock_conversation, mock_response):
    with patch('sqlite_utils.Database') as mock_db_class:
        mock_db_class.return_value = mock_db
        mock_db["conversations"].get.return_value = mock_conversation
        mock_db["responses"].rows_where.return_value = [mock_response]
        mock_db["state"].get.side_effect = sqlite_utils.db.NotFoundError
        mock_db.query.return_value = iter([])
        
        result = new_load_conversation("conv1")
        assert isinstance(result, Conversation)
        assert result.id == "conv1"
        assert len(result.responses) == 1
        assert result.responses[0].id == "resp1"

def test_get_most_recent_active_conversation(mock_db):
    mock_db.query.return_value = iter([{"conversation_id": "recent_conv"}])
    result = get_most_recent_active_conversation(mock_db)
    assert result == "recent_conv"

def test_get_most_recent_active_conversation_empty(mock_db):
    mock_db.query.return_value = iter([])
    result = get_most_recent_active_conversation(mock_db)
    assert result is None

def test_get_parent_id_with_direct_parent(mock_db, mock_response):
    response = Response.from_row(mock_response)
    response.parent_id = "parent1"
    mock_db.query.return_value = iter([{"id": "parent1"}])
    
    result = get_parent_id(response, mock_db)
    assert result == "parent1"

def test_get_parent_id_no_parent(mock_db, mock_response):
    response = Response.from_row(mock_response)
    mock_db.query.return_value = iter([])
    
    result = get_parent_id(response, mock_db)
    assert result is None
