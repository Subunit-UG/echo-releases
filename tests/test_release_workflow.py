"""Static safety contract for Echo's public release workflow."""
from __future__ import annotations

import base64
import contextlib
import io
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
CONTRACT_WORKFLOW = (ROOT / ".github/workflows/release-contract.yml").read_text(encoding="utf-8")
PR_CHECK_WORKFLOW = (ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
PLATFORM_TRUST_DOC = (ROOT / "docs/PLATFORM-TRUST.md").read_text(encoding="utf-8")
POLICY = ROOT / "scripts" / "release_policy.sh"
MACOS_VERIFIER = ROOT / "scripts" / "verify-platform-macos.sh"
MACOS_ARCHIVE_VALIDATOR = ROOT / "scripts" / "validate-macos-app-archive.py"
WINDOWS_VERIFIER = ROOT / "scripts" / "verify-platform-windows.ps1"
RELEASE_ASSETS = ROOT / "scripts" / "release-assets.sh"
UPDATER_VERIFIER = ROOT / "scripts" / "verify-updater-signatures.py"
ASSET_UPLOADER = ROOT / "scripts" / "upload-release-asset.py"


def azure_signing_scope_errors(workflow: str) -> list[str]:
    """Azure access belongs to opt-in steps; OIDC permissions are job-scoped."""
    errors: list[str] = []
    backend_condition = (
        "runner.os == 'Windows' && (vars.WINDOWS_SIGNING_BACKEND == 'artifact-signing' "
        "|| vars.WINDOWS_SIGNING_BACKEND == 'azure-key-vault')"
    )
    azure_reference = re.compile("azure", re.IGNORECASE)
    token_write = re.compile(r"id-token:\s*write", re.IGNORECASE)
    header, jobs = workflow.split("\njobs:\n", 1)
    if azure_reference.search(header) or token_write.search(header):
        errors.append("Azure/OIDC access is forbidden at workflow level")
    for job in re.finditer(r"(?ms)^  ([A-Za-z0-9_-]+):\n.*?(?=^  [A-Za-z0-9_-]+:\n|\Z)", jobs):
        name, block = job.group(1), job.group(0)
        steps = list(re.finditer(r"(?ms)^      - .*?(?=^      - |\Z)", block))
        for step in steps:
            text = step.group(0)
            # The backend enum itself grants no Azure access.
            if azure_reference.search(text.replace("azure-key-vault", "")):
                condition = re.search(r"^        if: (.+)$", text, re.MULTILINE)
                if condition is None or condition.group(1) != backend_condition:
                    errors.append(f"{name}: Azure step requires the explicit Windows backend condition")
            if token_write.search(text):
                errors.append(f"{name}: id-token permission must be at job level")
        outside_steps = re.sub(r"(?ms)^      - .*?(?=^      - |\Z)", "", block)
        if name == "build":
            # GitHub has no step-level permissions. This matrix job signs Windows
            # during Tauri bundling; other matrix rows never run Azure actions.
            if not any("uses: azure/login@" in step.group(0) for step in steps):
                errors.append("build: OIDC requires a Windows signing login step")
            permissions = re.search(r"(?m)^    permissions:\n(?:^      [^\n]*\n)*", outside_steps)
            if permissions is not None:
                restricted = re.sub(r"(?m)^      id-token: write[^\n]*\n", "", permissions.group(0))
                outside_steps = outside_steps.replace(permissions.group(0), restricted, 1)
        if azure_reference.search(outside_steps) or token_write.search(outside_steps):
            errors.append(f"{name}: Azure/OIDC access outside the signing scope")
    return errors


def workflow_run_block(after: str) -> str:
    marker_index = WORKFLOW.index(after)
    run_marker = "\n        run: |\n"
    run_index = WORKFLOW.index(run_marker, marker_index) + len(run_marker)
    lines: list[str] = []
    for line in WORKFLOW[run_index:].splitlines():
        if line and not line.startswith("          "):
            break
        lines.append(line[10:] if line else "")
    return "\n".join(lines) + "\n"


def workflow_job_block(job: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job)}:\n.*?(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        WORKFLOW,
    )
    if match is None:
        raise AssertionError(f"workflow job not found: {job}")
    return match.group(0)


