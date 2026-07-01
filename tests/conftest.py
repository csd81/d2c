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


@pytest.fixture(autouse=True)
def reset_trust():
    """Reset the global trust gate between tests."""
    from d2c.trust import reset_trust_gate

    reset_trust_gate()
    yield
    reset_trust_gate()


@pytest.fixture(autouse=True)
def reset_usage_tracker():
    """Reset the global usage tracker between tests (Phase 55)."""
    from d2c.usage import set_usage_tracker

    set_usage_tracker(None)
    yield
    set_usage_tracker(None)


@pytest.fixture(autouse=True)
def isolate_approvals_path(monkeypatch, tmp_path):
    """Phase 64: ApprovalCache can persist to disk. Redirect the default
    location for every test so nothing ever reads/writes the real
    ~/.d2c/approvals.json — matches the reset_trust/reset_usage_tracker
    isolation pattern above."""
    import d2c.approvals as approvals_mod

    monkeypatch.setattr(approvals_mod, "DEFAULT_APPROVALS_PATH", tmp_path / "approvals.json")


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def trusted_gate(tmp_dir):
    """Create a trust gate that has decided True."""
    from d2c.trust import WorkSpaceTrustGate, set_trust_gate

    gate = WorkSpaceTrustGate(tmp_dir)
    gate.decide(True)
    set_trust_gate(gate)
    return gate


@pytest.fixture
def untrusted_gate(tmp_dir):
    """Create a trust gate that has decided False."""
    from d2c.trust import WorkSpaceTrustGate, set_trust_gate

    gate = WorkSpaceTrustGate(tmp_dir)
    gate.decide(False)
    set_trust_gate(gate)
    return gate
