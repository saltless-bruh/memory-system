"""Default pytest execution must remain offline unless explicitly overridden."""

import tomllib
from pathlib import Path


def test_pytest_disables_network_sockets_by_default() -> None:
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = config["tool"]["pytest"]["ini_options"]["addopts"].split()
    assert "--disable-socket" in addopts
    assert "--allow-unix-socket" in addopts
