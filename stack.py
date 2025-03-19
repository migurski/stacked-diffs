#!/usr/bin/env python3
import subprocess
import sys

import networkx


def get_main_sha() -> str:
    output = subprocess.check_output(("git", "rev-parse", "main"))
    return output.decode("utf8").strip()


def get_curr_branch() -> str:
    output = subprocess.check_output(("git", "rev-parse", "--abbrev-ref", "HEAD"))
    return output.decode("utf8").strip()


def get_sha_list() -> list[str]:
    output = subprocess.check_output(("git", "rev-list", "HEAD"))
    lines = output.decode("utf8").split()
    return lines


def main(action, args):
    print(
        action,
        "branch:",
        get_curr_branch(),
        "main:",
        get_main_sha()[:9],
        "list:",
        [s[:9] for s in get_sha_list()],
        "args:",
        args,
    )


if __name__ == "__main__":
    action, *args = sys.argv[1:]
    exit(main(action, args))
