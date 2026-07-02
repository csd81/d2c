"""Tiny greeter package used as a d2c eval fixture."""

from greeter.util import add, multiply, subtract

__all__ = ["add", "subtract", "multiply"]


def main() -> None:
    print(add(1, 2))
