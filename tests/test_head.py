import pytest
import sqlite_utils
from llm.plugins import pm
from llm_head import new_load_conversation, m012_track_current_conversation
from unittest.mock import patch
from llm.migrations import migrate


def test_plugin_is_installed():
    names = [mod.__name__ for mod in pm.get_plugins()]
    assert "llm_head" in names


@pytest.fixture
def mock_db():
    db = sqlite_utils.Database(':memory:')
    migrate(db)
    
    # Add test conversation
    db["conversations"].insert({
        "id": "test-conv-1",
        "name": "Test Conversation",
        "model": "gpt-3.5-turbo"
    })
    
    return db


def test_load_conversation_by_id(mock_db):
    with patch('llm_head.logs_db_path', return_value=':memory:'), \
         patch('llm_head.sqlite_utils.Database', return_value=mock_db):
        
        conversation = new_load_conversation("test-conv-1")
        assert conversation is not None
        assert conversation.id == "test-conv-1"
        assert "gpt-3.5-turbo" in str(conversation.model)
        assert conversation.name == "Test Conversation"
