"""Create a sanitized local KAVI runtime-hook attestation JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from spectraflow.reliability.hook_attestation import inspect_runner_source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner-file", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    result = inspect_runner_source(args.runner_file)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"

    if args.output:
        target = Path(args.output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
