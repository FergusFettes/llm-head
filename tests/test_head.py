import pytest
import sqlite_utils
from llm.plugins import pm
from llm_head import new_load_conversation, m012_track_current_conversation
from unittest.mock import patch
import datetime


def test_plugin_is_installed():
    names = [mod.__name__ for mod in pm.get_plugins()]
    assert "llm_head" in names


@pytest.fixture
def mock_db():
    db = sqlite_utils.Database(memory=True)
    # Create required tables
    db["conversations"].create({
        "id": str,
        "model": str,
        "system": str,
        "created_at": str
    }, pk="id")
    
    db["responses"].create({
        "id": str,
        "conversation_id": str,
        "prompt": str,
        "response": str,
        "model": str,
        "datetime_utc": str,
        "parent_id": str
    }, pk="id")
    
    db["state"].create({
        "key": str,
        "value": str
    }, pk="key")
    
    # Add test conversation
    db["conversations"].insert({
        "id": "test-conv-1",
        "model": "gpt-3.5-turbo",
        "system": "You are a helpful assistant",
        "created_at": "2024-01-01T00:00:00"
    })
    
    return db


def test_load_conversation_by_id(mock_db):
    with patch('llm_head.logs_db_path', return_value=':memory:'), \
         patch('llm_head.sqlite_utils.Database', return_value=mock_db):
        
        conversation = new_load_conversation("test-conv-1")
        assert conversation is not None
        assert conversation.id == "test-conv-1"
        assert conversation.model == "gpt-3.5-turbo"
        assert conversation.system == "You are a helpful assistant"