class ReleaseWorkflowContract(unittest.TestCase):
    def assert_v1_trust_chain(self, workflow: str) -> None:
        self.assertIn(
            "requires_platform_trust: ${{ steps.policy.outputs.requires_platform_trust }}",
            workflow,
        )
        self.assertIn(
            "needs: [prepare, reserve, build, verify_updater_trust, verify_macos_platform_trust, "
            "verify_windows_platform_trust]",
            workflow,
        )
        self.assertIn(
            "if: always() && needs.prepare.outputs.should_build == 'true' && "
            "needs.build.result == 'success'",
            workflow,
        )
        self.assertEqual(
            workflow.count(
                "if: needs.prepare.outputs.should_build == 'true' && "
                "needs.prepare.outputs.requires_platform_trust == 'true'"
            ),
            2,
        )
        self.assertIn(
            "\n          REQUIRED: ${{ needs.prepare.outputs.requires_platform_trust }}\n",
            workflow,
        )
        self.assertIn('[ "$MAC_RESULT" = success ]', workflow)
        self.assertIn('[ "$WINDOWS_RESULT" = success ]', workflow)
        self.assertIn('[ "$UPDATER_RESULT" = success ]', workflow)
        self.assertIn("needs: [prepare, reserve, build, verify_updater_trust, platform_trust]", workflow)
        self.assertIn(
            "if: always() && needs.prepare.outputs.should_build == 'true' && "
            "needs.verify_updater_trust.result == 'success' && needs.platform_trust.result == 'success'",
            workflow,
        )
        for expected_recheck in (
            'verify_hash "$trust_dir/$mac" "$VERIFIED_MAC_APP_SHA256" "$mac native trust"',
            'verify_hash "$trust_dir/$dmg" "$VERIFIED_MAC_DMG_SHA256" "$dmg"',
            'verify_hash "$trust_dir/$win64" "$VERIFIED_WINDOWS_X64_SHA256" "$win64 native trust"',
            'verify_hash "$trust_dir/$winarm" "$VERIFIED_WINDOWS_ARM64_SHA256" "$winarm native trust"',
        ):
            self.assertIn(expected_recheck, workflow)
        self.assertIn("was replaced after hash verification", workflow)
        self.assertIn("verify-updater-signatures.py", workflow)
        self.assertIn("public_key_sha256", workflow)
        self.assertIn('expected_sig_hash=$(jq -r --arg key "$key"', workflow)
        self.assertIn('verify_hash "$trust_dir/$name.sig" "$expected_sig_hash" "$name.sig"', workflow)
        self.assertIn('python3 scripts/upload-release-asset.py \\', workflow)
        self.assertNotIn("gh release upload", workflow)

    def test_matrix_uploads_only_to_reserved_release_id(self) -> None:
        self.assertIn("reserve:\n    needs: prepare", WORKFLOW)
        self.assertIn("releaseId: ${{ needs.reserve.outputs.release_id }}", WORKFLOW)
        self.assertNotIn('releaseName: "Echo ${{ needs.prepare.outputs.tag }}"', WORKFLOW)
        self.assertRegex(
            WORKFLOW,
            r"build:\n\s+needs: \[prepare, reserve\]",
        )
        self.assertIn("source_commit: ${{ steps.source.outputs.commit }}", WORKFLOW)
        self.assertIn('[ "$(git rev-parse HEAD)" = "$SOURCE_COMMIT" ]', WORKFLOW)
        self.assertIn('[ "$(git -C _source rev-parse HEAD)" = "$SOURCE_COMMIT" ]', WORKFLOW)

    def test_public_build_never_caches_private_compiler_outputs(self) -> None:
        for name, workflow in (("build.yml", WORKFLOW), ("pr-check.yml", PR_CHECK_WORKFLOW)):
            with self.subTest(workflow=name):
                self.assertNotIn("swatinem/rust-cache", workflow)
                self.assertNotRegex(workflow, r"(?im)^\s*uses:\s*[^\n]*(?:rust-cache|cache[^\n]*rust)")
        self.assertEqual(CONTRACT_WORKFLOW.count('".github/workflows/pr-check.yml"'), 2)

    def test_same_tag_reservation_is_serialized_and_duplicate_safe(self) -> None:
        self.assertIn("group: echo-release-${{ needs.prepare.outputs.tag }}", WORKFLOW)
        self.assertIn("if [ \"$count\" -gt 1 ]; then", WORKFLOW)
        self.assertIn("release reservation lost its invariant", WORKFLOW)
        self.assertIn("refusing ambiguous assets", RELEASE_ASSETS.read_text(encoding="utf-8"))

    def test_all_triggers_share_one_full_workflow_lock(self) -> None:
        workflow_header = WORKFLOW.split("\njobs:\n", 1)[0]
        self.assertRegex(
            workflow_header,
            r"\nconcurrency:\n\s+group: echo-release-build\n\s+cancel-in-progress: false\n",
        )

    def test_release_discovery_retries_eventual_consistency_without_losing_fail_closed(self) -> None:
        self.assertIn("release discovery settle $visibility_attempt/4", WORKFLOW)
        self.assertIn("for visibility_attempt in $(seq 1 10)", WORKFLOW)
        self.assertIn('direct=$(gh api "repos/$REPO/releases/$id")', WORKFLOW)
        self.assertIn('-f tag_name="$TAG" -f name="Echo $TAG"', WORKFLOW)
        self.assertIn("reservation_ok=0", WORKFLOW)
        self.assertIn("after bounded retry", WORKFLOW)

    def test_publish_uses_exact_id_and_separates_stable_from_draft_rc(self) -> None:
        self.assertIn('release_assets_load "$repo" "$id" "$tag" "$prerelease"', WORKFLOW)
        self.assertNotIn('select(.tag_name==\\"$tag\\")][0]', WORKFLOW)
        self.assertIn("-F draft=false -F prerelease=false -f make_latest=true", WORKFLOW)
        self.assertIn('if [ "$mode" = "draft_rc" ]; then', WORKFLOW)
        self.assertIn("retained as private draft", WORKFLOW)
        self.assertIn("RC release $id escaped its draft/prerelease boundary", WORKFLOW)
        self.assertIn("prerelease: ${{ needs.prepare.outputs.prerelease == 'true' }}", WORKFLOW)

    def test_final_manifest_id_must_match_numeric_upload_response(self) -> None:
        publish_script = workflow_run_block(
            "      - name: Assemble + validate latest.json; publish Stable or retain Draft RC\n"
        )
        self.assertIn("upload_response=$(python3 scripts/upload-release-asset.py", publish_script)
        self.assertIn("uploaded_latest_id=$(jq -er", publish_script)
        self.assertIn('verify_uploaded_manifest_id "$final" "$uploaded_latest_id"', publish_script)
        match = re.search(
            r"(?ms)^verify_uploaded_manifest_id\(\) \{\n.*?^\}\n",
            publish_script,
        )
        self.assertIsNotNone(match)
        function = match.group(0)
        valid = subprocess.run(
            ["bash", "-c", function + "\nverify_uploaded_manifest_id \"$FINAL\" 91"],
            env={**os.environ, "FINAL": json.dumps({"assets": [{"name": "latest.json", "id": 91}]})},
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)

        invalid_releases = (
            ({"assets": [{"name": "latest.json", "id": 92}]}, "replaced"),
            ({"assets": []}, "found 0"),
            ({"assets": [{"name": "latest.json", "id": 91}, {"name": "latest.json", "id": 92}]}, "found 2"),
        )
        for release, expected_error in invalid_releases:
            with self.subTest(release=release):
                result = subprocess.run(
                    ["bash", "-c", function + "\nverify_uploaded_manifest_id \"$FINAL\" 91"],
                    env={**os.environ, "FINAL": json.dumps(release)},
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stdout)

    def test_schedule_ignores_suffix_tags_and_docs_match(self) -> None:
        self.assertIn("grep -E '^v[0-9]+\\.[0-9]+\\.[0-9]+$'", WORKFLOW)
        self.assertIn("Automatische Poller bauen ausschließlich suffixlose Stable-Tags", README)
        self.assertIn("niemals `Latest`", README)

    def test_only_one_component_can_create_a_release(self) -> None:
        creates = re.findall(r'repos/\$REPO/releases"', WORKFLOW)
        self.assertEqual(len(creates), 1, "only reserve may POST a release")

    def test_release_gate_changes_always_trigger_contract_ci(self) -> None:
        for path in (
            "scripts/release_policy.sh",
            "scripts/release-assets.sh",
            "scripts/verify-updater-signatures.py",
            "scripts/upload-release-asset.py",
            "scripts/validate-macos-app-archive.py",
            "scripts/verify-platform-macos.sh",
            "scripts/verify-platform-windows.ps1",
            "tests/test_release_workflow.py",
            "docs/PLATFORM-TRUST.md",
        ):
            self.assertEqual(CONTRACT_WORKFLOW.count(f'"{path}"'), 2, path)

    def test_v1_platform_trust_chain_is_fail_closed(self) -> None:
        self.assert_v1_trust_chain(WORKFLOW)

    def test_platform_gate_runtime_rejects_skips_failures_and_invalid_hashes(self) -> None:
        script = workflow_run_block("      - id: gate\n")
        valid_hash = "a" * 64
        evidence = {
            "public_key_sha256": valid_hash,
            "mac": {
                "name": "echo_aarch64.app.tar.gz", "id": 1, "sha256": valid_hash,
                "sig_name": "echo_aarch64.app.tar.gz.sig", "sig_id": 2,
                "sig_sha256": valid_hash, "signed_filename": "echo.app.tar.gz",
            },
            "linux": {
                "name": "echo_1.0.0-rc.1_amd64.AppImage", "id": 3, "sha256": valid_hash,
                "sig_name": "echo_1.0.0-rc.1_amd64.AppImage.sig", "sig_id": 4,
                "sig_sha256": valid_hash, "signed_filename": "echo_1.0.0-rc.1_amd64.AppImage",
            },
            "windows_x64": {
                "name": "echo_1.0.0-rc.1_x64-setup.exe", "id": 5, "sha256": valid_hash,
                "sig_name": "echo_1.0.0-rc.1_x64-setup.exe.sig", "sig_id": 6,
                "sig_sha256": valid_hash, "signed_filename": "echo_1.0.0-rc.1_x64-setup.exe",
            },
            "windows_arm64": {
                "name": "echo_1.0.0-rc.1_arm64-setup.exe", "id": 7, "sha256": valid_hash,
                "sig_name": "echo_1.0.0-rc.1_arm64-setup.exe.sig", "sig_id": 8,
                "sig_sha256": valid_hash, "signed_filename": "echo_1.0.0-rc.1_arm64-setup.exe",
            },
        }
        base_env = {
            **os.environ,
            "TAG": "v1.0.0-rc.1",
            "REQUIRED": "true",
            "UPDATER_RESULT": "success",
            "UPDATER_EVIDENCE": json.dumps(evidence, separators=(",", ":")),
            "MAC_RESULT": "success",
            "WINDOWS_RESULT": "success",
            "MAC_APP_HASH": valid_hash,
            "MAC_DMG_HASH": valid_hash,
            "WINDOWS_X64_HASH": valid_hash,
            "WINDOWS_ARM64_HASH": valid_hash,
        }
        negative_overrides = (
            {"MAC_RESULT": "skipped"},
            {"MAC_RESULT": "failure"},
            {"WINDOWS_RESULT": "cancelled"},
            {"UPDATER_RESULT": "skipped"},
            {"UPDATER_EVIDENCE": "{}"},
            {"WINDOWS_ARM64_HASH": ""},
            {"MAC_DMG_HASH": "A" * 64},
            {"REQUIRED": "unexpected"},
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary) / "github-output"
            for overrides in negative_overrides:
                with self.subTest(overrides=overrides):
                    env = {**base_env, **overrides, "GITHUB_OUTPUT": str(output)}
                    result = subprocess.run(
                        ["bash", "-c", script],
                        env=env,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stdout)

            successful = subprocess.run(
                ["bash", "-c", script],
                env={**base_env, "GITHUB_OUTPUT": str(output)},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(successful.returncode, 0, successful.stderr)
            emitted = output.read_text(encoding="utf-8")
            for output_name in (
                "app_archive_sha256",
                "dmg_sha256",
                "windows_x64_sha256",
                "windows_arm64_sha256",
            ):
                self.assertIn(f"{output_name}={valid_hash}\n", emitted)

            legacy = subprocess.run(
                ["bash", "-c", script],
                env={
                    **base_env,
                    "REQUIRED": "false",
                    "TAG": "v0.5.166",
                    "MAC_RESULT": "skipped",
                    "WINDOWS_RESULT": "skipped",
                    "UPDATER_EVIDENCE": json.dumps(evidence, separators=(",", ":")).replace(
                        "1.0.0-rc.1", "0.5.166"
                    ),
                    "GITHUB_OUTPUT": str(output),
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            self.assertIn("0.x release", legacy.stdout)

    def test_contract_rejects_each_publish_bypass_mutation(self) -> None:
        mutations = {
            "publish drops aggregate gate": WORKFLOW.replace(
                "needs: [prepare, reserve, build, verify_updater_trust, platform_trust]",
                "needs: [prepare, reserve, build]",
                1,
            ),
            "publish ignores gate result": WORKFLOW.replace(
                "needs.platform_trust.result == 'success'",
                "true",
                1,
            ),
            "publish drops direct updater dependency": WORKFLOW.replace(
                "needs: [prepare, reserve, build, verify_updater_trust, platform_trust]",
                "needs: [prepare, reserve, build, platform_trust]",
                1,
            ),
            "aggregate gate cannot inspect skipped verifiers": WORKFLOW.replace(
                "if: always() && needs.prepare.outputs.should_build == 'true'",
                "if: needs.prepare.outputs.should_build == 'true'",
                1,
            ),
            "aggregate gate ignores policy output": WORKFLOW.replace(
                "          REQUIRED: ${{ needs.prepare.outputs.requires_platform_trust }}",
                '          REQUIRED: "false"',
                1,
            ),
            "aggregate gate accepts skipped updater verifier": WORKFLOW.replace(
                '[ "$UPDATER_RESULT" = success ]',
                '[ -n "$UPDATER_RESULT" ]',
                1,
            ),
            "aggregate gate ignores Windows failure": WORKFLOW.replace(
                '[ "$WINDOWS_RESULT" = success ]',
                '[ -n "$WINDOWS_RESULT" ]',
                1,
            ),
            "publish drops Windows ARM hash": WORKFLOW.replace(
                '"$VERIFIED_WINDOWS_ARM64_SHA256" "$winarm native trust"',
                '"$VERIFIED_WINDOWS_X64_SHA256" "$winarm native trust"',
                1,
            ),
            "publish drops asset replacement check": WORKFLOW.replace(
                "was replaced after hash verification",
                "was inspected",
            ),
            "publish skips signature hash recheck": WORKFLOW.replace(
                'verify_hash "$trust_dir/$name.sig" "$expected_sig_hash" "$name.sig"',
                'echo "$expected_sig_hash" >/dev/null',
                1,
            ),
        }
        for label, mutated in mutations.items():
            with self.subTest(label=label), self.assertRaises(AssertionError):
                self.assert_v1_trust_chain(mutated)

    def test_platform_jobs_use_canonical_draft_and_exact_asset_names(self) -> None:
        for job in ("verify_updater_trust:", "verify_macos_platform_trust:", "verify_windows_platform_trust:"):
            self.assertIn(job, WORKFLOW)
        self.assertEqual(WORKFLOW.count("source scripts/release-assets.sh"), 4)
        for exact_name in (
            'app_archive="echo_aarch64.app.tar.gz"',
            'dmg="echo_${version}_aarch64.dmg"',
            'win64="echo_${version}_x64-setup.exe"',
            'winarm="echo_${version}_arm64-setup.exe"',
        ):
            self.assertIn(exact_name, WORKFLOW)
        self.assertIn("repos/$RELEASE_ASSETS_REPO/releases/assets/$RELEASE_ASSET_LAST_ID", RELEASE_ASSETS.read_text(encoding="utf-8"))
        self.assertNotIn("grep -E '^echo_.+_x64-setup", WORKFLOW)

    def test_draft_verifiers_have_job_scoped_access_without_mutation_credentials(self) -> None:
        for job in (
            "verify_updater_trust",
            "verify_macos_platform_trust",
            "verify_windows_platform_trust",
        ):
            with self.subTest(job=job):
                block = workflow_job_block(job)
                permission = re.search(
                    r"(?m)^    permissions:\n((?:      [a-z-]+: (?:read|write|none)\n)+)",
                    block,
                )
                self.assertIsNotNone(permission)
                self.assertEqual(permission.group(1), "      contents: write\n")

                secret_names = set(re.findall(r"secrets\.([A-Z0-9_]+)", block))
                self.assertLessEqual(
                    secret_names,
                    {"GITHUB_TOKEN", "ECHO_TAURI_DEPLOY_KEY"},
                    f"{job} gained a PAT or unrelated secret",
                )
                self.assertIn("GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}", block)
                for forbidden_write in (
                    "gh api -X POST",
                    "gh api -X PATCH",
                    "gh api -X PUT",
                    "gh api -X DELETE",
                    "gh release create",
                    "gh release edit",
                    "gh release delete",
                    "gh release upload",
                    "upload-release-asset.py",
                    "tauri-action",
                    "git push",
                ):
                    self.assertNotIn(forbidden_write, block)

    def test_updater_gate_binds_four_artifacts_and_four_signatures_for_all_versions(self) -> None:
        self.assertIn("verify_updater_trust:\n    needs: [prepare, reserve, build]", WORKFLOW)
        self.assertIn("if: needs.prepare.outputs.should_build == 'true'", WORKFLOW)
        self.assertNotIn("requires_platform_trust == 'true'\n    runs-on: ubuntu-24.04", WORKFLOW)
        self.assertIn('names=("$mac" "$linux" "$win64" "$winarm")', WORKFLOW)
        self.assertIn('release_asset_download "$name.sig"', WORKFLOW)
        for field in ("id", "sha256", "sig_id", "sig_sha256", "signed_filename"):
            self.assertIn(field, WORKFLOW)
        self.assertIn('signed_names=("echo.app.tar.gz" "$linux" "$win64" "$winarm")', WORKFLOW)
        self.assertIn("0.x release: cryptographic updater gate passed", WORKFLOW)

    def test_v1_missing_signer_identities_fail_closed_and_are_documented(self) -> None:
        for variable in (
            "APPLE_TEAM_ID",
            "APPLE_SIGNING_AUTHORITY",
            "WINDOWS_PUBLISHER",
        ):
            self.assertIn(f"vars.{variable}", WORKFLOW)
            self.assertIn(f"repository variable {variable} is required for v1+", WORKFLOW)
            self.assertIn(f"`{variable}`", PLATFORM_TRUST_DOC)

    def test_azure_access_is_limited_to_opt_in_signing_scope(self) -> None:
        self.assertEqual(azure_signing_scope_errors(WORKFLOW), [])

    def test_azure_scope_rejects_missing_conditions_and_oidc_permission_leaks(self) -> None:
        build = workflow_job_block("build")
        cloud_condition = (
            "        if: runner.os == 'Windows' && (vars.WINDOWS_SIGNING_BACKEND == 'artifact-signing' "
            "|| vars.WINDOWS_SIGNING_BACKEND == 'azure-key-vault')\n"
        )
        for step in re.finditer(r"(?ms)^      - .*?(?=^      - |\Z)", build):
            text = step.group(0)
            if "azure" not in text.replace("azure-key-vault", "").lower():
                continue
            self.assertIn(cloud_condition, text)
            with self.subTest(step=text.splitlines()[0]):
                mutant = WORKFLOW.replace(text, text.replace(cloud_condition, "", 1), 1)
                self.assertTrue(azure_signing_scope_errors(mutant))
        mutants = {
            "workflow permission": WORKFLOW.replace("permissions:\n", "permissions:\n  id-token: write\n", 1),
            "other job permission": WORKFLOW.replace("  prepare:\n", "  prepare:\n    permissions:\n      id-token: write\n", 1),
            "workflow Azure variable": WORKFLOW.replace("\njobs:\n", "\nenv:\n  AZURE_CLIENT_ID: synthetic\n\njobs:\n", 1),
        }
        for label, mutant in mutants.items():
            with self.subTest(mutation=label):
                self.assertNotEqual(mutant, WORKFLOW)
                self.assertTrue(azure_signing_scope_errors(mutant))


class ReleasePolicyContract(unittest.TestCase):
    def run_policy(self, event: str, ref: str, tag: str, mode: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(POLICY), event, ref, tag, mode],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_manual_stable_and_draft_rc_are_accepted(self) -> None:
        stable = self.run_policy("workflow_dispatch", "v0.5.166", "v0.5.166", "stable")
        self.assertEqual(stable.returncode, 0, stable.stderr)
        self.assertEqual(
            stable.stdout,
            "release_mode=stable\n"
            "prerelease=false\n"
            "requires_platform_trust=false\n",
        )

        rc = self.run_policy("workflow_dispatch", "v0.5.166-rc.1", "v0.5.166-rc.1", "draft_rc")
        self.assertEqual(rc.returncode, 0, rc.stderr)
        self.assertEqual(
            rc.stdout,
            "release_mode=draft_rc\n"
            "prerelease=true\n"
            "requires_platform_trust=false\n",
        )

    def test_major_one_and_later_require_platform_trust_for_stable_and_rc(self) -> None:
        cases = (
            ("workflow_dispatch", "v1.0.0", "stable"),
            ("schedule", "v2.3.4", "stable"),
            ("workflow_dispatch", "v1.0.0-rc.1", "draft_rc"),
            ("workflow_dispatch", "v12.0.1-rc.99", "draft_rc"),
        )
        for event, ref, mode in cases:
            with self.subTest(ref=ref, mode=mode):
                result = self.run_policy(event, ref, ref, mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("requires_platform_trust=true\n", result.stdout)

    def test_zero_major_never_requires_platform_trust(self) -> None:
        for ref, mode in (
            ("v0.999.999", "stable"),
            ("v0.999.999-rc.7", "draft_rc"),
        ):
            with self.subTest(ref=ref, mode=mode):
                result = self.run_policy("workflow_dispatch", ref, ref, mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("requires_platform_trust=false\n", result.stdout)

    def test_automation_cannot_select_draft_rc(self) -> None:
        for event in ("schedule", "repository_dispatch", "push", ""):
            with self.subTest(event=event):
                result = self.run_policy(event, "v0.5.166-rc.1", "v0.5.166-rc.1", "draft_rc")
                self.assertNotEqual(result.returncode, 0)

    def test_stable_rejects_suffixes_and_malformed_values(self) -> None:
        cases = (
            ("v0.5.166-rc.1", "v0.5.166-rc.1"),
            ("v0.5.166", "v0.5.166-rc.1"),
            ("v0.5.166", "v0.5.167"),
            ("v1.0.0", "v0.9.999"),
            ("v0.5.166;touch-pwned", "v0.5.166"),
            ("v01.0.0", "v01.0.0"),
            ("v1.00.0", "v1.00.0"),
            ("v1.0.00", "v1.0.00"),
            ("", "v0.5.166"),
            ("v0.5", "v0.5"),
        )
        for ref, tag in cases:
            with self.subTest(ref=ref, tag=tag):
                result = self.run_policy("workflow_dispatch", ref, tag, "stable")
                self.assertNotEqual(result.returncode, 0)

    def test_draft_rc_rejects_wrong_shape_or_mismatched_tag(self) -> None:
        cases = (
            ("v0.5.166", "v0.5.166"),
            ("v0.5.166-alpha.1", "v0.5.166-alpha.1"),
            ("v0.5.166-rc.0", "v0.5.166-rc.0"),
            ("v0.5.166-RC.1", "v0.5.166-RC.1"),
            ("v0.5.166-rc.1", "v0.5.166-rc.2"),
            ("v0.5.166-rc.1\nforged", "v0.5.166-rc.1\nforged"),
        )
        for ref, tag in cases:
            with self.subTest(ref=ref, tag=tag):
                result = self.run_policy("workflow_dispatch", ref, tag, "draft_rc")
                self.assertNotEqual(result.returncode, 0)


class PlatformVerifierContract(unittest.TestCase):
    def test_shell_entrypoints_have_valid_bash_syntax(self) -> None:
        for script in (POLICY, RELEASE_ASSETS, MACOS_VERIFIER):
            with self.subTest(script=script.name):
                result = subprocess.run(
                    ["bash", "-n", str(script)],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_macos_verifier_checks_both_shipped_containers_fail_closed(self) -> None:
        script = MACOS_VERIFIER.read_text(encoding="utf-8")
        for required in (
            "codesign --verify --deep --strict",
            "Authority=$expected_authority",
            "Identifier=$expected_bundle_id",
            "TeamIdentifier=$expected_team_id",
            'verify_signer_metadata "$dmg" "DMG"',
            "xcrun stapler validate",
            "spctl --assess --type execute",
            "spctl --assess --type open",
            "hdiutil attach",
            "validate-macos-app-archive.py",
            "CFBundleShortVersionString",
            "CFBundleVersion",
            "expected_version",
            "evidence retained:",
        ):
            self.assertIn(required, script)
        self.assertNotIn("rm -", script)
        self.assertNotRegex(
            script,
            r"(?:codesign|spctl|xcrun stapler)[^\n]*\|\|\s*(?:true|:)",
        )

    def test_windows_verifier_checks_native_trust_and_timestamp_fail_closed(self) -> None:
        script = WINDOWS_VERIFIER.read_text(encoding="utf-8")
        for required in (
            "Get-AuthenticodeSignature",
            "SignatureStatus]::Valid",
            "TimeStamperCertificate",
            "ExpectedPublisher",
            "ExpectedVersion",
            "ProductVersion",
            "FileVersion",
            "verify", "/pa", "/all",
        ):
            self.assertIn(required, script)
        for credential_marker in ("PASSWORD", "TOKEN", "AZURE_CLIENT_SECRET", "APPLE_"):
            self.assertNotIn(credential_marker, script.upper())

    def test_verifiers_reject_missing_arguments_without_credentials(self) -> None:
        result = subprocess.run(
            ["bash", str(MACOS_VERIFIER)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)


@unittest.skipUnless(shutil.which("minisign"), "minisign is required for behavioral tests")
class UpdaterSignatureVerifierContract(unittest.TestCase):
    names = (
        "echo.app.tar.gz",
        "echo_1.0.0-rc.1_amd64.AppImage",
        "echo_1.0.0-rc.1_x64-setup.exe",
        "echo_1.0.0-rc.1_arm64-setup.exe",
    )

    @staticmethod
    def generate_key(root: pathlib.Path, stem: str) -> tuple[pathlib.Path, pathlib.Path]:
        public = root / f"{stem}.pub"
        secret = root / f"{stem}.key"
        result = subprocess.run(
            ["minisign", "-G", "-W", "-p", str(public), "-s", str(secret)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        return public, secret

    def prepare_fixture(self, root: pathlib.Path) -> tuple[pathlib.Path, list[tuple[str, pathlib.Path, pathlib.Path]]]:
        public, secret = self.generate_key(root, "trusted")
        config = root / "tauri.conf.json"
        config.write_text(
            json.dumps(
                {
                    "plugins": {
                        "updater": {
                            "pubkey": base64.b64encode(public.read_bytes()).decode("ascii")
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        pairs = []
        for index, signed_name in enumerate(self.names):
            artifact = root / f"artifact-{index}"
            raw_signature = root / f"signature-{index}.minisig"
            encoded_signature = root / f"signature-{index}.sig"
            artifact.write_bytes(f"synthetic artifact {index}\n".encode())
            result = subprocess.run(
                [
                    "minisign", "-S", "-s", str(secret), "-m", str(artifact),
                    "-x", str(raw_signature), "-t", f"timestamp:1700000000\tfile:{signed_name}",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            encoded_signature.write_bytes(base64.b64encode(raw_signature.read_bytes()))
            pairs.append((signed_name, artifact, encoded_signature))
        return config, pairs

    def run_verifier(
        self,
        root: pathlib.Path,
        config: pathlib.Path,
        pairs: list[tuple[str, pathlib.Path, pathlib.Path]],
        run_name: str,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "python3", str(UPDATER_VERIFIER), "--config", str(config),
            "--evidence-dir", str(root / f"evidence-{run_name}"),
            "--output-json", str(root / f"result-{run_name}.json"),
        ]
        for signed_name, artifact, signature in pairs:
            command.extend(("--pair", signed_name, str(artifact), str(signature)))
        return subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)

    def test_valid_four_platform_fixture_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            config, pairs = self.prepare_fixture(root)
            result = self.run_verifier(root, config, pairs, "valid")
            self.assertEqual(result.returncode, 0, result.stderr)
            evidence = json.loads((root / "result-valid.json").read_text(encoding="utf-8"))
            self.assertEqual(len(evidence["pairs"]), 4)
            self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", pair["artifact_sha256"]) for pair in evidence["pairs"]))

    def test_artifact_signature_key_and_version_filename_mutations_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            config, pairs = self.prepare_fixture(root)

            mutated_artifact = pairs[0][1]
            original_artifact = mutated_artifact.read_bytes()
            mutated_artifact.write_bytes(original_artifact + b"mutation")
            result = self.run_verifier(root, config, pairs, "artifact")
            self.assertNotEqual(result.returncode, 0)
            mutated_artifact.write_bytes(original_artifact)

            signature = pairs[1][2]
            original_signature = signature.read_bytes()
            raw = bytearray(base64.b64decode(original_signature, validate=True))
            raw[60] ^= 1
            signature.write_bytes(base64.b64encode(raw))
            result = self.run_verifier(root, config, pairs, "signature")
            self.assertNotEqual(result.returncode, 0)
            signature.write_bytes(original_signature)

            wrong_public, _ = self.generate_key(root, "wrong")
            wrong_config = root / "wrong-tauri.conf.json"
            wrong_config.write_text(
                json.dumps({"plugins": {"updater": {"pubkey": base64.b64encode(wrong_public.read_bytes()).decode("ascii")}}}),
                encoding="utf-8",
            )
            result = self.run_verifier(root, wrong_config, pairs, "key")
            self.assertNotEqual(result.returncode, 0)

            wrong_names = list(pairs)
            wrong_names[1] = ("echo_1.0.0_amd64.AppImage", pairs[1][1], pairs[1][2])
            result = self.run_verifier(root, config, wrong_names, "filename")
            self.assertNotEqual(result.returncode, 0)

    def test_requires_exactly_four_pairs_and_source_compiles(self) -> None:
        compile(UPDATER_VERIFIER.read_text(encoding="utf-8"), str(UPDATER_VERIFIER), "exec")
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            config, pairs = self.prepare_fixture(root)
            result = self.run_verifier(root, config, pairs[:3], "three")
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("signatures verified", result.stdout)


class NumericReleaseAssetUploaderContract(unittest.TestCase):
    @staticmethod
    def load_module():
        spec = importlib.util.spec_from_file_location("release_asset_uploader", ASSET_UPLOADER)
        if spec is None or spec.loader is None:
            raise AssertionError("cannot load numeric release asset uploader")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_upload_targets_numeric_release_id_and_never_places_token_in_url(self) -> None:
        module = self.load_module()
        with tempfile.TemporaryDirectory() as temporary:
            payload = pathlib.Path(temporary) / "latest.json"
            payload.write_text('{"version":"1.0.0"}\n', encoding="utf-8")
            captured = {}

            class Response(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    self.close()

            def fake_urlopen(request, timeout):
                captured["request"] = request
                captured["timeout"] = timeout
                return Response(b'{"id":99,"name":"latest.json"}')

            output = io.StringIO()
            with (
                mock.patch.object(module, "urlopen", fake_urlopen),
                mock.patch.dict(os.environ, {"GH_TOKEN": "synthetic-token"}),
                mock.patch.object(
                    sys,
                    "argv",
                    ["upload", "Subunit-UG/echo-releases", "42", "latest.json", str(payload)],
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(module.main(), 0)

            request = captured["request"]
            self.assertEqual(
                request.full_url,
                "https://uploads.github.com/repos/Subunit-UG/echo-releases/releases/42/assets?name=latest.json",
            )
            self.assertNotIn("synthetic-token", request.full_url)
            self.assertEqual(request.data, payload.read_bytes())
            self.assertEqual(captured["timeout"], 60)
            self.assertEqual(json.loads(output.getvalue()), {"id": 99, "name": "latest.json"})

    def test_invalid_or_tag_like_release_id_is_rejected_before_network(self) -> None:
        module = self.load_module()
        with tempfile.TemporaryDirectory() as temporary:
            payload = pathlib.Path(temporary) / "latest.json"
            payload.write_text("{}", encoding="utf-8")
            for release_id in ("v1.0.0", "0", "-1", "1/other"):
                with (
                    self.subTest(release_id=release_id),
                    mock.patch.dict(os.environ, {"GH_TOKEN": "synthetic-token"}),
                    mock.patch.object(
                        sys,
                        "argv",
                        ["upload", "Subunit-UG/echo-releases", release_id, "latest.json", str(payload)],
                    ),
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit),
                ):
                    module.main()


class ReleaseAssetResolverContract(unittest.TestCase):
    def run_download(self, root: pathlib.Path, assets: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        fake_gh = fake_bin / "gh"
        fake_gh.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" > \"$GH_CALL_LOG\"\n"
            "printf 'synthetic asset bytes'\n",
            encoding="utf-8",
        )
        fake_gh.chmod(0o755)
        script = f'''source "{RELEASE_ASSETS}"
RELEASE_ASSETS_REPO=Subunit-UG/echo-releases
RELEASE_ASSETS_JSON="$ASSETS_JSON"
release_asset_download expected.bin "$DESTINATION" || exit 1
printf 'asset_id=%s\\n' "$RELEASE_ASSET_LAST_ID"
'''
        return subprocess.run(
            ["bash", "-c", script],
            env={
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "ASSETS_JSON": json.dumps({"assets": assets}),
                "DESTINATION": str(root / "downloaded.bin"),
                "GH_CALL_LOG": str(root / "gh-call"),
            },
            check=False,
            capture_output=True,
            text=True,
        )

    def test_exact_asset_name_uses_its_numeric_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            result = self.run_download(root, [{"name": "expected.bin", "id": 123}])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("asset_id=123", result.stdout)
            self.assertEqual((root / "downloaded.bin").read_bytes(), b"synthetic asset bytes")
            self.assertIn("releases/assets/123", (root / "gh-call").read_text(encoding="utf-8"))

    def test_duplicate_missing_or_invalid_asset_id_fails_before_download(self) -> None:
        cases = (
            [],
            [{"name": "expected.bin", "id": 1}, {"name": "expected.bin", "id": 2}],
            [{"name": "expected.bin", "id": "v1.0.0"}],
            [{"name": "expected.bin", "id": 0}],
        )
        for index, assets in enumerate(cases):
            with self.subTest(assets=assets), tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary)
                result = self.run_download(root, assets)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / "gh-call").exists(), index)
                self.assertFalse((root / "downloaded.bin").exists(), index)


class MacOSArchiveValidatorContract(unittest.TestCase):
    @staticmethod
    def add_member(
        archive: tarfile.TarFile,
        name: str,
        *,
        kind: bytes = tarfile.REGTYPE,
        data: bytes = b"",
        linkname: str = "",
    ) -> None:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.mode = 0o755 if kind == tarfile.DIRTYPE else 0o644
        member.linkname = linkname
        member.size = len(data) if kind == tarfile.REGTYPE else 0
        archive.addfile(member, io.BytesIO(data) if data else None)

    def run_validator(
        self,
        archive_path: pathlib.Path,
        destination: pathlib.Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(MACOS_ARCHIVE_VALIDATOR), str(archive_path), str(destination)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_validator_preserves_internal_framework_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            archive_path = root / "Echo.app.tar.gz"
            destination = root / "evidence"
            with tarfile.open(archive_path, "w:gz") as archive:
                self.add_member(
                    archive,
                    "Echo.app/Contents/Frameworks/Foo.framework/Versions/A/Foo",
                    data=b"signed-framework-placeholder",
                )
                self.add_member(
                    archive,
                    "Echo.app/Contents/Frameworks/Foo.framework/Versions/Current",
                    kind=tarfile.SYMTYPE,
                    linkname="A",
                )
                self.add_member(
                    archive,
                    "Echo.app/Contents/Frameworks/Foo.framework/Foo",
                    kind=tarfile.SYMTYPE,
                    linkname="Versions/Current/Foo",
                )
                self.add_member(
                    archive,
                    "Echo.app/Contents/Resources/original",
                    data=b"shared-resource-placeholder",
                )
                self.add_member(
                    archive,
                    "Echo.app/Contents/Resources/copy",
                    kind=tarfile.LNKTYPE,
                    linkname="Echo.app/Contents/Resources/original",
                )

            result = self.run_validator(archive_path, destination)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("archive validated and safely extracted:", result.stdout)
            current = destination / "Echo.app/Contents/Frameworks/Foo.framework/Versions/Current"
            framework = destination / "Echo.app/Contents/Frameworks/Foo.framework/Foo"
            self.assertTrue(current.is_symlink())
            self.assertTrue(framework.is_symlink())
            self.assertEqual(os.readlink(current), "A")
            self.assertEqual(os.readlink(framework), "Versions/Current/Foo")
            self.assertEqual(framework.read_bytes(), b"signed-framework-placeholder")
            original = destination / "Echo.app/Contents/Resources/original"
            copy = destination / "Echo.app/Contents/Resources/copy"
            self.assertEqual(copy.read_bytes(), b"shared-resource-placeholder")
            self.assertEqual(original.stat().st_ino, copy.stat().st_ino)

    def test_validator_rejects_unsafe_archives_before_extracting_any_member(self) -> None:
        cases = {
            "parent traversal": [
                ("safe", tarfile.REGTYPE, b"first", ""),
                ("../escape", tarfile.REGTYPE, b"bad", ""),
            ],
            "absolute path": [("/escape", tarfile.REGTYPE, b"bad", "")],
            "escaping symlink": [
                ("Echo.app/link", tarfile.SYMTYPE, b"", "../../outside"),
            ],
            "escaping hard link": [
                ("Echo.app/hard", tarfile.LNKTYPE, b"", "../outside"),
            ],
            "duplicate member": [
                ("Echo.app/file", tarfile.REGTYPE, b"first", ""),
                ("Echo.app/file", tarfile.REGTYPE, b"second", ""),
            ],
            "macOS name collision": [
                ("Echo.app/File", tarfile.REGTYPE, b"first", ""),
                ("Echo.app/file", tarfile.REGTYPE, b"second", ""),
            ],
            "macOS prefix collision": [
                ("Echo.app/One/file", tarfile.REGTYPE, b"first", ""),
                ("echo.app/Two/file", tarfile.REGTYPE, b"second", ""),
            ],
            "member below regular file": [
                ("Echo.app/Contents", tarfile.REGTYPE, b"file", ""),
                ("Echo.app/Contents/payload", tarfile.REGTYPE, b"bad", ""),
            ],
            "member below link": [
                ("Echo.app/Contents", tarfile.DIRTYPE, b"", ""),
                ("Echo.app/pivot", tarfile.SYMTYPE, b"", "Contents"),
                ("Echo.app/pivot/payload", tarfile.REGTYPE, b"bad", ""),
            ],
            "forward hard link": [
                (
                    "Echo.app/copy",
                    tarfile.LNKTYPE,
                    b"",
                    "Echo.app/original",
                ),
                ("Echo.app/original", tarfile.REGTYPE, b"later", ""),
            ],
            "link cycle": [
                ("Echo.app/a", tarfile.SYMTYPE, b"", "b"),
                ("Echo.app/b", tarfile.SYMTYPE, b"", "a"),
            ],
            "special device": [("Echo.app/device", tarfile.FIFOTYPE, b"", "")],
        }
        for label, members in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary)
                archive_path = root / "malicious.tar.gz"
                destination = root / "evidence"
                with tarfile.open(archive_path, "w:gz") as archive:
                    for name, kind, data, linkname in members:
                        self.add_member(
                            archive,
                            name,
                            kind=kind,
                            data=data,
                            linkname=linkname,
                        )

                result = self.run_validator(archive_path, destination)

                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("error: archive rejected:", result.stderr)
                self.assertNotIn("safely extracted", result.stdout)
                self.assertTrue(destination.is_dir())
                self.assertEqual(list(destination.iterdir()), [])

    def test_validator_source_compiles(self) -> None:
        compile(
            MACOS_ARCHIVE_VALIDATOR.read_text(encoding="utf-8"),
            str(MACOS_ARCHIVE_VALIDATOR),
            "exec",
        )


SOURCE_REPO = "Subunit-UG/echo-app"
SOURCE_WORKFLOWS = ("build.yml", "pr-check.yml", "promote-stable.yml")


def owner_move_errors(texts: dict[str, str]) -> list[str]:
    """Org move 2026-10-30: one canonical private source, no retired owner in executable lines."""
    errors: list[str] = []
    for name, text in texts.items():
        code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
        if any("subunit-ai" in line.lower() for line in code):
            errors.append(f"{name}: retired owner subunit-ai outside comments")
        if name not in SOURCE_WORKFLOWS:
            continue
        settings = [line.strip() for line in code if line.strip().startswith("SOURCE_REPO:")]
        if settings != [f"SOURCE_REPO: {SOURCE_REPO}"]:
            errors.append(f"{name}: SOURCE_REPO must be set exactly once to {SOURCE_REPO}")
        urls = re.findall(r"git@github\.com:[^\s\"']+", text)
        if not urls or any(url != "git@github.com:$SOURCE_REPO.git" for url in urls):
            errors.append(f"{name}: private source must be addressed only via $SOURCE_REPO")
    return errors


class OrgMoveWorkflowContract(unittest.TestCase):
    def setUp(self) -> None:
        github = ROOT / ".github"
        self.texts = {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(github.rglob("*"))
            if path.is_file() and path.suffix in {".yml", ".yaml", ".cjs", ".js", ".sh", ".py"}
        }

    def test_workflows_use_one_canonical_source_and_no_retired_owner(self) -> None:
        self.assertTrue(set(SOURCE_WORKFLOWS) <= set(self.texts))
        self.assertEqual(owner_move_errors(self.texts), [])

    def test_each_owner_regression_is_detected(self) -> None:
        build = self.texts["build.yml"]
        mutations = {
            "old source owner": ("build.yml", build.replace(f"SOURCE_REPO: {SOURCE_REPO}", "SOURCE_REPO: subunit-ai/echo-tauri")),
            "hardcoded source URL": ("build.yml", build.replace(
                '"git@github.com:$SOURCE_REPO.git" .', '"git@github.com:Subunit-UG/echo-app.git" .', 1)),
            "second SOURCE_REPO": ("promote-stable.yml", self.texts["promote-stable.yml"].replace(
                "concurrency:", "  SOURCE_REPO: Subunit-UG/echo-tauri\nconcurrency:", 1)),
            "retired owner in a step": ("auto-freigabe.yml", self.texts["auto-freigabe.yml"] + "\n      - run: gh api repos/subunit-ai/echo-releases\n"),
        }
        for label, (name, mutated) in mutations.items():
            with self.subTest(label=label):
                self.assertNotEqual(mutated, self.texts[name])
                self.assertNotEqual(owner_move_errors({**self.texts, name: mutated}), [])


if __name__ == "__main__":
    unittest.main()
