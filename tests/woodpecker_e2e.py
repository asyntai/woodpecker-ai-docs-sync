#!/usr/bin/env python3
"""Run the plugin image inside a real Woodpecker pipeline.

`woodpecker-cli exec` runs a pipeline file with the same engine the Woodpecker
agent uses: it clones nothing, mounts the folder as the workspace, turns the
step settings into PLUGIN_ variables and resolves `from_secret`. The stand-in
API runs in its own container on a Docker network that the pipeline joins.

Needs Linux with Docker and woodpecker-cli. Usage:

    python3 tests/woodpecker_e2e.py --cli /opt/wp/woodpecker-cli \
        --image asyntai/woodpecker-ai-docs-sync:1.0.0
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
API_KEY = "test-key-2f8a1c"
NETWORK = "asyntai-wp-e2e"
API_NAME = "asyntai-wp-e2e-api"
API_IMAGE = "python:3.13.16-alpine3.24"

PIPELINE = """\
when:
  - event: manual

steps:
  - name: first-sync
    image: {image}
    settings:
      api_key:
        from_secret: asyntai_api_key
      path: docs
      patterns: ["*.md", "*.txt"]
      exclude: [drafts/*]
      title_prefix: "Docs / "
      prune: true
      base_url: http://{api}:8080

  - name: second-sync
    image: {image}
    settings:
      api_key:
        from_secret: asyntai_api_key
      path: docs
      patterns: ["*.md", "*.txt"]
      exclude: [drafts/*]
      title_prefix: "Docs / "
      prune: true
      base_url: http://{api}:8080

  - name: remove-a-page
    image: {api_image}
    commands:
      - rm docs/notes.txt

  - name: prune-sync
    image: {image}
    settings:
      api_key:
        from_secret: asyntai_api_key
      path: docs
      patterns: "*.md,*.txt"
      exclude: drafts/*
      title_prefix: "Docs / "
      prune: true
      base_url: http://{api}:8080

  - name: dry-run
    image: {image}
    settings:
      path: docs
      dry_run: true
      base_url: http://{api}:8080
"""

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print("  PASS  %s" % name)
    else:
        FAILED.append(name)
        print("  FAIL  %s" % name)
        if detail:
            print("        %s" % detail)


def sh(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def make_repo(base):
    docs = os.path.join(base, "docs")
    os.makedirs(os.path.join(docs, "guides"))
    os.makedirs(os.path.join(docs, "drafts"))
    files = {
        "index.md": "# Welcome\n\nAsyntai answers questions on your website.",
        "guides/setup.md": "# Setup\n\nPaste the script tag before the body ends.",
        "drafts/wip.md": "# Draft\n\nThis page is not ready for readers yet.",
        "notes.txt": "Plain text notes that are long enough to send.",
    }
    for name, text in files.items():
        with open(os.path.join(docs, name), "w", encoding="utf-8") as handle:
            handle.write(text)
    for cmd in (["git", "init", "-q", "-b", "main"],
                ["git", "add", "."],
                ["git", "-c", "user.name=test", "-c", "user.email=t@example.org",
                 "commit", "-q", "-m", "docs"]):
        sh(cmd, cwd=base, check=True)


def step_log(output, step):
    """Return the lines woodpecker-cli printed for one step."""
    prefix = "[%s:" % step
    return "\n".join(line for line in output.splitlines() if line.startswith(prefix))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", default="woodpecker-cli")
    parser.add_argument("--image", default="asyntai/woodpecker-ai-docs-sync:1.0.0")
    args = parser.parse_args()

    work = tempfile.mkdtemp(prefix="asyntai-wp-e2e-")
    repo = os.path.join(work, "repo")
    state = os.path.join(work, "state")
    os.makedirs(repo)
    os.makedirs(state)
    shutil.copy(os.path.join(HERE, "fake_api.py"), state)
    make_repo(repo)
    with open(os.path.join(repo, ".woodpecker.yaml"), "w") as handle:
        handle.write(PIPELINE.format(image=args.image, api=API_NAME,
                                     api_image=API_IMAGE))

    sh(["docker", "rm", "-f", API_NAME])
    sh(["docker", "network", "rm", NETWORK])
    sh(["docker", "network", "create", NETWORK], check=True)
    sh(["docker", "run", "-d", "--name", API_NAME, "--network", NETWORK,
        "-v", "%s:/state" % state, API_IMAGE,
        "python3", "/state/fake_api.py", "8080", "/state/server.json", "0.0.0.0"],
       check=True)
    time.sleep(1)

    try:
        proc = sh([args.cli, "exec", "--network", NETWORK,
                   "--secrets", "asyntai_api_key=%s" % API_KEY,
                   "--repo-path", repo,
                   os.path.join(repo, ".woodpecker.yaml")], cwd=repo)
        output = proc.stdout + proc.stderr
        print(output[-4000:])
        print("\nA real Woodpecker pipeline")
        check("the pipeline succeeds", proc.returncode == 0)

        first = step_log(output, "first-sync")
        check("the first step starts a log group", "▶  Asyntai" in first, first[:300])
        check("the first step added three pages",
              "3 added, 0 updated, 0 unchanged" in first, first[-300:])
        check("the excluded draft was not sent", "wip.md" not in first)
        check("the secret value never reached the log", API_KEY not in output)

        second = step_log(output, "second-sync")
        check("the second step changed nothing",
              "0 added, 0 updated, 3 unchanged, 0 deleted" in second, second[-300:])

        pruned = step_log(output, "prune-sync")
        check("comma text settings work in a real pipeline",
              "Files:   2" in pruned, pruned[:300])
        check("the removed page was pruned",
              "deleted   Docs / notes.txt" in pruned and "1 deleted" in pruned,
              pruned[-300:])

        dry = step_log(output, "dry-run")
        check("the dry run needs no secret and sends nothing",
              "Nothing was sent" in dry, dry[-300:])

        open(os.path.join(state, "server.json.stop"), "w").close()
        for _ in range(100):
            if os.path.exists(os.path.join(state, "server.json")):
                break
            time.sleep(0.1)
        with open(os.path.join(state, "server.json")) as handle:
            dump = json.load(handle)
        titles = sorted(e["title"] for e in dump["entries"])
        check("the knowledge base holds exactly the current pages",
              titles == ["Docs / guides/setup.md", "Docs / index.md"], str(titles))
    finally:
        sh(["docker", "rm", "-f", API_NAME])
        sh(["docker", "network", "rm", NETWORK])
        shutil.rmtree(work, ignore_errors=True)

    print("\n%d passed, %d failed." % (len(PASSED), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
