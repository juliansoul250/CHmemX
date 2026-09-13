"""Non-editable wheel smoke, outside the checkout, using a synthetic owner."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import venv


def main():
    wheels = list((Path(__file__).resolve().parents[1] / "dist").glob("chmemx-*.whl"))
    assert len(wheels) == 1, "Expected exactly one release wheel"
    with tempfile.TemporaryDirectory(prefix="chmemx-wheel-") as name:
        root = Path(name).resolve()
        venv.create(root / "env", with_pip=True)
        python = root / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", str(wheels[0])], check=True
        )
        project = root / "project"
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        command = [
            str(python),
            "-I",
            "-B",
            "-m",
            "chmemx.cli",
            "--store",
            str(root / "store"),
            "--cwd",
            str(project),
            "--agent-id",
            "fictional-source",
        ]

        def cli(*args):
            return json.loads(
                subprocess.check_output([*command, *args], cwd=root, text=True, encoding="utf-8")
            )

        cli("init", "--project-id", "project-fictional")
        skill_catalog = root / "skills.json"
        skill_intent = root / "intent.json"
        skill_catalog.write_text(
            json.dumps(
                [
                    {
                        "id": "review",
                        "description": "review interface",
                        "capabilities": ["ui"],
                        "actions": ["read"],
                    },
                    {
                        "id": "generate",
                        "description": "review interface",
                        "capabilities": ["ui"],
                        "actions": ["generate"],
                    },
                ]
            ),
            encoding="utf-8",
        )
        skill_intent.write_text(
            json.dumps(
                {
                    "decision": "search",
                    "query": "review interface",
                    "capabilities": ["ui"],
                    "allowed_actions": ["read"],
                    "forbidden_actions": ["generate"],
                }
            ),
            encoding="utf-8",
        )
        selection = json.loads(
            subprocess.check_output(
                [
                    str(python),
                    "-I",
                    "-B",
                    "-m",
                    "chmemx.skill_retrieval",
                    "--catalog",
                    str(skill_catalog),
                    "--intent",
                    str(skill_intent),
                ],
                cwd=root,
                text=True,
                encoding="utf-8",
            )
        )
        assert [item["id"] for item in selection["candidates"]] == ["review"]
        assert selection["execution_authorized"] is False
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25"},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "upload",
                    "arguments": {
                        "key": "preference.editor.theme",
                        "value": "界面使用蓝色主题。",
                        "source": {"quote": "界面使用蓝色主题。"},
                        "request_id": "fictional-request",
                    },
                },
            },
        ]
        process = subprocess.run(
            [*command, "serve"],
            cwd=root,
            text=True,
            encoding="utf-8",
            input="\n".join(json.dumps(r) for r in requests) + "\n",
            capture_output=True,
            check=True,
        )
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        assert replies[0]["result"]["serverInfo"]["version"] == "0.5.5"
        pending = json.loads(replies[1]["result"]["content"][0]["text"])
        assert pending["status"] == "PENDING_CURATION"
        batch = cli("review", pending["upload_id"])
        assert cli("review", pending["upload_id"])["batch_digest"] == batch["batch_digest"]
        approved = cli(
            "approve",
            batch["batch_id"],
            "--digest",
            batch["batch_digest"],
            "--confirmation",
            batch["accepted_confirmations"]["en"],
        )
        assert (
            cli("status", "--upload-id", pending["upload_id"])["upload"]["status"]
            == "ACTIVE_COMMITTED"
        )
        assert (
            cli("recall", "preference.editor.theme")["entries"][0]["body"] == "界面使用蓝色主题。"
        )
        plan_path = root / "archive-plan.json"
        plan = cli(
            "maintenance-plan",
            "--action",
            "archive",
            "--older-than-days",
            "0",
            "--output",
            str(plan_path),
        )
        assert (
            cli("maintenance-apply", "--plan", str(plan_path), "--digest", plan["digest"])["status"]
            == "MAINTENANCE_COMPLETE"
        )
        assert cli("status", "--upload-id", pending["upload_id"])["upload"]["storage"] == "archive"
        assert (
            cli("recall", "preference.editor.theme")["entries"][0]["body"] == "界面使用蓝色主题。"
        )
        subprocess.run(
            ["git", "-C", str(root / "store"), "revert", "--no-edit", approved["commit"]],
            check=True,
            capture_output=True,
        )
        assert (
            cli("status", "--upload-id", pending["upload_id"])["upload"]["status"]
            == "COMMIT_NOT_CURRENT"
        )
        assert cli("review", pending["upload_id"])["status"] == "COMMIT_NOT_CURRENT"
        assert cli("recall", "preference.editor.theme")["entries"] == []
    print("INSTALLED_WHEEL_MCP_LIFECYCLE_PASS")


if __name__ == "__main__":
    main()
