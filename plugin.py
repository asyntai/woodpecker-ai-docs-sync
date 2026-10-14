#!/usr/bin/env python3
"""Woodpecker CI plugin: send documentation files to the Asyntai knowledge base.

Every file becomes one knowledge base entry. The title is derived from the
path, so the same file always maps to the same entry: on the next run the old
entry is removed first and the new text takes its place. That keeps a pipeline
that runs on every commit from filling the knowledge base with duplicates.

A file whose text already matches the stored entry is left alone. A pipeline
that runs on every commit therefore costs nothing against the daily upload
limit on the days when the documentation did not change.

Woodpecker passes every step setting as an environment variable with a
PLUGIN_ prefix (api_key becomes PLUGIN_API_KEY). A list setting arrives as a
JSON array or as comma separated text, so both are accepted. The plugin uses
only the Python standard library, so the image needs nothing installed.
"""

import fnmatch
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.0.0"
DEFAULT_BASE_URL = "https://asyntai.com"
DEFAULT_PATTERNS = "*.md,*.mdx,*.txt"
MIN_CONTENT_CHARS = 10          # the API rejects anything shorter
LIST_LIMIT = 100                # the API returns at most 100 entries

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


class ApiError(Exception):
    pass


def info(message):
    print(message, flush=True)


def success(message):
    print(GREEN + "✔ " + message + RESET, flush=True)


def warning(message):
    print(YELLOW + "✖ " + message + RESET, flush=True)


def fail(message):
    print(RED + "✖ " + message + RESET, file=sys.stderr, flush=True)
    return 1


def setting(name, default=""):
    """Read one step setting. Woodpecker sends `path` as PLUGIN_PATH."""
    value = os.environ.get("PLUGIN_" + name)
    if value is None:
        return default
    return value.strip()


def setting_flag(name):
    return setting(name).lower() in ("true", "1", "yes", "on")


def setting_list(name, default=""):
    """Read a setting that holds one pattern, a comma list or a YAML list."""
    text = setting(name, default) or default
    if text.startswith("["):
        try:
            items = json.loads(text)
        except ValueError:
            items = None
        if isinstance(items, list):
            return [str(i).strip() for i in items if str(i).strip()]
    return [p.strip() for p in text.split(",") if p.strip()]


