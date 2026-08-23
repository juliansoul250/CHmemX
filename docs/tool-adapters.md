# Tool adapters

Every tool should expose the same `memory-graph` Skill behavior:

- load the shared skill at task start;
- run scoped Active lookup and content-grid vector lookup;
- use recalled memory only after authority checks;
- package new source-owned material at task end;
- stop after upload.

Tools may use different private installation mechanisms. Each tool installs its own adapter; a
coordinator must not edit another tool's private configuration. Keep one stable source Agent ID per
tool and one curator Agent ID for permanent writes.
