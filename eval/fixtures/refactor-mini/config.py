"""Tiny config module used by the refactor-mini eval fixture."""

# Request timeout in seconds. Referenced in README.md and tests/test_config.py;
# a repeated-literal update task changes this in all three places.
TIMEOUT = 30


def describe() -> str:
    return f"timeout is {TIMEOUT} seconds"
