# Quick start

For v0.3, start with the [MCP quick setup](mcp.md). The manual workflow below
is retained for existing stores and bulk curators; it is no longer required for daily tool use.

[简体中文](zh-CN/quickstart.md)

## Environment

```bash
export MEMORY_GRAPH_KIT="/path/to/shared-agent-memory-graph"
export MEMORY_GRAPH_HOME="$HOME/.memory-graph/store"
```

## Initialize once

Choose one real Git project and run the confirmed initialization command shown in the root README.
Initialization creates a separate Git store and never edits the source project.

## Read

Run `start` at each task start. Build the first vector index after initialization:

```bash
python3 "$MEMORY_GRAPH_KIT/scripts/vector_memory.py" build \
  --store "$MEMORY_GRAPH_HOME" \
  --taxonomy "$MEMORY_GRAPH_KIT/examples/content-directory.example.json" \
  --output "$MEMORY_GRAPH_KIT/vector-index.json" --replace
```

Use `vector_memory.py recall` for content-grid routing across registered subprojects.

After the first index exists, maintain a redacted golden-query suite and use `optimize` after every
approved commit. The command recalculates corpus IDF, evaluates bounded scoring profiles, and refuses
to replace the published index when generated coverage or the golden suite regresses:

```bash
python3 "$MEMORY_GRAPH_KIT/scripts/vector_memory.py" optimize \
  --store "$MEMORY_GRAPH_HOME" \
  --taxonomy /path/to/content-directory.json \
  --suite /path/to/recall-evaluation.json \
  --output "$HOME/.memory-graph/vector-index.json" \
  --report "$HOME/.memory-graph/recall-quality-report.json" \
  --replace
```

## Upload

Each source tool writes `memorygraph-agent-export-v1` JSON using the schema and example. The upload
is pending input, not memory. It must not be recalled or imported by the source tool.

## Curate and approve

Use `curate_uploads.py` to combine uploads for one scope/project. Review its duplicate, conflict,
and routing evidence. Import the curated inventory as the curator, show full batch review, then wait
for the exact owner phrase printed by the runtime. Rebuild the vector index after commit.
