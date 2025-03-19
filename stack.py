#!/usr/bin/env python3
import contextlib
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


def get_curr_branch() -> tuple[str, str]:
    output1 = subprocess.check_output(("git", "rev-parse", "--abbrev-ref", "HEAD"))
    branchname = output1.decode("utf8").strip()
    output2 = subprocess.check_output(("git", "rev-parse", "HEAD"))
    sha = output2.decode("utf8").strip()
    return branchname, sha


def get_sha_list() -> list[str]:
    output = subprocess.check_output(("git", "rev-list", "HEAD"))
    lines = output.decode("utf8").split()
    return lines


@contextlib.contextmanager
def read_graph():
    if os.path.exists(".stack.json"):
        with open(".stack.json", "r") as file:
            graph = networkx.node_link_graph(json.load(file), edges="edges")
    else:
        graph = networkx.DiGraph()
    main_branch, main_sha = get_main_branch()
    graph.add_node(main_branch, sha=main_sha)
    yield graph
    for line in networkx.generate_network_text(graph):
        node_id = line.split(" ")[-1].strip()
        print(line, graph.nodes[node_id]["sha"][:9])
    with open(".stack.json", "w") as file:
        json.dump(networkx.node_link_data(graph, edges="edges"), file, indent=2)


def main(action, args):
    with read_graph() as graph:
        main_branch, main_sha = get_main_branch()
        curr_branch, curr_sha = get_curr_branch()
        print(
            action,
            "branch:",
            (curr_branch, curr_sha[:9]),
            "main:",
            (main_branch, main_sha[:9]),
            "list:",
            [s[:9] for s in get_sha_list()],
            "args:",
            args,
        )
        if curr_branch == "HEAD":
            pass
        elif curr_branch in graph.nodes:
            graph.nodes[curr_branch]["sha"] = curr_sha
        elif action == "post-checkout" and curr_branch not in graph.nodes:
            graph_shas = {
                graph.nodes[node_id]["sha"]: node_id for node_id in graph.nodes
            }
            print("Add", curr_branch, curr_sha, graph_shas)
            for other_sha in get_sha_list():
                if other_sha == curr_sha:
                    graph.add_node(curr_branch, sha=curr_sha, base=other_sha)
                    graph.add_edge(main_branch, curr_branch)
                    break


if __name__ == "__main__":
    action, *args = sys.argv[1:]
    exit(main(action, args))
