"""Tests for result deduplication in variable_memory"""
from src.tools.base import ToolResult

class FakeMemory:
    """Minimal stand-in for Memory.add_data dedup logic."""
    def __init__(self):
        self.data = []

    @staticmethod
    def _content_fingerprint(data) -> str:
        import hashlib
        preview = str(data)[:500]
        return hashlib.md5(preview.encode('utf-8', errors='replace')).hexdigest()

    def add_data(self, data):
        if isinstance(data, ToolResult):
            new_fp = self._content_fingerprint(data.data)
            for existing in self.data:
                if not isinstance(existing, ToolResult):
                    continue
                if existing.name == data.name and existing.source == data.source:
                    return False
                if existing.name == data.name and self._content_fingerprint(existing.data) == new_fp:
                    return False
        self.data.append(data)
        return True

def test_add_data_no_dup():
    mem = FakeMemory()
    a = ToolResult(name="A", description="desc", data="data1", source="src1")
    b = ToolResult(name="B", description="desc", data="data2", source="src2")
    assert mem.add_data(a) is True
    assert mem.add_data(b) is True
    assert len(mem.data) == 2

def test_add_data_same_name_source():
    mem = FakeMemory()
    a = ToolResult(name="A", description="desc", data="data1", source="src1")
    b = ToolResult(name="A", description="different", data="different", source="src1")
    assert mem.add_data(a) is True
    assert mem.add_data(b) is False
    assert len(mem.data) == 1

def test_add_data_same_name_content():
    mem = FakeMemory()
    a = ToolResult(name="A", description="desc", data="same_content", source="src1")
    b = ToolResult(name="A", description="different", data="same_content", source="src2")
    assert mem.add_data(a) is True
    assert mem.add_data(b) is False
    assert len(mem.data) == 1

def test_add_data_different_content():
    mem = FakeMemory()
    a = ToolResult(name="A", description="desc", data="content_1", source="src1")
    b = ToolResult(name="A", description="desc", data="completely_different_content", source="src2")
    assert mem.add_data(a) is True
    assert mem.add_data(b) is True
    assert len(mem.data) == 2
