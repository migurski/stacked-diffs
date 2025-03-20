#!/usr/bin/env python3
import contextlib
import functools
import json
import os
import subprocess
import sys

import networkx


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
    for parent_branch in graph.predecessors(curr_branch):
        new_base_sha = graph.nodes[parent_branch]["sha"]
        run_command(("git", "rebase", new_base_sha))
        graph.nodes[curr_branch]["base"] = new_base_sha


def add_branch(
    graph: networkx.DiGraph, main_branch: str, curr_branch: str, curr_sha: str
):
    graph_shas = {graph.nodes[node_id]["sha"]: node_id for node_id in graph.nodes}
    print("Add", curr_branch, curr_sha, graph_shas)
    for other_sha in get_sha_list():
        if other_sha == curr_sha:
            graph.add_node(curr_branch, sha=curr_sha, base=other_sha)
            graph.add_edge(main_branch, curr_branch)
            break


def main(action, args):
    with read_graph() as graph:
        curr_branch, curr_sha = get_curr_branch()
        if curr_branch == "HEAD":
            return
        if curr_branch in graph.nodes:
            update_branch(graph, curr_branch, curr_sha)
        if action == "restack":
            if curr_branch in graph.nodes:
                restack_branch(graph, curr_branch, curr_sha)
            else:
                raise NotImplementedError()
        elif action == "post-checkout" and curr_branch not in graph.nodes:
            _, is_branch = args
            if is_branch == "1":
                main_branch, main_sha = get_main_branch()
                add_branch(graph, main_branch, curr_branch, curr_sha)


if __name__ == "__main__":
    action, *args = sys.argv[1:]
    exit(main(action, args))
