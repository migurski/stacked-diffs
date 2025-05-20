#!/usr/bin/env python3
import argparse
import contextlib
import enum
import functools
import json
import logging
import os
import re
import subprocess
import urllib.parse

import networkx
import requests


PR_PATTERN = re.compile(
    r"^https://api.github.com/repos/(?P<repo>[\w\-]+/[\w\-]+)/pulls/(?P<number>\d+)$"
)

ORIGIN_PATTERN = re.compile(
    r"^origin\tgit@github.com:(?P<repo>[\w\-]+/[\w\-]+).git \(push\)$", re.MULTILINE
)


class Actions(enum.StrEnum):
    post_commit = "post-commit"
    post_checkout = "post-checkout"
    restack = "restack"
    move_onto = "move-onto"
    submit = "submit"
    forget = "forget"


def run_command(*cmd: str, env: dict | None = None):
    logging.info("--> %s", " ".join(cmd))
    subprocess.check_call(cmd, env=env)


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


def get_head_branch() -> tuple[str, str]:
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
    head_branch, head_sha = get_head_branch()
    graph.add_node(main_branch, sha=main_sha)

    yield graph

    for line in networkx.generate_network_text(graph):
        node_id = line.split(" ")[-1].strip()
        graph_node = graph.nodes[node_id]
        node_sha = graph_node["sha"]
        prefix = "━━▶︎" if node_id == head_branch else "   "
        if "pull_url" in graph_node:
            pull_url = PR_PATTERN.sub(
                r"https://github.com/\g<repo>/pull/\g<number>",
                graph_node["pull_url"],
            )
            logging.info("%s %s ┈ %s ● %s", prefix, line, node_sha[:7], pull_url)
        else:
            logging.info("%s %s ┈ %s", prefix, line, node_sha[:7])
    with open(".stack.json", "w") as file:
        json.dump(networkx.node_link_data(graph, edges="edges"), file, indent=2)


def update_branch(graph: networkx.DiGraph, head_branch: str, head_sha: str):
    graph.nodes[head_branch]["sha"] = head_sha
    for parent_branch in graph.predecessors(head_branch):
        base_sha = get_output(("git", "merge-base", parent_branch, head_branch)).strip()
        graph.nodes[head_branch]["base"] = base_sha


def restack_branch(graph: networkx.DiGraph, head_branch: str, head_sha: str):
    if graph.nodes[head_branch]["sha"] != head_sha:
        raise ValueError("Current branch SHA incorrect")
    (parent_branch,) = graph.predecessors(head_branch)
    new_base_sha = graph.nodes[parent_branch]["sha"]
    run_command("git", "rebase", new_base_sha)
    graph.nodes[head_branch]["base"] = new_base_sha


def move_branch(
    graph: networkx.DiGraph, head_branch: str, head_sha: str, new_parent: str
):
    if graph.nodes[head_branch]["sha"] != head_sha:
        raise ValueError("Current branch SHA incorrect")
    (parent_branch,) = graph.predecessors(head_branch)
    logging.info("Move %s %s onto %s", head_branch, head_sha[:7], new_parent)
    old_base_sha = graph.nodes[head_branch]["base"]
    new_base_sha = graph.nodes[new_parent]["sha"]
    try:
        run_command("git", "rebase", "--onto", new_base_sha, old_base_sha, head_branch)
    except subprocess.CalledProcessError as err:
        logging.warning("****** Rebase error: %s ******", err)
    else:
        _, new_head_sha = get_head_branch()
    graph.nodes[head_branch]["base"] = new_base_sha
    graph.nodes[head_branch]["sha"] = new_head_sha
    graph.remove_edge(parent_branch, head_branch)
    graph.add_edge(new_parent, head_branch)

    for child_head in list(graph.successors(head_branch)):
        run_command("git", "checkout", child_head, env=dict(STACKY_STACKY="1"))
        _, child_sha = get_head_branch()
        move_branch(graph, child_head, child_sha, head_branch)

    run_command("git", "checkout", head_branch, env=dict(STACKY_STACKY="1"))


