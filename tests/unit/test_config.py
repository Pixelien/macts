"""Config loader unit testler."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.config.loader import _resolve_env_vars, load_yaml_config


class TestEnvVarResolution:
    def test_simple_substitution(self) -> None:
        with patch.dict(os.environ, {"FOO": "bar"}):
            assert _resolve_env_vars("${FOO}") == "bar"

    def test_default_value(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert _resolve_env_vars("${MISSING:-default}") == "default"

    def test_nested_dict(self) -> None:
        with patch.dict(os.environ, {"X": "1", "Y": "2"}):
            result = _resolve_env_vars(
                {"a": "${X}", "b": {"c": "${Y}"}}
            )
            assert result == {"a": "1", "b": {"c": "2"}}

    def test_list(self) -> None:
        with patch.dict(os.environ, {"A": "1", "B": "2"}):
            assert _resolve_env_vars(["${A}", "${B}"]) == ["1", "2"]

    def test_no_substitution_for_non_string(self) -> None:
        assert _resolve_env_vars(42) == 42
        assert _resolve_env_vars(3.14) == 3.14
        assert _resolve_env_vars(True) is True
        assert _resolve_env_vars(None) is None


class TestYamlConfigLoading:
    def test_load_example_config(self) -> None:
        path = Path(__file__).parent.parent.parent / "config" / "config.example.yaml"
        if not path.exists():
            pytest.skip(f"Example config bulunamadı: {path}")

        # Test ortamı için minimum env vars
        with patch.dict(
            os.environ,
            {
                "MACTS_MODE": "testnet",
                "MACTS_ENV": "test",
                "LOG_LEVEL": "INFO",
            },
        ):
            config = load_yaml_config(path)

        assert "system" in config
        assert "exchange" in config
        assert config["system"]["mode"] == "testnet"

    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_yaml_config("/nonexistent/path.yaml")
