from config import TIMEOUT, describe


def test_timeout_value():
    assert TIMEOUT == 30


def test_describe_mentions_timeout():
    assert "30 seconds" in describe()
