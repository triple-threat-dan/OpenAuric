import json
import logging
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from auric.core.session_router import SessionRouter


@pytest.fixture
def temp_storage(tmp_path):
    return tmp_path / "active_sessions.json"


@pytest.fixture
def router(temp_storage):
    return SessionRouter(storage_path=temp_storage)


def test_init_with_path(temp_storage):
    router = SessionRouter(storage_path=temp_storage)
    assert router.storage_path == temp_storage
    assert router.active_sessions == {}
    assert router._closed_contexts == set()


def test_init_without_path(tmp_path):
    with patch("auric.core.session_router.AURIC_ROOT", tmp_path):
        router = SessionRouter()
        assert router.storage_path == tmp_path / "active_sessions.json"


def test_load_no_file(temp_storage):
    router = SessionRouter(storage_path=temp_storage)
    # _load is called in __init__, but since file doesn't exist, it should be empty
    assert router.active_sessions == {}
    assert router._closed_contexts == set()


def test_load_legacy_format(temp_storage):
    legacy_data = {"context1": "session1", "context2": "session2"}
    temp_storage.write_text(json.dumps(legacy_data), encoding="utf-8")
    
    router = SessionRouter(storage_path=temp_storage)
    assert router.active_sessions == legacy_data
    assert router._closed_contexts == set()


def test_load_new_format(temp_storage):
    new_data = {
        "active_sessions": {"context1": "session1"},
        "closed_contexts": ["context2"]
    }
    temp_storage.write_text(json.dumps(new_data), encoding="utf-8")
    
    router = SessionRouter(storage_path=temp_storage)
    assert router.active_sessions == {"context1": "session1"}
    assert router._closed_contexts == {"context2"}


def test_load_invalid_format(temp_storage):
    # Data is a list, which is invalid for the router
    temp_storage.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    
    router = SessionRouter(storage_path=temp_storage)
    assert router.active_sessions == {}
    assert router._closed_contexts == set()


def test_load_corruption(temp_storage, caplog):
    temp_storage.write_text("not json at all", encoding="utf-8")
    
    with caplog.at_level(logging.ERROR):
        router = SessionRouter(storage_path=temp_storage)
        assert "Failed to load active sessions" in caplog.text
        assert router.active_sessions == {}


def test_save_success(temp_storage):
    router = SessionRouter(storage_path=temp_storage)
    router.active_sessions = {"c1": "s1"}
    router._closed_contexts = {"c2"}
    router._save()
    
    with open(temp_storage, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    assert data["active_sessions"] == {"c1": "s1"}
    assert "c2" in data["closed_contexts"]


def test_save_error(temp_storage, caplog):
    router = SessionRouter(storage_path=temp_storage)
    # Use a directory as path to trigger OSError
    bad_path = temp_storage.parent / "subdir"
    bad_path.mkdir()
    router.storage_path = bad_path
    
    with caplog.at_level(logging.ERROR):
        router._save()
        assert "Failed to save active sessions" in caplog.text


def test_get_active_session_id_new_context(router):
    context = "user_123"
    sid = router.get_active_session_id(context)
    assert sid is not None
    assert router.active_sessions[context] == sid
    assert router.storage_path.exists()


def test_get_active_session_id_existing_context(router):
    context = "user_123"
    sid1 = router.get_active_session_id(context)
    sid2 = router.get_active_session_id(context)
    assert sid1 == sid2


def test_get_active_session_id_closed_context(router):
    context = "user_123"
    router.get_active_session_id(context) # Create it
    router.close_session(context) # Marks as closed
    
    sid = router.get_active_session_id(context)
    assert sid is None


def test_start_new_session_fresh(router):
    context = "user_123"
    sid = router.start_new_session(context)
    assert sid is not None
    assert router.active_sessions[context] == sid


def test_start_new_session_rotation(router):
    context = "user_123"
    sid1 = router.get_active_session_id(context)
    sid2 = router.start_new_session(context)
    assert sid1 != sid2
    assert router.active_sessions[context] == sid2


def test_start_new_session_clears_closed(router):
    context = "user_123"
    router.get_active_session_id(context) # Create it
    router.close_session(context)
    assert router.is_context_closed(context)
    
    sid = router.start_new_session(context)
    assert sid is not None
    assert not router.is_context_closed(context)


def test_close_session_existing(router):
    context = "user_123"
    sid = router.get_active_session_id(context)
    
    closed_sid = router.close_session(context)
    assert closed_sid == sid
    assert context not in router.active_sessions
    assert router.is_context_closed(context)


def test_close_session_missing(router, caplog):
    context = "nonexistent"
    with caplog.at_level(logging.WARNING):
        closed_sid = router.close_session(context)
        assert closed_sid is None
        assert "No active session to close" in caplog.text


def test_close_all_sessions(router):
    router.get_active_session_id("c1")
    router.get_active_session_id("c2")
    
    closed_pairs = router.close_all_sessions()
    assert len(closed_pairs) == 2
    assert router.active_sessions == {}
    assert router.is_context_closed("c1")
    assert router.is_context_closed("c2")


def test_list_active_contexts(router):
    router.get_active_session_id("c1")
    router.get_active_session_id("c2")
    
    contexts = router.list_active_contexts()
    assert len(contexts) == 2
    assert "c1" in contexts
    assert "c2" in contexts
    
    # Ensure it's a copy
    contexts["c3"] = "s3"
    assert "c3" not in router.active_sessions


def test_get_all_active_session_ids(router):
    s1 = router.get_active_session_id("c1")
    s2 = router.get_active_session_id("c2")
    
    sids = router.get_all_active_session_ids()
    assert sids == {s1, s2}
