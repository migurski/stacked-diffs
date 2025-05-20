import contextlib
import json
import http.server
import itertools
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid

import networkx


PYTHON_STACK_PY = "{0} {1}".format(
    shlex.quote(sys.executable),
    shlex.quote(os.path.join(os.path.dirname(__file__), "stack.py")),
)


def run_cmd(cmd: str, quiet=True):
    pipe_kwargs = dict(stderr=subprocess.PIPE, stdout=subprocess.PIPE) if quiet else {}
    for line in cmd.strip().split("\n"):
        command = line.strip()
        if command.startswith("git push origin "):
            # Skip these, there is nowhere to push to
            continue
        subprocess.check_call(command, shell=True, env={}, **pipe_kwargs)


def get_output(*cmd: str):
    return subprocess.check_output(cmd, stderr=subprocess.PIPE).decode("utf8")


def get_git_log():
    return get_output("git", "log", "--pretty=%s (%D)").strip().split("\n")


def get_stack_graph():
    with open(".stack.json") as file:
        graph = networkx.node_link_graph(json.load(file), edges="edges")
    return graph


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
        run_cmd("git remote add origin git@github.com:migurski/temp.git")
        add_hooks(tempdir)
        yield tempdir


@contextlib.contextmanager
def mock_github():
    requests = []

    class FakeGithub(http.server.BaseHTTPRequestHandler):
        state = {}
        counter = itertools.count(1)

        def read_json_request(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length"))))

        def write_json_response(self, code, data):
            self.send_response(code)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf8"))

        def do_PATCH(self):
            input = self.read_json_request()
            if state := self.state.get(self.path):
                code, resp = 200, {"url": self.path}
                self.state[self.path] = {**self.state[self.path], **input}
            else:
                code, resp = 422, {}
            requests.append((self.command, self.path, input))
            self.write_json_response(code, resp)

        def do_POST(self):
            input = self.read_json_request()
            if self.path == "/repos/migurski/temp/pulls":
                url = f"/repos/migurski/temp/pull/{next(self.counter)}"
                code, resp = 200, {"url": url}
                self.state[url] = input
            else:
                code, resp = 422, {}
            requests.append((self.command, self.path, input))
            self.write_json_response(code, resp)

    old_token, os.environ["GITHUB_TOKEN"] = os.getenv("GITHUB_TOKEN"), str(uuid.uuid4())

    for port in range(8000, 8099):
        try:
            server = http.server.HTTPServer(("", port), FakeGithub)
        except OSError:
            continue
        else:
            threading.Thread(target=server.serve_forever).start()
            try:
                yield f"http://localhost:{port}", os.environ["GITHUB_TOKEN"], requests
            finally:
                server.server_close()
                server.shutdown()
                break

    if old_token is not None:
        os.environ["GITHUB_TOKEN"] = old_token
    else:
        del os.environ["GITHUB_TOKEN"]


class TestRepo(unittest.TestCase):
    def test_one_branch(self):
        with fresh_repo():
            run_cmd("git commit -m 'empty' --allow-empty")
            graph = get_stack_graph()
        self.assertEqual(len(graph.nodes), 1)

    def test_one_branch_subdir1(self):
        """We're inside a subdirectory"""
        with fresh_repo():
            os.mkdir("subdir")
            os.chdir("subdir")
            run_cmd("git commit -m two --allow-empty")
            os.chdir("..")
            graph = get_stack_graph()
        self.assertEqual(len(graph.nodes), 1)

    def test_one_branch_subdir2(self):
        """We're inside a subdirectory"""
        with fresh_repo():
            os.mkdir("subdir")
            os.chdir("subdir")
            run_cmd("git commit -m two --allow-empty")
            os.chdir("..")
            graph = get_stack_graph()
        self.assertEqual(len(graph.nodes), 1)

    def test_two_branches(self):
        """One branch simply extends main"""
        with fresh_repo():
            run_cmd("""
                git commit -m one --allow-empty
                git checkout -b branch/1
                git commit -m two --allow-empty
                git checkout main
                """)
            graph = get_stack_graph()
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(list(graph.successors("main")), ["branch/1"])
        self.assertEqual(graph.nodes["branch/1"]["base"], graph.nodes["main"]["sha"])

    def test_two_branches_subdir1(self):
        """One branch simply extends main and we're inside a subdirectory"""
        with fresh_repo():
            run_cmd("""
                git commit -m one --allow-empty
                git checkout -b branch/1
                """)
            os.mkdir("subdir")
            os.chdir("subdir")
            run_cmd("""
                git commit -m two --allow-empty
                git checkout main
                """)
            os.chdir("..")
            graph = get_stack_graph()
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(list(graph.successors("main")), ["branch/1"])
        self.assertEqual(graph.nodes["branch/1"]["base"], graph.nodes["main"]["sha"])

    def test_two_branches_subdir2(self):
        """One branch simply extends main and we're inside a subdirectory"""
        with fresh_repo():
            os.mkdir("subdir")
            os.chdir("subdir")
            run_cmd("""
                git commit -m one --allow-empty
                git checkout -b branch/1
                git commit -m two --allow-empty
                git checkout main
                """)
            os.chdir("..")
            graph = get_stack_graph()
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(list(graph.successors("main")), ["branch/1"])
        self.assertEqual(graph.nodes["branch/1"]["base"], graph.nodes["main"]["sha"])

    def test_two_branches_no_ff(self):
        """One branch diverges slightly from main"""
        with fresh_repo():
            run_cmd("""
                git commit -m one --allow-empty
                git checkout -b branch/1
                git commit -m two --allow-empty
                git checkout main
                git commit -m three --allow-empty
                git checkout branch/1
                git commit -m four --allow-empty
                """)
            graph = get_stack_graph()
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(list(graph.successors("main")), ["branch/1"])
        self.assertNotEqual(graph.nodes["branch/1"]["base"], graph.nodes["main"]["sha"])

    def test_two_branches_ff_ok(self):
        """One branch simply extends main after a merge"""
        with fresh_repo():
            run_cmd("""
                git commit -m one --allow-empty
                git checkout -b branch/1
                git checkout main
                git commit -m two --allow-empty
                git checkout branch/1
                git merge main
                git commit -m three --allow-empty
                """)
            graph = get_stack_graph()
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(list(graph.successors("main")), ["branch/1"])
        self.assertEqual(graph.nodes["branch/1"]["base"], graph.nodes["main"]["sha"])

    def test_two_branches_restack(self):
        """One branch restacked after changes to main"""
        with fresh_repo():
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
            log, graph = get_git_log(), get_stack_graph()

        self.assertEqual(len(log), 4)
        self.assertEqual(
            log, ["four (HEAD -> branch/1)", "two ()", "three (main)", "one ()"]
        )

        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(list(graph.successors("main")), ["branch/1"])
        self.assertEqual(graph.nodes["branch/1"]["base"], graph.nodes["main"]["sha"])

    def test_three_branches(self):
        """Two branches diverge slightly from main"""
        with fresh_repo():
            run_cmd(f"""
                git commit -m one --allow-empty
                git checkout -b branch/1
                git commit -m two --allow-empty
                git checkout main
                git checkout -b branch/2
                git commit -m three --allow-empty
                """)
            log, graph = get_git_log(), get_stack_graph()

        self.assertEqual(len(log), 2)
        self.assertEqual(log, ["three (HEAD -> branch/2)", "one (main)"])

        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(list(graph.successors("main")), ["branch/1", "branch/2"])
        self.assertEqual(graph.nodes["branch/1"]["base"], graph.nodes["main"]["sha"])
        self.assertEqual(graph.nodes["branch/2"]["base"], graph.nodes["main"]["sha"])

    def test_three_branches_skipstep(self):
        """Two branches diverge slightly from main"""
        with fresh_repo():
            run_cmd(f"""
                git commit -m one --allow-empty
                git checkout -b branch/1
                git commit -m two --allow-empty
                git checkout -b branch/2 main
                git commit -m three --allow-empty
                """)
            log, graph = get_git_log(), get_stack_graph()

        self.assertEqual(len(log), 2)
        self.assertEqual(log, ["three (HEAD -> branch/2)", "one (main)"])

        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(list(graph.successors("main")), ["branch/1", "branch/2"])
        self.assertEqual(graph.nodes["branch/1"]["base"], graph.nodes["main"]["sha"])
        self.assertEqual(graph.nodes["branch/2"]["base"], graph.nodes["main"]["sha"])

    def test_three_branches_stacked(self):
        """Two branches stacked atop main in sequence"""
        with fresh_repo():
            run_cmd(f"""
                git commit -m one --allow-empty
                git checkout -b br/1
                git commit -m two --allow-empty
                git checkout -b br/2
                git commit -m three --allow-empty
                """)
            log, graph = get_git_log(), get_stack_graph()

        self.assertEqual(len(log), 3)
        self.assertEqual(log, ["three (HEAD -> br/2)", "two (br/1)", "one (main)"])

        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(list(graph.successors("main")), ["br/1"])
        self.assertEqual(list(graph.successors("br/1")), ["br/2"])
        self.assertEqual(graph.nodes["br/1"]["base"], graph.nodes["main"]["sha"])
        self.assertEqual(graph.nodes["br/2"]["base"], graph.nodes["br/1"]["sha"])

    def test_two_branches_move_up_onto(self):
        """One branch moved onto another"""
        with fresh_repo():
            run_cmd(f"""
                git commit -m one --allow-empty
                git checkout -b br/1
                git commit -m two --allow-empty
                git checkout -b br/2 main
                git commit -m three --allow-empty
                {PYTHON_STACK_PY} move-onto br/1
                """)
            log, graph = get_git_log(), get_stack_graph()

        self.assertEqual(len(log), 3)
        self.assertEqual(log, ["three (HEAD -> br/2)", "two (br/1)", "one (main)"])

        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(list(graph.successors("main")), ["br/1"])
        self.assertEqual(list(graph.successors("br/1")), ["br/2"])
        self.assertEqual(graph.nodes["br/1"]["base"], graph.nodes["main"]["sha"])
        self.assertEqual(graph.nodes["br/2"]["base"], graph.nodes["br/1"]["sha"])

    def test_two_branches_move_down_onto(self):
        """One branch moved onto another"""
        with fresh_repo():
            run_cmd(f"""
                git commit -m one --allow-empty
                git checkout -b br/1
                git commit -m two --allow-empty
                git checkout -b br/2
                git commit -m three --allow-empty
                {PYTHON_STACK_PY} move-onto main
                """)
            log, graph = get_git_log(), get_stack_graph()

        self.assertEqual(len(log), 2)
        self.assertEqual(log, ["three (HEAD -> br/2)", "one (main)"])

        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(list(graph.successors("main")), ["br/1", "br/2"])
        self.assertEqual(graph.nodes["br/1"]["base"], graph.nodes["main"]["sha"])
        self.assertEqual(graph.nodes["br/2"]["base"], graph.nodes["main"]["sha"])

    def test_three_branches_move_onto(self):
        """One stack moved onto another"""
        with fresh_repo():
            run_cmd(f"""
                git commit -m zero --allow-empty
                git checkout -b br/1
                git commit -m one --allow-empty
                git checkout -b br/2
                git commit -m two --allow-empty
                git checkout -b br/3
                git commit -m three --allow-empty
                git checkout br/2
                {PYTHON_STACK_PY} move-onto main
                git checkout br/3
                """)
            log, graph = get_git_log(), get_stack_graph()

        self.assertEqual(len(log), 3)
        self.assertEqual(log, ["three (HEAD -> br/3)", "two (br/2)", "zero (main)"])

        self.assertEqual(len(graph.nodes), 4)
        self.assertEqual(list(graph.successors("main")), ["br/1", "br/2"])
        self.assertEqual(graph.nodes["br/1"]["base"], graph.nodes["main"]["sha"])
        self.assertEqual(graph.nodes["br/2"]["base"], graph.nodes["main"]["sha"])
        self.assertEqual(graph.nodes["br/3"]["base"], graph.nodes["br/2"]["sha"])

    def test_one_branch_submit_1x(self):
        """One branch submitted to Github"""
        with fresh_repo():
            with mock_github() as (github_url, _, github_requests):
                run_cmd(f"""
                    git commit -m one --allow-empty
                    git checkout -b br/1
                    git commit -m two --allow-empty
                    git push origin br/1
                    {PYTHON_STACK_PY} submit --github {github_url} "PR 1"
                    """)
                log, graph = get_git_log(), get_stack_graph()

        self.assertEqual(len(log), 2)
        self.assertEqual(log, ["two (HEAD -> br/1)", "one (main)"])

        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(list(graph.successors("main")), ["br/1"])
        self.assertEqual(graph.nodes["br/1"]["base"], graph.nodes["main"]["sha"])
        self.assertEqual(
            graph.nodes["br/1"]["pull_url"], f"{github_url}/repos/migurski/temp/pull/1"
        )

        self.assertEqual(
            github_requests,
            [
                (
                    "POST",
                    "/repos/migurski/temp/pulls",
                    {"base": "main", "head": "br/1", "title": "PR 1", "draft": True},
                ),
            ],
        )

    def test_one_branch_submit_2x(self):
        """One branch submitted to Github twice"""
        with fresh_repo():
            with mock_github() as (github_url, _, github_requests):
                run_cmd(f"""
                    git commit -m one --allow-empty
                    git checkout -b br/1
                    git commit -m two --allow-empty
                    git checkout -b br/2
                    git commit -m three --allow-empty
                    git push origin br/2
                    {PYTHON_STACK_PY} submit --github {github_url} "PR 1"
                    {PYTHON_STACK_PY} move-onto main
                    git push origin br/2
                    {PYTHON_STACK_PY} submit --github {github_url}
                    """)
                log, graph = get_git_log(), get_stack_graph()

        self.assertEqual(len(log), 2)
        self.assertEqual(log, ["three (HEAD -> br/2)", "one (main)"])

        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(list(graph.successors("main")), ["br/1", "br/2"])
        self.assertEqual(graph.nodes["br/1"]["base"], graph.nodes["main"]["sha"])
        self.assertEqual(graph.nodes["br/2"]["base"], graph.nodes["main"]["sha"])
        self.assertEqual(
            graph.nodes["br/2"]["pull_url"], f"{github_url}/repos/migurski/temp/pull/1"
        )

        self.assertEqual(
            github_requests,
            [
                (
                    "POST",
                    "/repos/migurski/temp/pulls",
                    {"base": "br/1", "head": "br/2", "title": "PR 1", "draft": True},
                ),
                (
                    "PATCH",
                    "/repos/migurski/temp/pull/1",
                    {"base": "main", "head": "br/2"},
                ),
            ],
        )

    def test_two_branches_submit_1x(self):
        """Two branches submitted to Github once each"""
        with fresh_repo():
            with mock_github() as (github_url, _, github_requests):
                run_cmd(f"""
                    git commit -m one --allow-empty
                    git checkout -b br/1
                    git commit -m two --allow-empty
                    git push origin br/1
                    {PYTHON_STACK_PY} submit --github {github_url} "PR 1"
                    git checkout -b br/2
                    git commit -m three --allow-empty
                    git push origin br/2
                    {PYTHON_STACK_PY} submit --github {github_url} "PR 2"
                    """)
                log, graph = get_git_log(), get_stack_graph()

        self.assertEqual(len(log), 3)
        self.assertEqual(log, ["three (HEAD -> br/2)", "two (br/1)", "one (main)"])

        self.assertEqual(len(graph.nodes), 3)
        self.assertEqual(list(graph.successors("main")), ["br/1"])
        self.assertEqual(graph.nodes["br/1"]["base"], graph.nodes["main"]["sha"])
        self.assertEqual(
            graph.nodes["br/1"]["pull_url"], f"{github_url}/repos/migurski/temp/pull/1"
        )
        self.assertEqual(graph.nodes["br/2"]["base"], graph.nodes["br/1"]["sha"])
        self.assertEqual(
            graph.nodes["br/2"]["pull_url"], f"{github_url}/repos/migurski/temp/pull/2"
        )

        self.assertEqual(
            github_requests,
            [
                (
                    "POST",
                    "/repos/migurski/temp/pulls",
                    {"base": "main", "head": "br/1", "title": "PR 1", "draft": True},
                ),
                (
                    "POST",
                    "/repos/migurski/temp/pulls",
                    {"base": "br/1", "head": "br/2", "title": "PR 2", "draft": True},
                ),
            ],
        )
