#!/usr/bin/env python3
"""Test the plugin the way Woodpecker CI runs it.

Woodpecker starts the plugin image with the repository mounted at
CI_WORKSPACE, the working directory set to it, and every step setting as an
environment variable with a PLUGIN_ prefix. This runner does the same against
a stand-in API, in one of two modes:

    python tests/run_tests.py
        Runs plugin.py with this Python. Fast, no container needed.

    python tests/run_tests.py --image asyntai/woodpecker-ai-docs-sync:1.0.0
        Runs the built image with docker or podman, the repository mounted the
        way Woodpecker mounts it. This exercises the artifact that ships.
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLUGIN = os.path.join(ROOT, "plugin.py")
API_KEY = "test-key-2f8a1c"
WORKSPACE = "/woodpecker/src/example.org/team/project"

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print("  PASS  %s" % name)
    else:
        FAILED.append((name, detail))
        print("  FAIL  %s" % name)
        if detail:
            print("        %s" % detail)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_repo(base):
    repo = os.path.join(base, "repo")
    docs = os.path.join(repo, "docs")
    os.makedirs(os.path.join(docs, "guides"))
    os.makedirs(os.path.join(docs, "drafts"))
    os.makedirs(os.path.join(docs, ".hidden"))
    files = {
        "index.md": "# Welcome\n\nAsyntai answers questions on your website.",
        "pricing.md": "# Pricing\n\nThe Starter plan covers one website.",
        "guides/setup.md": "# Setup\n\nPaste the script tag before the body ends.",
        "drafts/wip.md": "# Draft\n\nThis page is not ready for readers yet.",
        "tiny.md": "short",
        "logo.png": "not text at all, and not a pattern match either",
        "notes.txt": "Plain text notes that are long enough to send.",
        ".hidden/secret.md": "# Hidden\n\nA hidden folder must never be read.",
    }
    for name, text in files.items():
        with open(os.path.join(docs, name), "w", encoding="utf-8") as handle:
            handle.write(text)
    with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as handle:
        handle.write("# Project\n\nThe readme of the repository itself.")
    return repo, docs


class Runner:
    """Starts the plugin like Woodpecker does, locally or as a container."""

    def __init__(self, repo, base_url, image=None, engine="docker"):
        self.repo = repo
        self.base_url = base_url
        self.image = image
        self.engine = engine

    def run(self, variables):
        variables = dict(variables)
        variables.setdefault("PLUGIN_BASE_URL", self.base_url)
        if self.image:
            cmd = [self.engine, "run", "--rm", "--network", "host",
                   "-v", "%s:%s" % (self.repo, WORKSPACE),
                   "-w", WORKSPACE,
                   "-e", "CI_WORKSPACE=%s" % WORKSPACE]
            for key, value in variables.items():
                cmd += ["-e", "%s=%s" % (key, value)]
            cmd.append(self.image)
            return subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("PLUGIN_")}
        env["CI_WORKSPACE"] = self.repo
        env["PYTHONIOENCODING"] = "utf-8"
        env.update(variables)
        return subprocess.run([sys.executable, PLUGIN], cwd=self.repo, env=env,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")


def server_dump(dump_path):
    flag = dump_path + ".flush"
    open(flag, "w").close()
    for _ in range(100):
        if not os.path.exists(flag):
            break
        time.sleep(0.05)
    with open(dump_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def titles(dump):
    return sorted(entry["title"] for entry in dump["entries"])


def static_checks():
    print("\nThe files a complete plugin must have")
    for name in ("docs.md", "README.md", "Dockerfile", "LICENSE",
                 "CHANGELOG.md", "plugin.py"):
        check("%s exists" % name, os.path.exists(os.path.join(ROOT, name)))

    with open(os.path.join(ROOT, "docs.md"), encoding="utf-8") as handle:
        docs = handle.read()
    match = re.match(r"^---\n(.*?)\n---\n", docs, re.S)
    check("docs.md starts with a metadata header", match is not None)
    header = match.group(1) if match else ""
    fields = dict(re.findall(r"^(\w+):\s*(.+)$", header, re.M))
    for field in ("name", "description", "author", "tags", "icon",
                  "containerImage", "containerImageUrl", "url"):
        check("the header has %s" % field, field in fields)
    check("the name carries the searched words",
          "AI" in fields.get("name", "") and "Chatbot" in fields.get("name", ""),
          fields.get("name"))
    check("tags is a YAML list",
          re.match(r"^\[.+\]$", fields.get("tags", "")) is not None)
    check("the listing link points to the Asyntai docs page",
          fields.get("url", "").startswith("https://asyntai.com/documentation/"))
    image = fields.get("containerImage", "")
    images = set(re.findall(r"image:\s*(\S+)", docs))
    check("every example uses the image named in the header",
          images == {image + ":1"}, str(images))
    check("the examples pass the key from a secret, never in clear",
          "from_secret: asyntai_api_key" in docs)

    with open(os.path.join(ROOT, "plugin.py"), encoding="utf-8") as handle:
        source = handle.read()
    settings = set(s.lower() for s in
                   re.findall(r'setting(?:_flag|_list)?\("([A-Z_]+)"', source))
    settings.add("title_prefix")
    table = set(re.findall(r"^\| `(\w+)`", docs, re.M))
    check("the settings table lists every setting the plugin reads",
          settings == table,
          "plugin %s, table %s" % (sorted(settings), sorted(table)))

    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
        readme = handle.read()
    check("the README carries the same text as docs.md",
          docs.split("\n---\n", 1)[1].strip() in readme)

    version = re.search(r'^VERSION = "([^"]+)"', source, re.M).group(1)
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as handle:
        changelog = handle.read()
    check("the changelog lists the plugin version",
          ("## %s" % version) in changelog)

    with open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8") as handle:
        dockerfile = handle.read()
    base = re.search(r"^FROM\s+(\S+)", dockerfile, re.M).group(1)
    check("the base image is pinned, never latest",
          "latest" not in base
          and re.search(r":\d+\.\d+\.\d+", base) is not None, base)
    check("the Dockerfile sets an ENTRYPOINT",
          re.search(r"^ENTRYPOINT", dockerfile, re.M) is not None)
    check("the Dockerfile installs nothing from the network",
          not re.search(r"\b(pip install|apk add|curl|wget)\b", dockerfile))


def behaviour_checks(runner, docs, dump_path):
    base = {"PLUGIN_API_KEY": API_KEY, "PLUGIN_PATH": "docs"}

    print("\nFirst run, an empty knowledge base")
    proc = runner.run(base)
    check("the plugin succeeds", proc.returncode == 0,
          proc.stdout[-600:] + proc.stderr[-600:])
    dump = server_dump(dump_path)
    expected = ["drafts/wip.md", "guides/setup.md", "index.md", "notes.txt",
                "pricing.md"]
    check("every matching file became an entry", titles(dump) == expected,
          "got %s" % titles(dump))
    check("the file under ten characters was skipped",
          "tiny.md" not in titles(dump))
    check("the file that matches no pattern was skipped",
          "logo.png" not in titles(dump))
    check("the hidden folder was not read",
          not any("secret" in t for t in titles(dump)))
    check("the repository readme outside path was not sent",
          "README.md" not in titles(dump))
    check("the text of a file arrived whole",
          any(e["content"].startswith("# Pricing") for e in dump["entries"]))
    check("no DELETE was sent when nothing was there",
          not any(c["method"] == "DELETE" for c in dump["calls"]))
    check("the summary counts them as added",
          "%d added" % len(expected) in proc.stdout, proc.stdout[-300:])
    check("the summary line is marked as a success",
          "✔ Done." in proc.stdout, proc.stdout[-300:])

    print("\nSecond run, nothing changed")
    first_ids = {e["title"]: e["id"] for e in dump["entries"]}
    posts_before = sum(1 for c in dump["calls"] if c["method"] == "POST")
    proc = runner.run(base)
    check("the plugin succeeds again", proc.returncode == 0,
          proc.stdout[-600:] + proc.stderr[-600:])
    dump = server_dump(dump_path)
    check("the entry count did not grow, so nothing was duplicated",
          len(dump["entries"]) == len(expected))
    check("every entry was left exactly as it was",
          first_ids == {e["title"]: e["id"] for e in dump["entries"]})
    check("no text was sent again, so the daily limit is untouched",
          sum(1 for c in dump["calls"] if c["method"] == "POST") == posts_before)
    check("the summary counts them as unchanged",
          "%d unchanged" % len(expected) in proc.stdout, proc.stdout[-300:])

    print("\nChanged text")
    with open(os.path.join(docs, "pricing.md"), "w", encoding="utf-8") as h:
        h.write("# Pricing\n\nThe Standard plan covers three websites.")
    proc = runner.run(base)
    dump = server_dump(dump_path)
    pricing = [e for e in dump["entries"] if e["title"] == "pricing.md"]
    check("the entry now holds the new text",
          len(pricing) == 1 and "Standard plan" in pricing[0]["content"],
          str(pricing))
    check("only the changed file was sent",
          "0 added, 1 updated, %d unchanged" % (len(expected) - 1)
          in proc.stdout, proc.stdout[-300:])

    print("\nexclude")
    proc = runner.run(dict(base, PLUGIN_EXCLUDE="drafts/*"))
    check("the plugin succeeds", proc.returncode == 0, proc.stderr[-400:])
    check("the excluded folder is not in the output",
          "drafts/wip.md" not in proc.stdout, proc.stdout[-400:])

    print("\npatterns")
    proc = runner.run(dict(base, PLUGIN_PATTERNS="*.txt"))
    check("the plugin succeeds", proc.returncode == 0, proc.stderr[-400:])
    check("only the matching file was handled",
          "Files:   1" in proc.stdout, proc.stdout[:400])

    print("\ntitle_prefix")
    runner.run(dict(base, PLUGIN_TITLE_PREFIX="Docs / "))
    dump = server_dump(dump_path)
    check("the prefix is on the titles", "Docs / index.md" in titles(dump),
          str(titles(dump)))
    check("the run without a prefix left its own entries in place",
          "index.md" in titles(dump))

    print("\nprune")
    prune = dict(base, PLUGIN_TITLE_PREFIX="Docs / ", PLUGIN_PRUNE="true")
    runner.run(prune)
    os.remove(os.path.join(docs, "notes.txt"))
    proc = runner.run(prune)
    check("the plugin succeeds", proc.returncode == 0, proc.stderr[-400:])
    dump = server_dump(dump_path)
    check("the entry of the deleted file is gone",
          "Docs / notes.txt" not in titles(dump), str(titles(dump)))
    check("the entries without the prefix were not touched",
          "notes.txt" in titles(dump), str(titles(dump)))
    check("the summary counts the deletion", "1 deleted" in proc.stdout,
          proc.stdout[-300:])

    print("\nprune without title_prefix")
    proc = runner.run(dict(base, PLUGIN_PRUNE="true"))
    check("the plugin succeeds", proc.returncode == 0, proc.stderr[-400:])
    check("the output warns that PRUNE was ignored",
          "prune was ignored" in proc.stdout, proc.stdout[-300:])
    check("nothing was deleted", "0 deleted" in proc.stdout, proc.stdout[-300:])

    print("\ndry_run")
    calls_before = len(server_dump(dump_path)["calls"])
    proc = runner.run(dict(base, PLUGIN_DRY_RUN="true"))
    check("the plugin succeeds", proc.returncode == 0, proc.stderr[-400:])
    check("the output says nothing was sent", "Nothing was sent" in proc.stdout,
          proc.stdout[-400:])
    check("the API was not called at all",
          len(server_dump(dump_path)["calls"]) == calls_before)
    proc = runner.run({"PLUGIN_PATH": "docs", "PLUGIN_DRY_RUN": "true"})
    check("a dry run needs no key", proc.returncode == 0, proc.stderr[-400:])

    print("\ndebug")
    proc = runner.run(dict(base, PLUGIN_DEBUG="true", PLUGIN_DRY_RUN="false"))
    check("the plugin succeeds", proc.returncode == 0, proc.stderr[-400:])
    check("every API call is printed", "DEBUG GET" in proc.stdout,
          proc.stdout[:400])
    check("the API key itself is never printed", API_KEY not in proc.stdout +
          proc.stderr)

    print("\nA single file as path")
    proc = runner.run(dict(base, PLUGIN_PATH="README.md"))
    check("the plugin succeeds", proc.returncode == 0, proc.stderr[-400:])
    check("the title is the file name on its own",
          "README.md" in titles(server_dump(dump_path)))

    print("\nList settings, the way Woodpecker sends a YAML list")
    proc = runner.run(dict(base, PLUGIN_PATTERNS='["setup.md"]'))
    check("a JSON list of patterns is read", proc.returncode == 0 and
          "Files:   1" in proc.stdout, proc.stdout[:400] + proc.stderr[-300:])
    proc = runner.run(dict(base, PLUGIN_EXCLUDE='["drafts/*", "guides/*"]'))
    check("a JSON list of excludes is read", proc.returncode == 0 and
          "drafts/wip.md" not in proc.stdout and
          "guides/setup.md" not in proc.stdout, proc.stdout[-400:])
    proc = runner.run(dict(base, PLUGIN_PATTERNS="index.md, pricing.md",
                           PLUGIN_DRY_RUN="true"))
    check("comma separated text with spaces is read",
          proc.returncode == 0 and "Files:   2" in proc.stdout, proc.stdout[-400:])

    print("\nWoodpecker log groups")
    proc = runner.run(dict(base, PLUGIN_DRY_RUN="true"))
    first = proc.stdout.splitlines()[0] if proc.stdout else ""
    check("the first line starts a log group (marker and two spaces)",
          first.startswith("\u25b6  Asyntai"), repr(first))

    print("\nFailures are reported")
    proc = runner.run({"PLUGIN_PATH": "docs"})
    check("an empty key stops the plugin", proc.returncode != 0)
    check("the message names the setting to set",
          "api_key" in proc.stderr, proc.stderr[-400:])

    proc = runner.run({"PLUGIN_API_KEY": API_KEY})
    check("an empty path stops the plugin", proc.returncode != 0)
    check("the message names the path setting", "setting path" in proc.stderr,
          proc.stderr[-400:])

    proc = runner.run(dict(base, PLUGIN_API_KEY="wrong-key"))
    check("a wrong key stops the plugin", proc.returncode != 0)
    check("the message repeats what the API said",
          "Invalid API key" in proc.stderr + proc.stdout, proc.stderr[-400:])

    proc = runner.run(dict(base, PLUGIN_PATH="no-such-folder"))
    check("a missing folder stops the plugin", proc.returncode != 0)
    check("the message names the missing folder",
          "no-such-folder" in proc.stderr, proc.stderr[-400:])

    proc = runner.run(dict(base, PLUGIN_PATTERNS="*.rst"))
    check("a pattern that matches nothing stops the plugin", proc.returncode != 0)

    proc = runner.run(dict(base, PLUGIN_BASE_URL="http://127.0.0.1:1"))
    check("an unreachable server stops the plugin", proc.returncode != 0)
    check("the message says the server could not be reached",
          "Cannot reach" in proc.stderr, proc.stderr[-400:])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", help="Run this built image instead of plugin.py.")
    parser.add_argument("--engine", default="docker",
                        help="docker or podman, used with --image.")
    args = parser.parse_args()

    if not args.image:
        static_checks()

    workdir = tempfile.mkdtemp(prefix="asyntai-woodpecker-test-")
    repo, docs = make_repo(workdir)
    port = free_port()
    dump_path = os.path.join(workdir, "server.json")
    server = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "fake_api.py"), str(port), dump_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        print("The stand-in API did not start.", file=sys.stderr)
        server.kill()
        return 2

    runner = Runner(repo, "http://127.0.0.1:%d" % port, args.image, args.engine)
    print("\nMode: %s" % ("image %s via %s" % (args.image, args.engine)
                          if args.image else "plugin.py with this Python"))
    try:
        behaviour_checks(runner, docs, dump_path)
    finally:
        open(dump_path + ".stop", "w").close()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(workdir, ignore_errors=True)

    print("\n%d passed, %d failed." % (len(PASSED), len(FAILED)))
    for name, _ in FAILED:
        print("  FAILED: %s" % name)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
