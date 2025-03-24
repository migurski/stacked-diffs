#!/usr/bin/env python3
import argparse
import contextlib
import enum
import functools
import json
import logging
import os
import subprocess

import networkx


class Actions(enum.StrEnum):
    post_commit = "post-commit"
    post_checkout = "post-checkout"
    restack = "restack"
    move_onto = "move-onto"


def run_command(cmd: tuple[str]):
    print("-->", " ".join(cmd))
    subprocess.check_call(cmd)


def get_output(cmd: tuple[str]) -> str:
    return subprocess.check_output(cmd, stderr=subprocess.PIPE).decode("utf8")


@functools.cache
def get_main_branch() -> tuple[str, str]:
    try:
        sha = get_output(("git", "rev-parse", "main")).strip()
    except subprocess.CalledProcessError:
        sha = get_output(("git", "rev-parse", "master")).strip()
        branchname = "master"
    else:
        branchname = "main"
    return branchname, sha


@functools.cache
def get_curr_branch() -> tuple[str, str]:
    branchname = get_output(("git", "rev-parse", "--abbrev-ref", "HEAD")).strip()
    sha = get_output(("git", "rev-parse", "HEAD")).strip()
    return branchname, sha


@functools.cache
def get_sha_list() -> list[str]:
    lines = get_output(("git", "rev-list", "HEAD")).split()
    return lines


@contextlib.contextmanager
def read_graph():
    if os.path.exists(".stack.json"):
        with open(".stack.json", "r") as file:
            graph = networkx.node_link_graph(json.load(file), edges="edges")
    else:
        graph = networkx.DiGraph()
    main_branch, main_sha = get_main_branch()
    curr_branch, curr_sha = get_curr_branch()
    graph.add_node(main_branch, sha=main_sha)

    yield graph

    for line in networkx.generate_network_text(graph):
        node_id = line.split(" ")[-1].strip()
        node_sha = graph.nodes[node_id]["sha"]
        print("==>" if node_sha == curr_sha else "   ", line, node_sha[:7])
    with open(".stack.json", "w") as file:
        json.dump(networkx.node_link_data(graph, edges="edges"), file, indent=2)


def update_branch(graph: networkx.DiGraph, curr_branch: str, curr_sha: str):
    graph.nodes[curr_branch]["sha"] = curr_sha
    for parent_branch in graph.predecessors(curr_branch):
        base_sha = get_output(("git", "merge-base", parent_branch, curr_branch)).strip()
        graph.nodes[curr_branch]["base"] = base_sha


def restack_branch(graph: networkx.DiGraph, curr_branch: str, curr_sha: str):
    if graph.nodes[curr_branch]["sha"] != curr_sha:
        raise ValueError("Current branch SHA incorrect")
    (parent_branch,) = graph.predecessors(curr_branch)
    new_base_sha = graph.nodes[parent_branch]["sha"]
    run_command(("git", "rebase", new_base_sha))
    graph.nodes[curr_branch]["base"] = new_base_sha


def move_branch(
    graph: networkx.DiGraph, curr_branch: str, curr_sha: str, new_parent: str
):
    if graph.nodes[curr_branch]["sha"] != curr_sha:
        raise ValueError("Current branch SHA incorrect")
    (parent_branch,) = graph.predecessors(curr_branch)
    if new_parent == parent_branch:
        # No-op
        return
    logging.info("Move %s %s onto %s", curr_branch, curr_sha[:9], new_parent)
    new_base_sha = graph.nodes[new_parent]["sha"]
    run_command(("git", "rebase", new_base_sha))
    graph.nodes[curr_branch]["base"] = new_base_sha
    graph.remove_edge(parent_branch, curr_branch)
    graph.add_edge(new_parent, curr_branch)


def add_branch(
    graph: networkx.DiGraph, parent_sha: str, curr_branch: str, curr_sha: str
):
    graph_shas = {graph.nodes[node_id]["sha"]: node_id for node_id in graph.nodes}
    logging.info(
        "Add %s %s %s",
        curr_branch,
        curr_sha[:9],
        {s[:9]: b for s, b in graph_shas.items()},
    )
    for other_sha in get_sha_list():
        if other_sha == curr_sha:
            graph.add_node(curr_branch, sha=curr_sha, base=other_sha)
            graph.add_edge(graph_shas[parent_sha], curr_branch)
            break


def main(action, args):
    with read_graph() as graph:
        curr_branch, curr_sha = get_curr_branch()
        if curr_branch == "HEAD":
            return
        if curr_branch in graph.nodes:
            update_branch(graph, curr_branch, curr_sha)
        if action == Actions.restack:
            if curr_branch in graph.nodes:
                restack_branch(graph, curr_branch, curr_sha)
            else:
                raise NotImplementedError()
        elif action == Actions.move_onto:
            (new_parent,) = args
            if new_parent in graph.nodes:
                move_branch(graph, curr_branch, curr_sha, new_parent)
            else:
                raise NotImplementedError()
        elif action == Actions.post_checkout and curr_branch not in graph.nodes:
            parent_sha, is_branch = args
            if is_branch == "1":
                add_branch(graph, parent_sha, curr_branch, curr_sha)


parser = argparse.ArgumentParser()
parser.add_argument("action", choices=list(Actions))


if __name__ == "__main__":
    args1, args2 = parser.parse_known_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    exit(main(args1.action, args2))
