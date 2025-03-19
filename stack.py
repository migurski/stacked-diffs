#!/usr/bin/env python3
import json
import os
import subprocess
import sys

import networkx


def get_main_branch() -> tuple[str, str]:
    try:
        output = subprocess.check_output(("git", "rev-parse", "main"))
    except subprocess.CalledProcessError:
        output = subprocess.check_output(("git", "rev-parse", "master"))
        branchname = "master"
    else:
        branchname = "main"
    return branchname, output.decode("utf8").strip()


def get_curr_branch() -> str:
    output = subprocess.check_output(("git", "rev-parse", "--abbrev-ref", "HEAD"))
    return output.decode("utf8").strip()


def get_sha_list() -> list[str]:
    output = subprocess.check_output(("git", "rev-list", "HEAD"))
    lines = output.decode("utf8").split()
    return lines


def read_graph() -> networkx.DiGraph:
    if not os.path.exists(".stack.json"):
        return networkx.DiGraph()
    with open(".stack.json", "r") as file:
        return networkx.node_link_graph(json.load(file), edges="edges")


def main(action, args):
    graph = read_graph()
    main_branch, main_sha = get_main_branch()
    graph.add_node(main_branch, sha=main_sha)
    print(
        action,
        "branch:",
        get_curr_branch(),
        "main:",
        (main_branch, main_sha[:9]),
        "list:",
        [s[:9] for s in get_sha_list()],
        "args:",
        args,
    )
    for line in networkx.generate_network_text(graph):
        node_id = line.split("─")[-1].strip()
        print(line, graph.nodes[node_id]["sha"][:9])


if __name__ == "__main__":
    action, *args = sys.argv[1:]
    exit(main(action, args))
