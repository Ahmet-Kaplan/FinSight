"""Smoke tests for backward compatibility"""
import pytest
import os

@pytest.mark.integration
def test_config_loads_existing_yaml():
    """Config class should load my_config.yaml without error."""
    if not os.path.exists('my_config.yaml'):
        pytest.skip("my_config.yaml not found")
    from src.config import Config
    config = Config('my_config.yaml')
    assert config.config is not None
    assert 'target_name' in config.config
