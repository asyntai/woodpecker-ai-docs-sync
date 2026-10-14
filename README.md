# Woodpecker CI plugin: Asyntai AI Chatbot Docs Sync

[Asyntai](https://asyntai.com) is an AI chatbot that answers your website visitors from your own content. This plugin sends the documentation in your repository to the chatbot's knowledge base on every pipeline run, so visitors get the answer that matches the version you just shipped.

- **One entry per file.** The entry title is the file path, with your `title_prefix` in front. A file keeps the same entry on every run, so a pipeline that runs on every commit never makes duplicates.
- **Changed files are replaced.** When the text of a file changes, the plugin replaces its entry with the new text.
- **Unchanged files are left alone.** A file whose text matches the stored entry is skipped, so a run with no documentation change sends nothing.
- **Deleted files can be removed.** With `prune: true` and a `title_prefix`, the plugin removes the entries of files that are gone. It touches only entries that carry your prefix.

Hidden folders such as `.git` are not read. Files under 10 characters are skipped. The log shows one line per file (`added`, `updated`, `unchanged`, `deleted` or `skip`) and a summary.

## Settings

| Settings Name   | Default             | Description |
| --------------- | ------------------- | ----------- |
| `api_key`       | _none_              | Your Asyntai API key. Required. Pass it from a secret with `from_secret`. |
| `path`          | _none_              | The folder or file to send, relative to the repository root, for example `docs` or `README.md`. Required. |
| `patterns`      | `*.md,*.mdx,*.txt`  | File patterns to include, as a list or comma separated text. |
| `exclude`       | _none_              | Patterns to skip, as a list or comma separated text, for example `[CHANGELOG.md, drafts/*]`. |
| `title_prefix`  | _none_              | Text put in front of every entry title, for example `"Docs / "`. |
| `website_id`    | your first website  | The Asyntai website to write to. |
| `prune`         | `false`             | Remove entries that carry your `title_prefix` and whose file is gone from the repository. |
| `dry_run`       | `false`             | List the files and send nothing. Useful on pull requests. |
| `debug`         | `false`             | Print every API call the plugin makes. The key is never printed. |
| `base_url`      | `https://asyntai.com` | The Asyntai address. Change it only for a private install. |

## Example

Send the `docs` folder on every push to `main`:

```yaml
steps:
  - name: sync-docs
    image: asyntai/woodpecker-ai-docs-sync:1
    settings:
      api_key:
        from_secret: asyntai_api_key
      path: docs
    when:
      - event: push
        branch: main
```

Send two folders with a prefix, and remove the entries of deleted files:

```yaml
steps:
  - name: sync-docs
    image: asyntai/woodpecker-ai-docs-sync:1
    settings:
      api_key:
        from_secret: asyntai_api_key
      path: docs
      patterns: ["*.md", "*.mdx"]
      exclude: [CHANGELOG.md, drafts/*]
      title_prefix: "Docs / "
      prune: true
    when:
      - event: push
        branch: main
```

Check a pull request without sending anything:

```yaml
steps:
  - name: check-docs
    image: asyntai/woodpecker-ai-docs-sync:1
    settings:
      path: docs
      dry_run: true
    when:
      - event: pull_request
```

## Setup

1. Create an [Asyntai account](https://asyntai.com/register/) on a paid plan. The knowledge base API is part of every paid plan.
2. Sign in to Asyntai, open **Settings**, then **API**, and copy your API key.
3. In Woodpecker open the repository **Settings**, then **Secrets**. Add a secret named `asyntai_api_key` with the key as the value.
4. Add the step to your `.woodpecker.yaml`.

Full guide: [asyntai.com/documentation/integrations/woodpecker-ci/](https://asyntai.com/documentation/integrations/woodpecker-ci/)