def add_branch(
    graph: networkx.DiGraph, parent_sha: str, head_branch: str, head_sha: str
):
    graph_shas = {graph.nodes[node_id]["sha"]: node_id for node_id in graph.nodes}
    logging.info(
        "Add %s %s %s",
        head_branch,
        head_sha[:7],
        {s[:7]: b for s, b in graph_shas.items()},
    )
    for other_sha in get_sha_list():
        if other_sha == head_sha:
            graph.add_node(head_branch, sha=head_sha, base=other_sha)
            graph.add_edge(graph_shas[parent_sha], head_branch)
            break


def submit_pull_request(graph: networkx.DiGraph, head_branch: str, title=None):
    (parent_branch,) = graph.predecessors(head_branch)
    headers = {"Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN')}"}
    if pull_url := graph.nodes[head_branch].get("pull_url"):
        resp = requests.patch(
            pull_url, json={"head": head_branch, "base": parent_branch}, headers=headers
        )
        if resp.status_code not in range(200, 299):
            raise ValueError(resp.text)
    else:
        if matched := ORIGIN_PATTERN.search(get_output(("git", "remote", "-v"))):
            repo = matched.group("repo")
        else:
            raise ValueError("Could not find github.com origin")
        for wanted_draft_mode in (True, False):
            resp = requests.post(
                f"{args1.github}/repos/{repo}/pulls",
                json={
                    "title": title or "Untitled Pull Request",
                    "head": head_branch,
                    "base": parent_branch,
                    "draft": wanted_draft_mode,
                },
                headers=headers,
            )
            if wanted_draft_mode and resp.status_code == 422:
                # Not all Github repos accept draft PRs
                continue
            else:
                pull_url = urllib.parse.urljoin(args1.github, resp.json()["url"])
                graph.nodes[head_branch]["pull_url"] = pull_url
                break


def forget_branch(graph: networkx.DiGraph, head_branch: str, old_branch: str):
    if old_branch == head_branch:
        raise ValueError(f"Can't forget {old_branch} while on {head_branch}")
    for child_head in list(graph.successors(old_branch)):
        raise ValueError(f"Can't forget {old_branch} while {child_head} exists")
    graph.remove_node(old_branch)


def main(args1: argparse.Namespace, args2: list[str]):
    if "STACKY_STACKY" in os.environ:
        return
    with read_graph() as graph:
        head_branch, head_sha = get_head_branch()
        if head_branch == "HEAD":
            return
        if head_branch in graph.nodes:
            update_branch(graph, head_branch, head_sha)
        if args1.action == Actions.restack:
            assert head_branch in graph.nodes, f"Should know {head_branch}"
            restack_branch(graph, head_branch, head_sha)
        elif args1.action == Actions.move_onto:
            (new_parent,) = args2
            assert new_parent in graph.nodes, f"Should know {new_parent}"
            assert head_branch in graph.nodes, f"Should know {head_branch}"
            move_branch(graph, head_branch, head_sha, new_parent)
        elif args1.action == Actions.submit:
            assert head_branch in graph.nodes, f"Should know {head_branch}"
            submit_pull_request(graph, head_branch, *args2)
        elif args1.action == Actions.forget:
            (old_branch,) = args2
            assert old_branch in graph.nodes, f"Should know {old_branch}"
            assert head_branch in graph.nodes, f"Should know {head_branch}"
            forget_branch(graph, head_branch, old_branch)
        elif args1.action == Actions.post_checkout and head_branch not in graph.nodes:
            parent_sha, is_branch = args2
            if is_branch == "1":
                add_branch(graph, parent_sha, head_branch, head_sha)


parser = argparse.ArgumentParser()
parser.add_argument("action", choices=list(Actions))
parser.add_argument("--github", default="https://api.github.com")


if __name__ == "__main__":
    args1, args2 = parser.parse_known_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    exit(main(args1, args2))
