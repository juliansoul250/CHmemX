# Shared Agent Memory Graph

A local-first, Git-backed memory system for multiple AI tools with centralized curation.

Different agents may read the same accepted memory graph and upload their own pending packages.
Source agents cannot promote uploads directly. A curator validates provenance, removes exact
duplicates, blocks conflicts, organizes content into topic/project grids, requests owner approval,
and commits permanent memory atomically to Git.

## Core model

```text
tool-specific upload inboxes
          ↓
PENDING_CURATION
          ↓
central curator
validate · deduplicate · compare Active · route into content grids
          ↓
owner conflict decision and exact batch confirmation
          ↓
Git-backed Active Memory
          ↓
content-grid vector pointer
          ↓
all participating tools
```

## Properties

- One canonical Active value per scope/class/key identity.
- Global preferences and project memory remain explicitly scoped.
- Uploads, candidates, quarantine, rejected data, and unresolved conflicts are never recalled.
- Exact owner confirmation binds the sealed batch ID, digest, candidate list, and Git HEAD.
- Future writes are committed by one curator identity; source-tool identity remains audit metadata.
- Offline sparse vector routing supports English and Chinese without a model download.
- Git history is preserved through supersede and revert instead of history rewriting.
- No runtime dependency outside Python's standard library and Git.

## Quick start

```bash
git clone <repository-url>
cd shared-agent-memory-graph

export MEMORY_GRAPH_KIT="$PWD"
export MEMORY_GRAPH_HOME="$HOME/.memory-graph/store"

python3 runtime/simple_memory.py init \
  --project-root /path/to/first/project \
  --project-id project-example \
  --title "Example Project" \
  --confirmed
```

Read [docs/quickstart.md](docs/quickstart.md) before using writes. The initialization command is a
local mutation and should only run after the owner chooses the store and first registered project.

## Repository layout

```text
runtime/simple_memory.py          canonical Git memory interface
scripts/assemble_inventory.py     validate one source upload
scripts/curate_uploads.py         deduplicate and compare uploads with Active
scripts/vector_memory.py          build/query the content-grid vector pointer
schemas/agent-export-v1.schema.json
skills/memory-graph/SKILL.md      portable shared skill
examples/                         safe templates
docs/                             architecture, curation, security, adapters
tests/                            acceptance tests
```

Open the [interactive architecture map](docs/architecture.html). It is source-bound to the first
public code commit and passed all 9 showcase checks plus four desktop containment checks in light
and dark themes.

## Security boundary

This prevents normal workflow mistakes; it is not an OS security boundary against a malicious
process running as the same user. Do not store credentials or private chat logs. See
[docs/security.md](docs/security.md).

## Tests

```bash
python3 tests/test_assemble_inventory.py
python3 tests/test_curate_uploads.py
python3 tests/test_vector_memory.py
PYTHONPATH=runtime python3 tests/simple_memory_test.py
```

## License

MIT
