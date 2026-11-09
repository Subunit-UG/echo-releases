"""Contract for the deploy-key probe (scripts/probe-deploy-key.sh and its daily workflow).

Network and GitHub are replaced by stand-ins on PATH. Checked: allowlist, missing key, pinned host key, success and
failure, and that no key material is left behind in output or files.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "probe-deploy-key.sh"
WORKFLOW = (ROOT / ".github/workflows/deploy-key-probe.yml").read_text(encoding="utf-8")
CONTRACT_WORKFLOW = (ROOT / ".github/workflows/release-contract.yml").read_text(encoding="utf-8")
STAND_IN = "only a stand-in"  # not a key; proves nothing leaks
PIN = re.search(r'github_ed25519_fingerprint="([^"]+)"', PROBE.read_text(encoding="utf-8")).group(1)


def run(args, key=STAND_IN, git=0, fingerprint=PIN, hosts=1):
    root = pathlib.Path(tempfile.mkdtemp(prefix="key-probe-test-"))
    try:
        bin_dir, runner = root / "bin", root / "runner"
        bin_dir.mkdir()
        runner.mkdir()
        log = root / "git.log"

        def fake(name, body):
            path = bin_dir / name
            path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
            path.chmod(0o755)

        fake("ssh-keyscan", "\n".join(f'echo "github.com ssh-ed25519 AAAA{i}"' for i in range(hosts)))
        fake("ssh-keygen", f'echo "256 {fingerprint} github.com (ED25519)"')
        fake("git", f"printf '%s|%s\\n' \"$*\" \"$GIT_SSH_COMMAND\" >> '{log}'; echo 'refs/heads/main deadbeef'; exit {git}")
        env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "RUNNER_TEMP": str(runner), "HOME": str(root)}
        if key is not None:
            env["SOURCE_DEPLOY_KEY"] = key
        result = subprocess.run(["bash", str(PROBE), *args], capture_output=True, text=True, env=env, check=False)
        return {
            "status": result.returncode,
            "out": result.stdout,
            "err": result.stderr,
            "log": log.read_text(encoding="utf-8") if log.exists() else "",
            "leftover": sorted(os.listdir(runner)),
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


class DeployKeyProbeScript(unittest.TestCase):
    ARGS = ["ECHO_TAURI_DEPLOY_KEY", "Subunit-UG/echo-app"]

    def test_readable_repository_passes_without_refs_or_key_in_output(self) -> None:
        r = run(self.ARGS)
        self.assertEqual(r["status"], 0, r["err"])
        self.assertRegex(r["out"], r"(?m)^PASS deploy-key ECHO_TAURI_DEPLOY_KEY → Subunit-UG/echo-app readable$")
        self.assertRegex(r["log"], r"(?m)^ls-remote --heads git@github\.com:Subunit-UG/echo-app\.git\|")
        for option in ("IdentitiesOnly=yes", "StrictHostKeyChecking=yes", "BatchMode=yes"):
            self.assertIn(option, r["log"])
        self.assertNotRegex(r["out"] + r["err"] + r["log"], "deadbeef|only a stand-in")
        self.assertEqual(r["leftover"], [], "credential directory is removed")

    def test_unreadable_repository_fails_visibly_and_still_cleans_up(self) -> None:
        r = run(self.ARGS, git=128)
        self.assertEqual(r["status"], 1)
        self.assertIn("::error title=Deploy key invalid::ECHO_TAURI_DEPLOY_KEY cannot read Subunit-UG/echo-app", r["out"])
        self.assertNotIn(STAND_IN, r["out"] + r["err"])
        self.assertEqual(r["leftover"], [])

    def test_missing_or_empty_key_exits_65_without_git(self) -> None:
        for key in (None, ""):
            with self.subTest(key=key):
                r = run(self.ARGS, key=key)
                self.assertEqual(r["status"], 65)
                self.assertEqual(r["log"], "")
                self.assertIn("Deploy key missing", r["out"])

    def test_allowlist_rejects_other_pairs_and_extra_arguments(self) -> None:
        for args in (["ECHO_TAURI_DEPLOY_KEY", "Subunit-UG/echo-server"], ["OTHER_KEY", "Subunit-UG/echo-app"],
                     ["ECHO_TAURI_DEPLOY_KEY", "Subunit-UG/echo-app", "extra"], ["ECHO_TAURI_DEPLOY_KEY"]):
            with self.subTest(args=args):
                r = run(args)
                self.assertEqual(r["status"], 64)
                self.assertEqual(r["log"], "")

    def test_wrong_or_ambiguous_github_host_key_exits_68_without_git(self) -> None:
        for kwargs in ({"fingerprint": "SHA256:wrong"}, {"hosts": 2}, {"hosts": 0}):
            with self.subTest(**kwargs):
                r = run(self.ARGS, **kwargs)
                self.assertEqual(r["status"], 68)
                self.assertEqual(r["log"], "")
                self.assertEqual(r["leftover"], [])


class DeployKeyProbeWorkflow(unittest.TestCase):
    def test_runs_daily_and_on_demand_with_read_only_permissions(self) -> None:
        self.assertRegex(WORKFLOW, r'(?m)^  schedule:\n    - cron: "[0-9]+ [0-9]+ \* \* \*"$')
        self.assertRegex(WORKFLOW, r"(?m)^  workflow_dispatch:$")
        self.assertRegex(WORKFLOW, r"(?m)^permissions:\n  contents: read$")
        self.assertNotIn("write", WORKFLOW.split("jobs:", 1)[0])

    def test_probes_exactly_the_release_deploy_key_against_its_repository(self) -> None:
        self.assertEqual(re.findall(r"secrets\.([A-Z0-9_]+)", WORKFLOW), ["ECHO_TAURI_DEPLOY_KEY"])
        self.assertIn("SOURCE_DEPLOY_KEY: ${{ secrets.ECHO_TAURI_DEPLOY_KEY }}", WORKFLOW)
        self.assertIn("run: bash scripts/probe-deploy-key.sh ECHO_TAURI_DEPLOY_KEY Subunit-UG/echo-app", WORKFLOW)
        self.assertIn("persist-credentials: false", WORKFLOW)

    def test_probe_changes_trigger_the_contract_ci(self) -> None:
        for path in ("scripts/probe-deploy-key.sh", ".github/workflows/deploy-key-probe.yml", "tests/test_deploy_key_probe.py"):
            with self.subTest(path=path):
                self.assertEqual(CONTRACT_WORKFLOW.count(f'- "{path}"'), 2)


if __name__ == "__main__":
    unittest.main()
