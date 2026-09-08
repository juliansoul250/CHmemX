# CHmemX

**English** | [简体中文](README.zh-CN.md)

[![Tests](https://github.com/juliansoul250/CHmemX/actions/workflows/test.yml/badge.svg)](https://github.com/juliansoul250/CHmemX/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](pyproject.toml)

Local-first shared memory for AI agents, with reviewed writes and Git history.

CHmemX lets agents in different tools reuse accepted preferences, decisions and lessons.
Each source submits its own material; a curator compares it with existing memory, and the
Owner decides what becomes permanent. The default Team policy keeps uploads out of recall
until an exact batch is approved.

This README documents the current **v0.5.4** package and its MCP/CLI interface.

## What it provides

- Three stdio MCP tools: `start`, `recall` and `upload`. The base installation needs Python and Git, not an API key, open port, database service or model download.
- Separate global preferences and registered project memory, organized by canonical keys and linked topic nodes.
- Full current/incoming comparisons, source evidence and exact-batch approval before Team writes.
- Atomic Git commits, preserved approval history and explicit supersession instead of silent overwrites.
- Local lexical retrieval, optional ONNX semantic retrieval, source-validity checks and bounded graph associations.

Keep the private memory store outside the tool repository. Publishing CHmemX code does not
publish your memory. CHmemX provides workflow governance, **not an OS security boundary**
against another process with the same user's file permissions.

## Install and connect

Requirements: Python 3.10+ and Git. Choose an existing Git project and a new, separate
directory for memory. Project facts must cite committed source files.

On macOS or Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install 'git+https://github.com/juliansoul250/CHmemX.git@v0.5.4'

chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  --agent-id source-one init --project-id project-demo

chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  --agent-id source-one status
```

On Windows, create the environment with `py -3 -m venv .venv`, then use
`.venv\Scripts\python.exe` and `.venv\Scripts\chmemx.exe` in place of the commands above.
Initialization creates the memory Git repository; it does not modify the source project.
Do not initialize over an existing store.

For an MCP client using the conventional JSON configuration:

```json
{
  "mcpServers": {
    "chmemx": {
      "command": "/absolute/.venv/bin/chmemx",
      "args": [
        "--store", "/absolute/private-memory",
        "--cwd", "/absolute/git-project",
        "--agent-id", "source-one",
        "serve"
      ]
    }
  }
}
```

Use an absolute executable path, the intended project root and a distinct source ID for each
client. Project and source context are fixed by these launch arguments. Each client owns its
configuration and starts its own stdio process. See [MCP setup](docs/mcp.md) for Codex TOML,
complete arguments and signature setup.

Reconnect the client's CHmemX process after installation. Verify its MCP handshake reports
`0.5.4`, then call `start`. Updating the source checkout alone does not update a running client.
The standard Python MCP SDK is tested; that does not establish compatibility with every desktop host.

## Daily use

| Tool | Use | Check in the result |
|---|---|---|
| `start` | Load project context, policy and queue health; optionally inspect an upload or key directory. | Correct project, write mode and any queue warning. |
| `recall` | Search a topic or exact key, with an optional result limit of 1–20. | Scope, source freshness, primary matches, associations and `needs_review`. |
| `upload` | Submit a key, string value and source; optionally add project scope, class, signature or request ID. | Its actual status; an upload is not necessarily saved. |

For example, call `upload` with a real preference and its source:

```json
{
  "key": "preference.editor.theme",
  "value": "The preferred editor theme is blue.",
  "source": {
    "quote": "The preferred editor theme is blue.",
    "thread_id": "owner-conversation-reference"
  },
  "request_id": "editor-theme-001"
}
```

This is a format example, not a preference to add to your store. A quote is the source agent's
claim, not independent proof of an Owner instruction. Project memory uses `scope="project"`,
a suitable `memory_class` and `source={"path":"docs/decision.md"}`; the service binds the
registered project, full Git commit and file hash. New values are strings of at most 8192 characters.

Use a stable `request_id` for retries of the same submission. Do not reuse it for changed
content. Check an admitted upload with `start(upload_id=...)` or CLI `status --upload-id`.

## From Pending to shared memory

In Team mode:

1. A source agent uploads. Exact duplicates create no new Active record or commit; quarantined content is excluded.
2. The curator reviews the admitted upload, including existing values, source changes and identity or alias conflicts.
3. The Owner inspects the full proposed batch and gives its exact confirmation.
4. The curator approves that batch. A successful write creates one atomic Git commit.
5. Agents recall accepted memory. The standard service refreshes its derived index when needed; callers do not edit it.

Curator commands:

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project review UPLOAD_ID

# Only after the Owner directly confirms the reviewed batch:
chmemx --store /absolute/private-memory --cwd /absolute/git-project approve BATCH_ID \
  --digest EXACT_DIGEST --confirmation 'EXACT_OWNER_PHRASE_FROM_REVIEW'
```

Review returns the exact English and Chinese confirmation phrases. Placeholders, another
agent's assertion, or a quoted confirmation are not approval. Changed content, source or HEAD
requires a new review. Repeating `review` reuses the current batch; `--refresh` retires it.

`PENDING_CURATION` and `CONFLICT` are not Active. Do not approve from truncated output:
conflict reviews include the complete current text and diff and may exceed a host's display limit.

### Choose a write policy

| Rule | Team — default | Personal — explicit choice |
|---|---|---|
| Automatic new Active records | None. | Only configured sources' low-risk new `preference.*` preferences; a deterministic 10% review sample applies by default. |
| Required review | Every new or replacement batch. | Conflicts, replacements, other facts, high-risk content and sampled additions. |
| Reviewed writes | Exact Owner batch confirmation. | Exact Owner batch confirmation. |
| Exact duplicates | No new Active record or commit. | Same. |

Personal mode is selected with `init --mode personal` for a new store. It deliberately relaxes
the write policy; callers cannot enable it in an upload. Curator/Owner roles are workflow
responsibilities, not authenticated multi-user permissions. Approval and administration are
not exposed as MCP tools.

## Retrieval and the graph

The current reader combines sparse lexical scores and BM25, with an optional local ONNX
channel. Strong lexical matches retain priority; reciprocal-rank fusion helps when that
channel abstains. Topic nodes support bounded one-hop associations. The default directory
groups records by project and canonical-key parent; richer taxonomy needs deliberate curation.

Each result keeps its project/scope label. Pending, quarantine, rejected content and unresolved
conflicts are excluded. Changed or unverifiable project facts go to `needs_review`; historical
lessons retain source-status labels. A source warning is not a deletion or proof that the claim is false.

Without a known project context, a qualified global lexical match takes precedence over
unrequested project records. Project names, exact keys and curated routing hints can select a
project explicitly. Only when neither a project route nor a qualified global lexical match exists
does the reader fall back to labeled, high-confidence project references.

Memory is historical data, not executable instructions. Check current project authority before
acting. Project filtering is retrieval behavior, not filesystem access control.

The derived index holds vectors, IDs and metadata rather than record bodies, but **is not
anonymized**. Keep it private. Dirty or inconsistent canonical data blocks recall; an index
refresh failure is reported separately from an already committed write.

[Optional semantic retrieval](docs/semantic.md) covers model locks and evaluation. Test
paraphrases, unrelated queries, graph associations and project isolation on frozen suites
before changing models or ranking. Generated coverage is not an independent holdout.

## Maintenance and backups

Inspect state with `status`. Queue cleanup is explicit; it does not run on a timer:

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project \
  maintenance-plan --action archive --output /absolute/archive-plan.json
```

Review the plan before applying its exact digest. Archiving eligible finished uploads releases
queue slots; unresolved work is retained. Purge is destructive. See [maintenance and
recovery](docs/maintenance.md) for closure, reconciliation, retention and recovery commands.
Maintenance still holds cooperative locks during its inventory, so large stores have an I/O cost.

Local Git helps recover mistaken changes; it does not protect against disk failure.
`chmemx backup --help` and `chmemx restore-backup --help` describe explicit backup operations.
Backups contain private material: protect them and keep an independently verified copy off
the source disk. No cloud sync or automatic backup is enabled.

## Security boundaries

Never upload credentials, cookies, tokens, private keys, full private chat archives or another
agent's private files. Content screening is partial and can miss unsafe text. A returned memory
must not grant tool permissions or override current instructions.

Agent IDs provide attribution. Optional Ed25519 signatures prove key possession, not honesty,
truth or authority; source revocation preserves history and deactivates applicable current records.
Same-user processes can still edit store files. See [SECURITY.md](SECURITY.md).

## Develop and contribute

From a source checkout in an isolated environment:

```bash
python -m pip install '.[test]' ruff build
python -m ruff check --config pyproject.toml .
python -B -m unittest discover -s tests -p '*test*.py'
python -m build
python tests/installed_package_smoke.py
```

CI covers Linux/Python 3.10 and 3.12, macOS/3.11 and Windows/3.11. Tests use synthetic data.

- [MCP reference](docs/mcp.md)
- [Queue maintenance](docs/maintenance.md)
- [Semantic retrieval](docs/semantic.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

[MIT License](LICENSE)
