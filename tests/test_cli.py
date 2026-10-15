"""The `operaitor` command line: version, doctor, serve settings and pass-through tools."""
from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

from operaitor import __version__, cli


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0 and __version__ in capsys.readouterr().out


def test_a_command_is_required(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main([])
    assert e.value.code == 2


def test_doctor_reports_the_installation(capsys):
    code = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert code in (0, 1) and f"operaitor {__version__}" in out and "numpy" in out and "fastapi" in out


def test_serve_public_mode_sets_the_environment():
    calls = []
    with mock.patch.dict(os.environ, {}, clear=False), mock.patch("uvicorn.run", lambda *a, **k: calls.append((a, k))):
        for var in ("PLANT_HOSTED", "PLANT_CAMPAIGN_DIR", "PLANT_MAX_SESSIONS"):
            os.environ.pop(var, None)
        cli.main(["serve", "--hosted", "--host", "127.0.0.1", "--port", "9123", "--max-sessions", "3", "--llm", "off"])
        assert os.environ["PLANT_HOSTED"] == "1"
        assert os.environ["PLANT_CAMPAIGN_DIR"] == ""          # public mode writes nothing to disk
        assert os.environ["PLANT_MAX_SESSIONS"] == "3" and os.environ["PLANT_LLM"] == "off"
    (args, kwargs), = calls
    assert args == ("operaitor.server.app:app",) and kwargs["host"] == "127.0.0.1" and kwargs["port"] == 9123


def test_pass_through_tools_get_their_own_arguments():
    seen = {}

    def fake_main():
        seen["argv"] = list(sys.argv)
        return 3

    with mock.patch("operaitor.benchmark.profit_campaign.main", fake_main), mock.patch.object(sys, "argv", ["x"]):
        assert cli.main(["benchmark", "--phase", "report", "--ids", "S01"]) == 3
    assert seen["argv"] == ["operaitor benchmark", "--phase", "report", "--ids", "S01"]
