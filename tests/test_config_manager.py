"""Tests for config_manager YAML helpers."""

from unittest.mock import patch

from custom_components.mcp_server_http_transport.config_manager import (
    _load_yaml_dict,
    _load_yaml_list,
)


class TestLoadYamlList:
    """Tests for _load_yaml_list."""

    def test_returns_empty_list_when_file_missing(self, tmp_path):
        """Test returns empty list when file does not exist."""
        result = _load_yaml_list(str(tmp_path / "nonexistent.yaml"))
        assert result == []

    def test_returns_empty_list_when_data_is_none(self, tmp_path):
        """Test returns empty list when YAML loads as None."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager.os.path.isfile",
                return_value=True,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_loader.load_yaml",
                return_value=None,
            ),
        ):
            result = _load_yaml_list("/fake/path.yaml")
        assert result == []

    def test_returns_list_when_data_is_list(self, tmp_path):
        """Test returns the list when YAML loads as a list."""
        expected = [{"id": "1", "alias": "Test"}]
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager.os.path.isfile",
                return_value=True,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_loader.load_yaml",
                return_value=expected,
            ),
        ):
            result = _load_yaml_list("/fake/path.yaml")
        assert result == expected

    def test_returns_empty_list_when_data_is_not_list(self, tmp_path):
        """Test returns empty list when YAML loads as non-list type."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager.os.path.isfile",
                return_value=True,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_loader.load_yaml",
                return_value="a string",
            ),
        ):
            result = _load_yaml_list("/fake/path.yaml")
        assert result == []


class TestLoadYamlDict:
    """Tests for _load_yaml_dict."""

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        """Test returns empty dict when file does not exist."""
        result = _load_yaml_dict(str(tmp_path / "nonexistent.yaml"))
        assert result == {}

    def test_returns_empty_dict_when_data_is_none(self):
        """Test returns empty dict when YAML loads as None."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager.os.path.isfile",
                return_value=True,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_loader.load_yaml",
                return_value=None,
            ),
        ):
            result = _load_yaml_dict("/fake/path.yaml")
        assert result == {}

    def test_returns_dict_when_data_is_dict(self):
        """Test returns the dict when YAML loads as a dict."""
        expected = {"script_1": {"alias": "Test"}}
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager.os.path.isfile",
                return_value=True,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_loader.load_yaml",
                return_value=expected,
            ),
        ):
            result = _load_yaml_dict("/fake/path.yaml")
        assert result == expected

    def test_returns_empty_dict_when_data_is_not_dict(self):
        """Test returns empty dict when YAML loads as non-dict type."""
        with (
            patch(
                "custom_components.mcp_server_http_transport.config_manager.os.path.isfile",
                return_value=True,
            ),
            patch(
                "custom_components.mcp_server_http_transport.config_manager.yaml_loader.load_yaml",
                return_value=[1, 2, 3],
            ),
        ):
            result = _load_yaml_dict("/fake/path.yaml")
        assert result == {}
