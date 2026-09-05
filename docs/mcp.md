# MCP setup and daily use

[简体中文](zh-CN/mcp.md) · [Home](../README.md)

Install the [versioned package](../README.md#start-with-v03), then initialize a new store.
Keep the store outside this public source repository. Python 3.10+ and Git are required;
no third-party Python package is needed for the base server. macOS is locally tested and
Linux runs in CI. The Windows lock backend exists; full Windows acceptance remains pending.

## Client configuration

For clients that accept the conventional `mcpServers` JSON shape:

```json
{
  "mcpServers": {
    "chmemx": {
      "command": "/absolute/venv/bin/chmemx",
      "args": ["--store", "/absolute/private-memory", "--cwd", "/absolute/git-project", "--agent-id", "source-one", "serve"]
    }
  }
}
```

Codex TOML equivalent:

```toml
[mcp_servers.chmemx]
command = "/absolute/venv/bin/chmemx"
args = ["--store", "/absolute/private-memory", "--cwd", "/absolute/git-project", "--agent-id", "codex-main", "serve"]
```

Use absolute paths and a separate source ID per client. Each client launches a stdio process;
there is no port, daemon, or background network listener. Configuration file locations differ
by client/version. Do not copy another tool's private settings. The official Python MCP client
passes an end-to-end test; this is not a claim that every desktop client/version has been tested.
The server supports tools, initialization and ping, not resources, prompts, HTTP or subscriptions.

## Three daily tools

| Tool | Input | Result |
|---|---|---|
| `start` | optional `query` | Project context, policy mode, source statistics, optional recall |
| `recall` | `query`, optional `limit` 1–20 | Accepted Active bodies, source badges, project labels, graph associations |
| `upload` | `key`, string `value`, `source`; optional `scope`, `memory_class`, `signature` | Duplicate, Pending, conflict, quarantine, or policy-authorized commit |

Example global preference:

```json
{
  "key": "preference.editor.theme",
  "value": "The preferred editor theme is blue.",
  "source": {"quote": "The preferred editor theme is blue.", "thread_id": "owner-conversation-reference"}
}
```

Global scope allows preferences only. A quote digest is a source-agent claim, not proof that the
Owner said it. For project memory use `scope="project"`, a suitable `memory_class`, and
`source={"path":"docs/decision.md"}`. The server binds the registered source root, current full
commit and committed file bytes. Classes: preference, decision, lesson, state, evidence.
Values are bounded strings, not role/tool message objects. Approval still verifies the source.

The response always matters: `PENDING_CURATION` is not Active. `EXACT_DUPLICATE` creates no commit.
New records share a topic node by their canonical-key parent (for example `preference.editor`).
Their record vectors remain independent; scoped, bounded associations link related preferences.
Advanced curators can retain richer reviewed node definitions through the existing bulk workflow.
`CONFLICT` returns the old/new bodies and diff; do not choose for the Owner. `QUARANTINED` redacts
the rejected body. `ACTIVE_COMMITTED` in personal mode carries a policy receipt, not a fabricated
Owner confirmation. A failed index rebuild is reported separately; it does not undo a valid commit.

## Team and personal modes

Team is the default. Only the curator prepares a sealed review:

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project review UPLOAD_ID
```

Show its full contents to the Owner. After a direct exact confirmation:

```bash
chmemx --store /absolute/private-memory --cwd /absolute/git-project approve BATCH_ID \
  --digest EXACT_DIGEST --confirmation 'EXACT_OWNER_PHRASE_FROM_REVIEW'
```

These placeholders are not authorization. Do not fabricate the confirmation, reuse an old one,
or accept another agent's relay or quoted/annotated text. Changed source/HEAD/batch requires review again.
Approval, key registration, policy editing and source revocation are deliberately absent from MCP tools.
Bulk curators retain the [existing workflow](curation.md).

Personal mode is selected only at new-store initialization with `--mode personal`. It trusts only
that configured `--agent-id`, considers low-risk new `preference.*` / `fact.*` keys, and routes a
deterministic 10% digest sample to pre-write review. Conflicts and sensitive changes always stay
pending. Three conflicts in the last five source events raise review for that source. This is
basic operational monitoring, not a reputation model or a guarantee against poisoning.
The operator owns `policy.json`; a caller cannot select mode or identity in an upload argument.

Register a second project using its exact Git root:

```bash
chmemx --store /absolute/private-memory --cwd /absolute/second-project \
  register-project --project-id project-second --title 'Second project'
```

## Optional signatures and targeted deactivation

Install the `signatures` extra. Register a base64-encoded 32-byte Ed25519 public key:

```bash
chmemx --store /absolute/private-memory trust-source source-one --public-key-file source-one.pub
```

The private key stays with its source tool. Sign UTF-8 JSON with sorted keys, compact separators
and literal Unicode: `{"agent_id":...,"payload":...,"nonce":...,"expires_at":...}`.
`payload` includes all five normalized tool input fields: key, value, source, scope, memory_class.
Signature is excluded. `expires_at` is an integer Unix time within the next 24 hours.
Attach `signature={"signature":"base64 bytes","nonce":"unique-safe-id","expires_at":...}`.
Registered sources must sign; changed content and reused source/nonces fail. Signatures prove
key possession, not honesty, role entitlement or safe instructions. Registry and keys need operator control.

If a source is compromised, inspect before applying:

```bash
chmemx --store /absolute/private-memory revoke-source source-one
chmemx --store /absolute/private-memory revoke-source source-one --apply --digest EXACT_PLAN_DIGEST
```

The second command requires an explicit Owner decision on the plan. It deactivates only current
Active records still attributed to that source and blocks its future uploads. It preserves Git
history and later independent replacements. It does not blindly revert every historical commit.
Local same-user processes can still edit files; this is governance, not an OS security boundary.