def request_json(method, url, api_key, body=None, timeout=60, debug=False):
    data = None
    headers = {
        "Authorization": "Bearer " + api_key,
        "Accept": "application/json",
        "User-Agent": "asyntai-woodpecker-plugin/" + VERSION,
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if debug:
        info("  DEBUG %s %s" % (method, url))

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        message = raw
        try:
            message = json.loads(raw).get("error", raw)
        except (ValueError, AttributeError):
            pass
        raise ApiError("HTTP %s from %s: %s" % (exc.code, url, message))
    except urllib.error.URLError as exc:
        raise ApiError("Cannot reach %s: %s" % (url, exc.reason))

    try:
        return json.loads(raw)
    except ValueError:
        raise ApiError("The API answered with text that is not JSON: %s" % raw[:200])


def collect_files(root, patterns, exclude):
    """Return the files under root that match one of the patterns."""
    found = []
    if os.path.isfile(root):
        return [root]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            relative = os.path.relpath(full, root).replace(os.sep, "/")
            if any(fnmatch.fnmatch(relative, pat) or fnmatch.fnmatch(name, pat)
                   for pat in exclude):
                continue
            if any(fnmatch.fnmatch(name, pat) for pat in patterns):
                found.append(full)
    return sorted(found)


def make_title(path, root, prefix):
    if os.path.isfile(root):
        relative = os.path.basename(root)
    else:
        relative = os.path.relpath(path, root).replace(os.sep, "/")
    return (prefix + relative) if prefix else relative


def read_text(path):
    with open(path, "rb") as handle:
        raw = handle.read()
    return raw.decode("utf-8", "replace").strip()


def existing_entries(base_url, api_key, website_id, debug):
    query = {"limit": str(LIST_LIMIT)}
    if website_id:
        query["website_id"] = website_id
    url = base_url + "/api/v1/knowledge/?" + urllib.parse.urlencode(query)
    payload = request_json("GET", url, api_key, debug=debug)
    entries = payload.get("entries") or []
    if len(entries) >= LIST_LIMIT:
        warning("The knowledge base holds %d or more entries. The API lists %d "
                "at a time, so an older entry with the same title may not be "
                "replaced." % (LIST_LIMIT, LIST_LIMIT))
    by_title = {}
    for entry in entries:
        by_title.setdefault(entry.get("title"), []).append(entry)
    return by_title


def stored_content(base_url, api_key, entry_id, debug):
    """Return the text held by an entry, or None when it cannot be read."""
    payload = request_json(
        "GET", "%s/api/v1/knowledge/%s/" % (base_url, entry_id), api_key,
        debug=debug)
    if not payload.get("content_available"):
        return None
    content = payload.get("content")
    return content.strip() if isinstance(content, str) else None


def resolve_root(docs_path):
    """Read `path` relative to the workspace, which is where Woodpecker runs."""
    if os.path.isabs(docs_path):
        return docs_path
    workspace = os.environ.get("CI_WORKSPACE", "").strip()
    if workspace and os.path.isdir(workspace):
        return os.path.join(workspace, docs_path)
    return docs_path


def main():
    api_key = setting("API_KEY")
    docs_path = setting("PATH")
    patterns = setting_list("PATTERNS", DEFAULT_PATTERNS)
    exclude = setting_list("EXCLUDE")
    prefix = os.environ.get("PLUGIN_TITLE_PREFIX", "")
    website_id = setting("WEBSITE_ID")
    base_url = (setting("BASE_URL", DEFAULT_BASE_URL)
                or DEFAULT_BASE_URL).rstrip("/")
    prune = setting_flag("PRUNE")
    dry_run = setting_flag("DRY_RUN")
    debug = setting_flag("DEBUG")

    if not docs_path:
        return fail("The setting path is empty. Set it to the folder or file "
                    "to send, for example path: docs")
    if not api_key and not dry_run:
        return fail("The setting api_key is empty. Add your Asyntai API key "
                    "as a Woodpecker secret, then pass it with "
                    "api_key: {from_secret: <secret name>}.")

    root = resolve_root(docs_path)
    if not os.path.exists(root):
        return fail("The path %s does not exist in the repository." % docs_path)

    files = collect_files(root, patterns, exclude)
    if not files:
        return fail("No file under %s matches %s." % (docs_path, ", ".join(patterns)))

    info("▶  Asyntai AI docs sync %s" % VERSION)
    info("  Source:  %s" % docs_path)
    info("  Files:   %d" % len(files))
    info("  Target:  %s" % base_url)
    info("")

    if dry_run:
        for path in files:
            info("  would send %s as \"%s\"" %
                 (os.path.relpath(path, root) if not os.path.isfile(root) else docs_path,
                  make_title(path, root, prefix)))
        info("")
        success("Dry run. Nothing was sent.")
        return 0

    try:
        by_title = existing_entries(base_url, api_key, website_id, debug)
    except ApiError as exc:
        return fail(str(exc))

    added = updated = unchanged = skipped = deleted = 0
    handled_titles = set()

    for path in files:
        title = make_title(path, root, prefix)
        content = read_text(path)
        handled_titles.add(title)

        if len(content) < MIN_CONTENT_CHARS:
            info("  skip      %s (under %d characters)" % (title, MIN_CONTENT_CHARS))
            skipped += 1
            continue

        old_entries = by_title.get(title, [])

        try:
            # One stored entry holding the same text means there is nothing to
            # do. Sending it again would cost the account part of its daily
            # upload limit and would change nothing.
            if len(old_entries) == 1:
                stored = stored_content(base_url, api_key,
                                        old_entries[0]["id"], debug)
                if stored is not None and stored == content:
                    unchanged += 1
                    info("  unchanged %s" % title)
                    continue

            for old in old_entries:
                request_json("DELETE",
                             "%s/api/v1/knowledge/%s/" % (base_url, old["id"]),
                             api_key, debug=debug)

            body = {"title": title, "content": content}
            if website_id:
                body["website_id"] = website_id
            result = request_json("POST", base_url + "/api/v1/knowledge/text/",
                                  api_key, body, debug=debug)
        except ApiError as exc:
            info("  failed    %s" % title)
            return fail(str(exc))

        if old_entries:
            updated += 1
            word = "updated  "
        else:
            added += 1
            word = "added    "
        info("  %s %s (%d characters, %s chunks)" %
             (word, title, len(content), result.get("chunks_created", "?")))

    if prune and prefix:
        # Only entries carrying this prefix are touched, so one pipeline can
        # never remove what another pipeline owns.
        for title, entries in sorted(by_title.items(), key=lambda kv: kv[0] or ""):
            if not title or not title.startswith(prefix):
                continue
            if title in handled_titles:
                continue
            for entry in entries:
                try:
                    request_json(
                        "DELETE",
                        "%s/api/v1/knowledge/%s/" % (base_url, entry["id"]),
                        api_key, debug=debug)
                except ApiError as exc:
                    return fail(str(exc))
                deleted += 1
                info("  deleted   %s (the file is gone)" % title)
    elif prune:
        warning("prune was ignored. Set title_prefix as well, so the plugin "
                "only removes entries it owns.")

    info("")
    success("Done. %d added, %d updated, %d unchanged, %d deleted, %d skipped."
            % (added, updated, unchanged, deleted, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
