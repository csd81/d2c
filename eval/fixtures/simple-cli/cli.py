"""Minimal CLI fixture for d2c eval tasks."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Say hello.")
    parser.add_argument("name", help="Name to greet")
    args = parser.parse_args()
    print(f"Hello, {args.name}!")


if __name__ == "__main__":
    main()
