"""Tests for save_result API compatibility"""
from unittest.mock import MagicMock
from src.tools.base import ToolResult

class FakeSaveResult:
    """Isolated version of DataCollector._save_result for testing."""
    def __init__(self):
        self.memory = MagicMock()
        self.memory.add_data = MagicMock(return_value=True)
        self.collected_data_list = []
        self.logger = MagicMock()

    def _save_result(self, *args, **kwargs):
        var = args[0] if len(args) > 0 else kwargs.get('var', kwargs.get('variable', kwargs.get('data', kwargs.get('result', None))))
        result_name = args[1] if len(args) > 1 else kwargs.get('result_name', kwargs.get('name', 'Unnamed result'))
        result_description = args[2] if len(args) > 2 else kwargs.get('result_description', kwargs.get('description', ''))
        data_source = args[3] if len(args) > 3 else kwargs.get('data_source', kwargs.get('source', ''))
        if var is None:
            self.logger.warning("save_result called with None data — skipping")
            return
        tool_result = ToolResult(name=result_name, description=result_description, data=var, source=data_source)
        self.memory.add_data(tool_result)
        self.collected_data_list.append(tool_result)

def test_save_result_positional():
    saver = FakeSaveResult()
    saver._save_result("my_data", "Name", "Desc", "Source")
    assert len(saver.collected_data_list) == 1
    assert saver.collected_data_list[0].data == "my_data"

def test_save_result_keyword_variable():
    saver = FakeSaveResult()
    saver._save_result(variable="my_data", name="Name", description="Desc", source="Source")
    assert len(saver.collected_data_list) == 1
    assert saver.collected_data_list[0].data == "my_data"

def test_save_result_keyword_data():
    saver = FakeSaveResult()
    saver._save_result(data="my_data", result_name="Name")
    assert len(saver.collected_data_list) == 1
    assert saver.collected_data_list[0].name == "Name"

def test_save_result_none_skipped():
    saver = FakeSaveResult()
    saver._save_result(None, "Name", "Desc", "Source")
    assert len(saver.collected_data_list) == 0
    saver.logger.warning.assert_called_once()

def test_save_result_mixed_positional_keyword():
    saver = FakeSaveResult()
    saver._save_result("my_data", "Name", description="Desc", source="Src")
    assert len(saver.collected_data_list) == 1
    assert saver.collected_data_list[0].description == "Desc"
