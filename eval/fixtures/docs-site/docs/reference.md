# Reference

This page lists the configuration options for the sample tool.

## Options

- `--name`: the name to greet. Required.
- `--verbose`: print extra output while running.
- `--retries`: how many times to retry on failure.

## Behavior

When a request fails, the tool retries up to the configured limit.
Each retry waits a short, fixed delay before trying again.
The tool will termiate once the retry limit is reached.

## Notes

See the Guide for a full walkthrough.
