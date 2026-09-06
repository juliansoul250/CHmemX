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
            return json.loads(subprocess.check_output([*command, *args], cwd=root, text=True))

        cli("init", "--project-id", "project-fictional")
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
                        "value": "The theme is blue.",
                        "source": {"quote": "The theme is blue."},
                        "request_id": "fictional-request",
                    },
                },
            },
        ]
        process = subprocess.run(
            [*command, "serve"],
            cwd=root,
            text=True,
            input="\n".join(json.dumps(r) for r in requests) + "\n",
            capture_output=True,
            check=True,
        )
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        assert replies[0]["result"]["serverInfo"]["version"] == "0.4.0"
        pending = json.loads(replies[1]["result"]["content"][0]["text"])
        assert pending["status"] == "PENDING_CURATION"
        batch = cli("review", pending["upload_id"])
        assert cli("review", pending["upload_id"])["batch_digest"] == batch["batch_digest"]
        cli(
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
        assert len(cli("recall", "preference.editor.theme")["entries"]) == 1
    print("INSTALLED_WHEEL_MCP_LIFECYCLE_PASS")


if __name__ == "__main__":
    main()
