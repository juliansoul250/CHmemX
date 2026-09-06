"""Public CLI. Serve starts stdio only and has no listener or cloud dependency."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .service import Service


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(prog="chmemx")
    parser.add_argument(
        "--store",
        type=Path,
        default=Path(os.environ.get("MEMORY_GRAPH_HOME", str(Path.home() / ".memory-graph/store"))),
    )
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--agent-id", default="local-agent")
    parser.add_argument("--model-dir", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--project-id", default="project-local")
    init.add_argument("--mode", choices=["team", "personal"], default="team")
    register = sub.add_parser("register-project")
    register.add_argument("--project-id", required=True)
    register.add_argument("--title", required=True)
    sub.add_parser("serve")
    status = sub.add_parser("status")
    status.add_argument("--upload-id")
    sub.add_parser("maintenance-plan")
    nonce_maintenance = sub.add_parser("maintenance-nonces")
    nonce_maintenance.add_argument("--digest", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--output-root", type=Path, required=True)
    backup.add_argument(
        "--pending-root",
        action="append",
        default=[],
        help="label=/explicit/shared/pending/root; never auto-discovered",
    )
    restore = sub.add_parser("restore-backup")
    restore.add_argument("--directory", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    recall = sub.add_parser("recall")
    recall.add_argument("query")
    review = sub.add_parser("review")
    review.add_argument("upload_id")
    review.add_argument("--refresh", action="store_true")
    approve = sub.add_parser("approve")
    approve.add_argument("batch_id")
    approve.add_argument("--digest", required=True)
    approve.add_argument(
        "--confirmation",
        required=True,
        help="Exact phrase given directly by the Owner after review",
    )
    plan = sub.add_parser("revoke-source")
    plan.add_argument("source_agent")
    plan.add_argument("--apply", action="store_true")
    plan.add_argument("--digest")
    trust = sub.add_parser("trust-source")
    trust.add_argument("source_agent")
    trust.add_argument("--public-key-file", type=Path, required=True)
    args = parser.parse_args()
    service = Service(args.store, args.cwd, args.agent_id, args.model_dir)
    try:
        if args.command == "serve":
            from .mcp_server import serve

            return serve(service)
        if args.command == "init":
            result = service.initialize(args.project_id, args.mode)
        elif args.command == "register-project":
            result = service.runtime.register_project(
                args.cwd, args.project_id, args.title, confirmed=True
            )
        elif args.command == "status":
            result = service.start(upload_id=args.upload_id)
        elif args.command == "maintenance-plan":
            result = service.queue.maintenance_plan()
        elif args.command == "maintenance-nonces":
            result = service.queue.maintain_nonces(args.digest)
        elif args.command == "backup":
            roots = dict(x.split("=", 1) for x in args.pending_root)
            result = service.runtime.create_backup(args.output_root, extra_pending_roots=roots)
        elif args.command == "restore-backup":
            result = service.runtime.restore_backup(args.directory, args.destination)
        elif args.command == "recall":
            result = service.recall(args.query)
        elif args.command == "review":
            result = service.review(args.upload_id, refresh=args.refresh)
        elif args.command == "trust-source":
            result = service.trust_source(
                args.source_agent, args.public_key_file.read_text(encoding="utf-8").strip()
            )
        elif args.command == "revoke-source":
            result = (
                service.revoke_apply(args.source_agent, args.digest)
                if args.apply
                else service.revoke_plan(args.source_agent)
            )
        else:
            result = service.approve(args.batch_id, args.digest, args.confirmation)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "code": getattr(error, "code", type(error).__name__),
                    "message": str(error),
                    "details": getattr(error, "details", {}),
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
