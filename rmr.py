#!/usr/bin/env python
import json
import sys
import pty
import os
import select
import subprocess
from getopt import getopt


class Process:
    def __init__(self, argv):
        self.master, self.slave = pty.openpty()
        self.proc = subprocess.Popen(
            argv,
            stdin=self.slave,
            stdout=self.slave,
            stderr=subprocess.STDOUT,
            env={"NO_COLOR": "1"},
        )
        self._stdin_queue = []

    def __iter__(self):
        while self.proc.poll() is None:
            r, w, x = select.select([self.master], [self.master], [self.master], 0)
            if x:
                return
            if r:
                data = os.read(self.master, 1_000_000).decode()
                lines = data.split("\n")
                # Theoretically we could end put yielding partial lines.
                yield from (line.rstrip("\r") for line in lines)
            if w and self._stdin_queue:
                line = self._stdin_queue.pop(0)
                os.write(self.master, f"{line}\n".encode())

    def writeline(self, line):
        self._stdin_queue.append(line)


class IgnoredSuggestionStore:
    def __init__(self, *, tool_id=None, purge_unused=False):
        if tool_id:
            self.path = ".ignores.{tool_id}.json"
        else:
            self.path = ".ignores.json"
        try:
            with open(self.path) as f:
                self._ignores = json.loads(f.read())
        except FileNotFoundError:
            self._ignores = {}

        self.purge_unused = purge_unused
        self._used = []

    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_inst, tb):
        if self.purge_unused:
            purged = {}
            for path, suggestions in self._ignores.items():
                for suggestion in suggestions:
                    if (path, suggestion) in self._used:
                        purged.setdefault(path, []).append(suggestion)
            self._ignores = purged
        with open(self.path, "w") as f:
            f.write(json.dumps(self._ignores))

    def add(self, path, suggestion):
        if path not in self._ignores:
            self._ignores[path] = []
        self._ignores[path].append(suggestion)

    def __contains___(self, change):
        if self.purge_unused:
            self._used.append(change)
        path, suggestion = change
        return suggestion in self._ignores.get(path, [])


def git_is_clean():
    p = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True)
    return not p.stdout.decode().strip()


def extract_changes(output, default_path=None):
    a_path = b_path = None
    change = []
    for line in output.splitlines():
        if line in ("n", "y", "s"):
            continue
        if line.startswith(("diff ", "index ")):
            if change:
                yield a_path, change
            a_path = b_path = None
            change = []
            continue
        if line.startswith("@@ "):
            continue
        if line.startswith("--- a/"):
            *_, a_path = line.partition(" a/")
            continue
        if line.startswith("+++ b/"):
            *_, b_path = line.partition(" b/")
            continue
        if not a_path and default_path:
            a_path = default_path
        if not line.startswith(("+", "-")):
            # context line
            if change:
                yield a_path, change
            change = []
            continue
        change.append(line)
    if change:
        yield a_path, change


def revert_ignored_changes(permanently_ignored):
    buffer = ""
    path = None
    for line in (proc := Process(["git", "checkout", "-p"])):
        if "Discard this hunk from worktree" in line:
            changes = list(extract_changes(buffer, default_path=path))
            if len(changes) > 1:
                proc.writeline("s")
            elif not changes:
                breakpoint()
            else:
                [[path, change]] = changes
                #if ignores.is_ignored(path, change):
                if (path, change) in permanently_ignored:
                    proc.writeline("y")
                else:
                    proc.writeline("n")
            buffer = ""
        else:
            buffer += f"{line}\n"


def remember_discarded_changes(permanently_ignored):
    p = subprocess.run(["git", "diff", "--no-ext-diff"], capture_output=True)
    for path, rejected_change in extract_changes(p.stdout.decode()):
        permanently_ignored.add(path, rejected_change)


def cli(argv):
    opts, args = getopt(argv, "h", ["tool-id=", "autopurge", "help"])
    opts = dict(opts)
    autopurge = "--autopurge" in opts
    tool_id = opts.get("--tool-id", None)

    if "-h" in opts or "--help" in opts:
        print("""
Usage: stint [--tool-id=<TOOL_ID>] [--autopurge] TOOL...

Options:
    --tool-id       Optionally specify the tool used (e.g. name of your
                    formatter), in case you use several tools.
    --autopurge     Whether to remove unused ignore rules.
""")
        sys.exit(1)

    if not git_is_clean():
        print("Aborting, Git repo is not clean.", file=sys.stderr)
        sys.exit(1)

    with IgnoredSuggestionStore(
        tool_id=tool_id,
        purge_unused=autopurge,
    ) as permanently_ignored:
        # Run the actual linter/formatter/whatever:
        subprocess.run(argv)

        revert_ignored_changes(permanently_ignored)

        # Ask user which new changes are "good":
        subprocess.run(["git", "add", "-p"])

        remember_discarded_changes(permanently_ignored)

    # Remove unstaged (rejected) changes
    subprocess.run(["git", "restore", "."])
    # Un-stage staged (approved) changes
    subprocess.run(["git", "reset"])


cli(sys.argv[1:])
