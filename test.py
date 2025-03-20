import contextlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest

import networkx


PYTHON_STACK_PY = "{0} {1}".format(
    shlex.quote(sys.executable),
    shlex.quote(os.path.join(os.path.dirname(__file__), "stack.py")),
)


def run_cmd(cmd, quiet=True):
    pipe_kwargs = dict(stderr=subprocess.PIPE, stdout=subprocess.PIPE) if quiet else {}
    for line in cmd.strip().split("\n"):
        subprocess.check_call(line.strip(), shell=True, **pipe_kwargs)


def add_hooks(repodir):
    with open(os.path.join(repodir, ".git/hooks/post-commit"), "w") as hook_ci:
        hook_ci.write(f"#!/bin/bash -ex\n{PYTHON_STACK_PY} post-commit")
    with open(os.path.join(repodir, ".git/hooks/post-checkout"), "w") as hook_co:
        hook_co.write(f"#!/bin/bash -ex\n{PYTHON_STACK_PY} post-checkout $2 $3")
    os.chmod(hook_ci.name, 0o775)
    os.chmod(hook_co.name, 0o775)


@contextlib.contextmanager
def fresh_repo():
    with tempfile.TemporaryDirectory() as tempdir:
        os.chdir(tempdir)
        run_cmd("git init")
        add_hooks(tempdir)
        yield tempdir


class TestRepo(unittest.TestCase):
    def test_one_branch(self):
        with fresh_repo() as repodir:
            run_cmd("git commit -m 'empty' --allow-empty")
            with open(os.path.join(repodir, ".stack.json")) as file:
                graph = networkx.node_link_graph(json.load(file), edges="edges")
        self.assertEqual(len(graph.nodes), 1)

    def test_two_branches(self):
        """One branch simply extends main"""
        with fresh_repo() as repodir:
            run_cmd("""
                git commit -m one --allow-empty
                git checkout -b branch/1
                git commit -m two --allow-empty
                git checkout main
                """)
            with open(os.path.join(repodir, ".stack.json")) as file:
                graph = networkx.node_link_graph(json.load(file), edges="edges")
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(list(graph.successors("main")), ["branch/1"])
        self.assertEqual(graph.nodes["branch/1"]["base"], graph.nodes["main"]["sha"])

    def test_two_branches_no_ff(self):
        """One branch diverges slightly from main"""
        with fresh_repo() as repodir:
            run_cmd("""
                git commit -m one --allow-empty
                git checkout -b branch/1
                git commit -m two --allow-empty
                git checkout main
                git commit -m three --allow-empty
                git checkout branch/1
                git commit -m four --allow-empty
                """)
            with open(os.path.join(repodir, ".stack.json")) as file:
                graph = networkx.node_link_graph(json.load(file), edges="edges")
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(list(graph.successors("main")), ["branch/1"])
        self.assertNotEqual(graph.nodes["branch/1"]["base"], graph.nodes["main"]["sha"])

    def test_two_branches_ff_ok(self):
        """One branch simply extends main after a merge"""
        with fresh_repo() as repodir:
            run_cmd("""
                git commit -m one --allow-empty
                git checkout -b branch/1
                git checkout main
                git commit -m two --allow-empty
                git checkout branch/1
                git merge main
                git commit -m three --allow-empty
                """)
            with open(os.path.join(repodir, ".stack.json")) as file:
                graph = networkx.node_link_graph(json.load(file), edges="edges")
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(list(graph.successors("main")), ["branch/1"])
        self.assertEqual(graph.nodes["branch/1"]["base"], graph.nodes["main"]["sha"])

    def test_two_branches_restack(self):
        """One branch diverges slightly from main"""
        with fresh_repo() as repodir:
            run_cmd(f"""
                git commit -m one --allow-empty
                git checkout -b branch/1
                git commit -m two --allow-empty
                git checkout main
                git commit -m three --allow-empty
                git checkout branch/1
                git commit -m four --allow-empty
                {PYTHON_STACK_PY} restack
                """)

            with open(os.path.join(repodir, ".stack.json")) as file:
                graph = networkx.node_link_graph(json.load(file), edges="edges")
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(list(graph.successors("main")), ["branch/1"])
        self.assertEqual(graph.nodes["branch/1"]["base"], graph.nodes["main"]["sha"])
