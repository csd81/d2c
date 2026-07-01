"""Phase 48: version metadata and --version CLI."""

import re
import subprocess
import sys

import pytest

import d2c


def test_version_is_nonempty_semverish():
    v = d2c.__version__
    assert isinstance(v, str) and v
    # major.minor.patch with optional pre-release/build suffix
    assert re.match(r"^\d+\.\d+\.\d+([.\-+].*)?$", v), v


def test_cli_version_matches_package_and_exits_before_loop():
    # argparse `version` action prints and exits(0) before any config/API/loop.
    out = subprocess.run(
        [sys.executable, "-m", "d2c", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == f"d2c {d2c.__version__}"


def test_installed_metadata_version_matches():
    # If d2c is installed (editable in dev), its distribution metadata version
    # should match the source __version__ (single source of truth).
    from importlib.metadata import PackageNotFoundError, version

    try:
        meta_version = version("d2c")
    except PackageNotFoundError:
        pytest.skip("d2c not installed as a distribution")
    assert meta_version == d2c.__version__
