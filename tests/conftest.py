import tempfile
from pathlib import Path

import pytest

from d2c.tools.write_tool import clear_read_files


@pytest.fixture(autouse=True)
def reset_read_files():
    """Clear read-file tracking between tests."""
    clear_read_files()
    yield
    clear_read_files()


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)
