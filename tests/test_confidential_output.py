"""Private diagnostics never cross the public runner's stdout/stderr boundary."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/run-confidential.sh'
ACTION = ROOT / '.github/actions/confidential-tauri/index.cjs'
WORKFLOWS = ROOT / '.github/workflows'
PIN = '84b9d35b5fc46c1e45415bdb6144030364f7ebc5'
CANARY = 'PRIVATE_SOURCE_CANARY_532'
INJECTION = '::error title=private::' + CANARY


def run_block(workflow, marker):
    tail = workflow[workflow.index(marker):]
    tail = tail.split('        run: |\n', 1)[1]
    lines = []
    for line in tail.splitlines():
        if line and not line.startswith('          '):
            break
        lines.append(line[10:])
    return '\n'.join(lines) + '\n'


def confidential_contract(texts):
    """Explicit public/private boundary inventory, independent of tool output format."""
    build, pr, promote = (texts[name] for name in ('build', 'pr-check', 'promote-stable'))
    expected = {
        'build': {
            'bash _release-ci/scripts/run-confidential.sh node': 1,
            'bash _release-ci/scripts/run-confidential.sh npm ci': 1,
            'uses: ./_release-ci/.github/actions/confidential-tauri': 1,
            'bash scripts/run-confidential.sh bash -s -- "$expected"': 1,
            'bash scripts/run-confidential.sh python3 scripts/verify-updater-signatures.py': 1,
        },
        'pr-check': {
            'bash "$GITHUB_WORKSPACE/_release-ci/scripts/run-confidential.sh" npm ci': 2,
            'bash "$GITHUB_WORKSPACE/_release-ci/scripts/run-confidential.sh" npm run build': 2,
            'bash "$GITHUB_WORKSPACE/_release-ci/scripts/run-confidential.sh" cargo check --locked --message-format short': 2,
            'bash "$GITHUB_WORKSPACE/_release-ci/scripts/run-confidential.sh" cargo test --locked --message-format short': 1,
            'bash "$GITHUB_WORKSPACE/_release-ci/scripts/run-confidential.sh" cargo clippy --locked --message-format short': 2,
        },
        'promote-stable': {
            'bash scripts/run-confidential.sh python3 - "$TAG"': 2,
            'bash scripts/run-confidential.sh python3 scripts/held-stable.py verify': 1,
            'bash scripts/run-confidential.sh python3 scripts/held-stable.py publish': 1,
        },
    }
    for workflow, commands in expected.items():
        for command, count in commands.items():
            if texts[workflow].count(command) != count:
                raise AssertionError(f'unprotected private boundary: {workflow}: {command}')
        if re.search(r'(?m)^\s*(?:run:\s*)?(?:npm |cargo |node <<|python3 - "\$TAG")', texts[workflow]):
            raise AssertionError('direct private tool invocation bypasses the boundary')
    for text, count in ((build, 1), (pr, 2)):
        for guard in ('if [ -e "$path" ] || [ -L "$path" ]; then',
                      'repository: ${{ github.repository }}', 'ref: ${{ github.sha }}',
                      'path: _release-ci'):
            if text.count(guard) != count:
                raise AssertionError('public controls must be independently checked out behind a collision guard')
        if text.count('persist-credentials: false') != count + (text is build):
            raise AssertionError('trusted checkouts must not persist credentials')
    if f'ref: {PIN}' not in build or 'repository: tauri-apps/tauri-action' not in build:
        raise AssertionError('upstream action pin changed')
    if 'uses: tauri-apps/tauri-action@' in build:
        raise AssertionError('upstream action must never run outside output isolation')
    if not build.index('Checkout private source') < build.index('Reserve trusted CI paths') < build.index('Checkout trusted public CI controls') < build.index('Checkout pinned upstream Tauri action'):
        raise AssertionError('private root must precede trusted checkouts')
    return expected


class ConfidentialAssertions:
    def assert_silent(self, result, status):
        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
        self.assertNotIn(CANARY, result.stdout + result.stderr)
        self.assertNotIn(INJECTION, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')


class ConfidentialCommandTests(ConfidentialAssertions, unittest.TestCase):
    def test_both_streams_child_processes_exit_status_permissions_and_cleanup(self):
        program = '''import os, pathlib, stat, subprocess, sys
root=pathlib.Path(os.environ['RUNNER_TEMP'])
directory=next(root.iterdir())
assert stat.S_IMODE(directory.stat().st_mode)==0o700
assert stat.S_IMODE((directory/'output').stat().st_mode)==0o600
print(sys.argv[1], flush=True)
print(sys.argv[1], file=sys.stderr, flush=True)
subprocess.run([sys.executable,'-c','import os,sys; os.write(1,sys.argv[1].encode()); os.write(2,sys.argv[1].encode())',sys.argv[1]],check=True)
pathlib.Path(os.environ['GITHUB_OUTPUT']).write_text('result=forwarded\\n')
assert os.environ['INPUT_RELEASEID']=='42'
assert sys.stdin.read()=='inherited stdin'
sys.exit(int(sys.argv[2]))
'''
        for status in (0, 1, 37, 101, 127):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); capture = root / 'capture'; capture.mkdir()
                env = {**os.environ, 'RUNNER_TEMP': str(capture), 'INPUT_RELEASEID': '42',
                       'GITHUB_OUTPUT': str(root / 'output')}
                result = subprocess.run(['bash', str(HELPER), sys.executable, '-c', program, INJECTION, str(status)],
                                        env=env, input='inherited stdin', capture_output=True, text=True)
                self.assert_silent(result, status)
                self.assertEqual(list(capture.iterdir()), [])
                self.assertEqual((root / 'output').read_text(), 'result=forwarded\n')

    def test_no_capture_directory_fails_before_command_and_signal_keeps_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = subprocess.run(['bash', str(HELPER), 'touch', str(root / 'executed')],
                                    env={**os.environ, 'RUNNER_TEMP': str(root / CANARY)}, capture_output=True, text=True)
            self.assert_silent(result, 1)
            self.assertFalse((root / 'executed').exists())
            result = subprocess.run(['bash', str(HELPER), 'bash', '-c', 'kill -TERM $$'],
                                    env={**os.environ, 'RUNNER_TEMP': str(root)}, capture_output=True, text=True)
            self.assert_silent(result, 143)
            self.assertEqual(list(root.iterdir()), [])

    def test_raw_control_proves_canary_would_leak_without_wrapper(self):
        result = subprocess.run([sys.executable, '-c', 'import sys; print(sys.argv[1]); print(sys.argv[1],file=sys.stderr)', INJECTION],
                                capture_output=True, text=True)
        self.assertIn(INJECTION, result.stdout)
        self.assertIn(INJECTION, result.stderr)


@unittest.skipUnless(shutil.which('node'), 'Node required for action boundary')
class ConfidentialActionTests(ConfidentialAssertions, unittest.TestCase):
    def test_full_action_input_env_runtime_file_outputs_and_cleanup(self):
        for status in (0, 1, 42, 101):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); capture = root / 'capture'; capture.mkdir()
                entry = root / '_release-tauri/dist/index.js'; entry.parent.mkdir(parents=True)
                entry.write_text('''const fs=require('node:fs'), cp=require('node:child_process');
const dir=fs.readdirSync(process.env.RUNNER_TEMP)[0];
if ((fs.statSync(process.env.RUNNER_TEMP+'/'+dir).mode & 0o777)!==0o700) process.exit(99);
fs.writeSync(1,process.env.CANARY); fs.writeSync(2,process.env.CANARY);
cp.spawnSync(process.execPath,['-e','process.stdout.write(process.env.CANARY);process.stderr.write(process.env.CANARY)'],{stdio:'inherit'});
fs.appendFileSync(process.env.GITHUB_OUTPUT,'releaseId='+process.env.INPUT_RELEASEID+'\\n');
fs.appendFileSync(process.env.GITHUB_ENV,'FORWARDED=yes\\n');
fs.writeFileSync(process.env.RESULT,JSON.stringify({args:process.env.INPUT_ARGS,token:process.env.GITHUB_TOKEN,cwd:process.cwd(),node:process.execPath}));
process.exit(Number(process.env.STATUS));
''')
                env = {**os.environ, 'RUNNER_TEMP': str(capture), 'GITHUB_WORKSPACE': str(root),
                       'CANARY': INJECTION, 'STATUS': str(status), 'INPUT_RELEASEID': '42',
                       'INPUT_ARGS': '--target aarch64-apple-darwin --features local-whisper',
                       'GITHUB_TOKEN': 'synthetic-only', 'GITHUB_OUTPUT': str(root / 'output'),
                       'GITHUB_ENV': str(root / 'env'), 'RESULT': str(root / 'result')}
                result = subprocess.run(['node', str(ACTION)], env=env, cwd=root, capture_output=True, text=True)
                self.assert_silent(result, status)
                self.assertEqual(list(capture.iterdir()), [])
                self.assertEqual((root / 'output').read_text(), 'releaseId=42\n')
                self.assertEqual((root / 'env').read_text(), 'FORWARDED=yes\n')
                forwarded = json.loads((root / 'result').read_text())
                self.assertEqual(forwarded['args'], env['INPUT_ARGS'])
                self.assertEqual(forwarded['token'], 'synthetic-only')
                self.assertEqual(Path(forwarded['cwd']).resolve(), root.resolve())
                self.assertEqual(Path(forwarded['node']).resolve(), Path(shutil.which('node')).resolve())

    def test_action_missing_entry_and_temp_fail_closed_without_exception_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for capture in (root, root / CANARY):
                result = subprocess.run(['node', str(ACTION)], env={**os.environ, 'RUNNER_TEMP': str(capture),
                                        'GITHUB_WORKSPACE': str(root / CANARY)}, capture_output=True, text=True)
                self.assert_silent(result, 1)
                self.assertEqual(list(root.iterdir()), [])


class ConfidentialWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.texts = {name: (WORKFLOWS / (name + '.yml')).read_text() for name in ('build', 'pr-check', 'promote-stable')}

    def test_contract_and_every_individual_unwrapped_boundary_mutation(self):
        expected = confidential_contract(self.texts)
        for name, commands in expected.items():
            for command, count in commands.items():
                for occurrence in range(count):
                    with self.subTest(workflow=name, command=command, occurrence=occurrence):
                        parts = self.texts[name].split(command)
                        mutated = command.join(parts[:occurrence+1]) + 'UNWRAPPED' + command.join(parts[occurrence+1:])
                        with self.assertRaises(AssertionError):
                            confidential_contract({**self.texts, name: mutated})

    def test_trusted_path_guards_reject_directories_and_dangling_symlinks(self):
        guard = run_block(self.texts['build'], '- name: Reserve trusted CI paths')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(subprocess.run(['bash', '-c', guard], cwd=root, capture_output=True).returncode, 0)
            for name in ('_release-ci', '_release-tauri'):
                path = root / name
                path.symlink_to(root / 'missing')
                self.assertNotEqual(subprocess.run(['bash', '-c', guard], cwd=root, capture_output=True).returncode, 0)
                path.unlink(); path.mkdir()
                self.assertNotEqual(subprocess.run(['bash', '-c', guard], cwd=root, capture_output=True).returncode, 0)
                path.rmdir()

    @unittest.skipUnless(shutil.which('node'), 'Node required for version validation')
    def test_actual_private_version_validators_keep_gates_and_hide_parse_errors(self):
        build = run_block(self.texts['build'], '- name: Verify release label matches every app version')
        promote = run_block(self.texts['promote-stable'], '- name: Checkout exact private source and assert version')
        promote = promote[promote.index('bash scripts/run-confidential.sh python3'):]
        for kind, program in (('build', build), ('promote', promote)):
            for mutation in ('valid', 'json', 'version', 'cargo'):
                with self.subTest(kind=kind, mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary); capture = root / 'capture'; capture.mkdir()
                    (root / '_release-ci').symlink_to(ROOT, target_is_directory=True)
                    (root / 'scripts').symlink_to(ROOT / 'scripts', target_is_directory=True)
                    source = root if kind == 'build' else root / '_source'
                    (source / 'src-tauri').mkdir(parents=True)
                    package = {'version': '1.2.3'}
                    (source / 'package.json').write_text(json.dumps(package))
                    (source / 'package-lock.json').write_text(json.dumps({**package, 'packages': {'': package}}))
                    (source / 'src-tauri/tauri.conf.json').write_text(json.dumps(package))
                    (source / 'src-tauri/Cargo.toml').write_text('[package]\nversion="1.2.3"\n')
                    fake = root / 'cargo'
                    fake.write_text('#!/bin/bash\nif [ "$MUTATION" = cargo ]; then echo "$CANARY" >&2; exit 101; fi\n' +
                                    'printf \'%s\\n\' \'{"packages":[{"name":"echo","version":"1.2.3"}]}\'\n')
                    fake.chmod(0o755)
                    if mutation == 'json':
                        (source / 'package.json').write_text('{"' + CANARY + '": invalid}')
                    elif mutation == 'version':
                        (source / 'package.json').write_text(json.dumps({'version': INJECTION}))
                    elif mutation == 'cargo':
                        (source / 'src-tauri/Cargo.toml').write_text('[package]\n' + CANARY + ' invalid')
                    result = subprocess.run(['bash', '-e', '-c', program], cwd=root,
                                            env={**os.environ, 'RUNNER_TEMP': str(capture), 'TAG': 'v1.2.3',
                                                 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                                                 'CANARY': INJECTION, 'MUTATION': mutation},
                                            capture_output=True, text=True)
                    if mutation == 'valid':
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn(CANARY, result.stdout + result.stderr)
                    self.assertEqual(result.stderr, '')
                    self.assertEqual(list(capture.iterdir()), [])

    def test_action_defaults_runtime_and_release_contract_filters(self):
        metadata = (ACTION.parent / 'action.yml').read_text()
        defaults = dict(re.findall(r"(?m)^  (\w+):\n    description:.*\n    default: '([^']*)'", metadata))
        self.assertEqual(defaults, dict(releaseDraft='false', prerelease='false', projectPath='.', includeDebug='false',
                                      includeRelease='true', includeUpdaterJson='true', updaterJsonKeepUniversal='false',
                                      isGitea='false', uploadPlainBinary='false', generateReleaseNotes='false'))
        self.assertIn("using: 'node24'", metadata)
        self.assertIn("main: 'index.cjs'", metadata)
        contract = (WORKFLOWS / 'release-contract.yml').read_text()
        for path in ('tests/test_confidential_output.py', 'scripts/run-confidential.sh', '.github/actions/confidential-tauri/**'):
            self.assertEqual(contract.count('"' + path + '"'), 2)

    def test_hosted_proof_is_public_only_and_covers_every_build_host(self):
        contract = (WORKFLOWS / 'release-contract.yml').read_text()
        hosted = contract.split('  confidential-hosted:\n', 1)[1]
        build_hosts = set(re.findall(r'          - platform: (\S+)', self.texts['build']))
        proof_hosts = set(re.search(r'platform: \[([^]]+)\]', hosted)[1].split(', '))
        self.assertEqual(proof_hosts, build_hosts)
        self.assertEqual(hosted.count('uses: ./.github/actions/confidential-tauri'), 2)
        self.assertIn('steps.public_failure.outcome', hosted)
        self.assertIn('steps.public_failure.outputs.releaseId', hosted)
        self.assertIn('if: always()', hosted)
        self.assertIn(f'ref: {PIN}', hosted)
        self.assertEqual(hosted.count('persist-credentials: false'), 2)
        self.assertEqual(contract.count('"tests/fixtures/confidential-hosted/**"'), 2)
        for forbidden in ('secrets.', 'contents: write', 'upload-artifact', 'workflow_dispatch:',
                          'repository_dispatch:', 'workflow_run:', 'gh release', 'ECHO_TAURI_DEPLOY_KEY'):
            self.assertNotIn(forbidden, contract)
        uses = re.findall(r'uses: (.+)', hosted)
        self.assertEqual(set(uses), {'./.github/actions/confidential-tauri',
                                    'actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4.3.1'})

    @unittest.skipUnless(shutil.which('node'), 'Node required for portable hosted fixture')
    def test_portable_hosted_probe_locally(self):
        result = subprocess.run(['node', str(ROOT / 'tests/fixtures/confidential-hosted/probe.cjs'), 'local'],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Unwrapped 37 control:', result.stdout)
        self.assertIn('cleanup passed.', result.stdout)

    def test_clippy_fallback_stays_advisory_but_second_failure_blocks(self):
        block = run_block(self.texts['pr-check'], '- name: clippy (advisory)')
        for fallback_status in (0, 101):
            with self.subTest(status=fallback_status), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); capture = root / 'capture'; capture.mkdir()
                (root / '_release-ci').symlink_to(ROOT, target_is_directory=True)
                fake = root / 'cargo'
                fake.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\necho "$CANARY"\necho "$CANARY" >&2\ncase "$*" in *warnings*) exit 101;; *) exit "$FALLBACK";; esac\n')
                fake.chmod(0o755)
                result = subprocess.run(['bash', '-e', '-c', block], cwd=root, env={**os.environ,
                                        'PATH': str(root) + os.pathsep + os.environ['PATH'], 'GITHUB_WORKSPACE': str(root),
                                        'RUNNER_TEMP': str(capture), 'CALLS': str(root / 'calls'), 'CANARY': INJECTION,
                                        'FALLBACK': str(fallback_status)}, capture_output=True, text=True)
                self.assertEqual(result.returncode, fallback_status)
                self.assertNotIn(CANARY, result.stdout + result.stderr)
                self.assertEqual((root / 'calls').read_text().splitlines(),
                                 ['clippy --locked --message-format short -- -D warnings', 'clippy --locked --message-format short'])
                self.assertEqual(list(capture.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
