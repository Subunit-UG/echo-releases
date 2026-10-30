#!/usr/bin/env python3
"""Seal a held stable draft; independently recheck before same-byte promotion.

The receipt hash is a manual approval input, never inferred from mutable metadata.
Only the publish subcommand mutates an existing release; it never uploads assets.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time

MARKER = 'ECHO_HELD_STABLE_V1'
RECEIPT = 'held-stable-receipt.json'
SHA = re.compile(r'[0-9a-f]{40}')
HASH = re.compile(r'[0-9a-f]{64}')
TAG = re.compile(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)')
# This repository moved on 2026-10-30 from the user account subunit-ai to the
# organization Subunit-UG under the same name; both names address one repository.
# Exactly these two owners, ASCII only (case-insensitive like GitHub), nothing else.
OWN_REPO = re.compile(r'(?:subunit-ai|subunit-ug)/echo-releases', re.ASCII | re.IGNORECASE)


def require(value, message):
    if not value:
        raise ValueError(message)


def own_repo(value):
    return type(value) is str and OWN_REPO.fullmatch(value) is not None


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def api(repo, route, *extra, timeout=120):
    result = subprocess.run(['gh', 'api', f'repos/{repo}/{route}', *extra],
                            capture_output=True, check=True, timeout=timeout)
    return json.loads(result.stdout)


def download(repo, asset, path):
    require(type(asset.get('id')) is int and asset['id'] > 0, 'invalid asset ID')
    require(type(asset.get('size')) is int and 0 < asset['size'] <= 2 * 1024**3,
            'invalid/oversize asset')
    with path.open('xb') as output:
        subprocess.run(['gh', 'api', '-H', 'Accept: application/octet-stream',
                        f'repos/{repo}/releases/assets/{asset["id"]}'],
                       stdout=output, stderr=subprocess.PIPE, check=True, timeout=600)
    require(path.stat().st_size == asset['size'], 'download size mismatch')


def inventory(release):
    assets = release['assets']
    require(type(assets) is list and len(assets) <= 32, 'invalid/oversize asset inventory')
    result = {}
    for asset in assets:
        name = asset['name']
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,180}', name), 'unsafe asset name')
        require(name not in result, 'duplicate asset name')
        require(asset.get('state') == 'uploaded', 'unfinished asset upload')
        require(type(asset.get('id')) is int and asset['id'] > 0, 'invalid asset ID')
        require(type(asset.get('size')) is int and 0 < asset['size'] <= 2 * 1024**3,
                'invalid asset size')
        result[name] = {'id': asset['id'], 'size': asset['size']}
    require(len({item['id'] for item in result.values()}) == len(result), 'duplicate asset ID')
    require(sum(item['size'] for item in result.values()) <= 8 * 1024**3, 'asset budget exceeded')
    return result


def canonical(repo, release_id, tag):
    result = subprocess.run(['gh', 'api', '--paginate', '--slurp',
                            f'repos/{repo}/releases?per_page=100'],
                            capture_output=True, check=True, timeout=120)
    matches = [release for page in json.loads(result.stdout) for release in page if release.get('tag_name') == tag]
    require(len(matches) == 1 and matches[0].get('id') == release_id, 'ambiguous canonical release')


def boundary(release, release_id, tag):
    require(release.get('id') == release_id and release.get('tag_name') == tag,
            'release identity changed')
    require(release.get('draft') is True and release.get('prerelease') is False,
            'release escaped stable draft boundary')
    require(MARKER in (release.get('body') or ''), 'release lacks held marker')


def validate_receipt(receipt, release, source, tag, release_id):
    require(type(receipt) is dict and type(receipt.get('schema')) is int and receipt.get('schema') == 1, 'invalid receipt schema')
    require(receipt.get('source_commit') == source and SHA.fullmatch(source), 'source mismatch')
    require(receipt.get('tag') == tag and TAG.fullmatch(tag), 'tag mismatch')
    require(receipt.get('release_id') == release_id, 'receipt release mismatch')
    boundary(release, release_id, tag)
    assets = inventory(release)
    receipt_assets = receipt.get('assets')
    required = set(names(tag) + [name + '.sig' for name in names(tag)] + ['latest.json', f'echo_{tag[1:]}_aarch64.dmg'])
    require(type(receipt_assets) is dict and required <= set(receipt_assets), 'incomplete artifact set')
    require(type(receipt_assets) is dict and set(assets) == set(receipt_assets) | {RECEIPT},
            'asset inventory changed')
    for name, expected in receipt_assets.items():
        require(type(expected) is dict and type(expected.get('sha256')) is str and HASH.fullmatch(expected['sha256']),
                'invalid receipt asset hash')
        require(assets[name] == {key: expected.get(key) for key in ('id', 'size')},
                'asset ID or size changed')
    require(type(receipt.get('requires_platform_trust')) is bool and receipt['requires_platform_trust'] == (not tag.startswith('v0.')),
            'platform policy mismatch')
    return assets


def validate_run(receipt, run, jobs, repo):
    # A draft sealed before the org move records the old owner; same repository.
    require(own_repo(receipt.get('repo')) and own_repo(repo), 'repository mismatch')
    require(type(receipt.get('run_id')) is int and receipt['run_id'] > 0, 'invalid build run')
    require(type(receipt.get('run_attempt')) is int and receipt['run_attempt'] > 0, 'invalid build attempt')
    require(run.get('id') == receipt['run_id'] and run.get('run_attempt') == receipt['run_attempt'],
            'build attempt mismatch')
    require(run.get('path') == '.github/workflows/build.yml' and run.get('event') == 'workflow_dispatch',
            'not canonical manual build')
    require(run.get('status') == 'completed' and run.get('conclusion') == 'success', 'build not successful')
    # A receipt binds the workflow revision; later edits cannot repin earlier evidence.
    require(run.get('head_sha') == receipt.get('workflow_commit') and
            SHA.fullmatch(receipt.get('workflow_commit', '')), 'workflow revision mismatch')
    names = [job['name'] for job in jobs]
    require(len(names) == len(set(names)), 'ambiguous build jobs')
    for platform in ('ubuntu-24.04', 'windows-latest', 'windows-11-arm', 'macos-14'):
        matches = [job for job in jobs if platform in job['name'] and job['name'].startswith('build (')]
        require(len(matches) == 1 and matches[0].get('conclusion') == 'success', 'incomplete platform matrix')
    for name in ('verify_updater_trust', 'platform_trust', 'publish'):
        matches = [job for job in jobs if job['name'] == name]
        require(len(matches) == 1 and matches[0].get('conclusion') == 'success', 'missing successful trust/seal job')
    if receipt['requires_platform_trust']:
        for name in ('verify_macos_platform_trust', 'verify_windows_platform_trust'):
            matches = [job for job in jobs if job['name'] == name]
            require(len(matches) == 1 and matches[0].get('conclusion') == 'success', 'native trust missing')


def names(tag):
    ver = tag[1:]
    return ['echo_aarch64.app.tar.gz', f'echo_{ver}_amd64.AppImage',
            f'echo_{ver}_x64-setup.exe', f'echo_{ver}_arm64-setup.exe']


def check_manifest(manifest, tag, repo, directory):
    require(own_repo(repo), 'invalid manifest repository')
    expected = {}
    artifacts = names(tag)
    keys = [('darwin-aarch64', 'darwin-aarch64-app'), ('linux-x86_64', 'linux-x86_64-appimage'),
            ('windows-x86_64', 'windows-x86_64-nsis'), ('windows-aarch64', 'windows-aarch64-nsis')]
    for name, aliases in zip(artifacts, keys):
        item = {'url': f'https://github.com/{repo}/releases/download/{tag}/{name}',
                'signature': (directory / (name + '.sig')).read_text().strip()}
        for key in aliases:
            expected[key] = item
    require(manifest.get('version') == tag[1:] and manifest.get('platforms') == expected,
            'final manifest does not bind exact updater bytes')


def seal(args):
    require(SHA.fullmatch(args.source_commit) and TAG.fullmatch(args.tag), 'invalid source/tag')
    canonical(args.repo, args.release_id, args.tag)
    release = api(args.repo, f'releases/{args.release_id}')
    boundary(release, args.release_id, args.tag)
    assets = inventory(release)
    require(RECEIPT not in assets, 'sealed release cannot be resealed')
    evidence = json.loads(args.updater_evidence)
    for key, name in zip(('mac', 'linux', 'windows_x64', 'windows_arm64'), names(args.tag)):
        for suffix, idkey, hashkey in (('', 'id', 'sha256'), ('.sig', 'sig_id', 'sig_sha256')):
            expected = evidence[key]
            require(assets.get(name + suffix, {}).get('id') == expected[idkey], 'unverified updater ID')
            require(digest(args.directory / (name + suffix)) == expected[hashkey], 'unverified updater hash')
    require('latest.json' in assets, 'manifest missing')
    for name, asset in assets.items():
        target = args.directory / name
        if not target.exists():
            download(args.repo, asset, target)
        require(target.stat().st_size == asset['size'], 'asset size changed at seal')
        asset['sha256'] = digest(target)
    receipt = {'schema': 1, 'repo': args.repo, 'release_id': args.release_id,
               'tag': args.tag, 'source_commit': args.source_commit,
               'run_id': args.run_id, 'run_attempt': args.run_attempt,
               'workflow_commit': __import__('os').environ['GITHUB_SHA'],
               'requires_platform_trust': not args.tag.startswith('v0.'),
               'public_key_sha256': evidence['public_key_sha256'], 'assets': assets}
    check_manifest(json.loads((args.directory / 'latest.json').read_text()), args.tag, args.repo, args.directory)
    path = args.directory / RECEIPT
    path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    subprocess.run([sys.executable, 'scripts/upload-release-asset.py', args.repo,
                    str(args.release_id), RECEIPT, str(path)], check=True, stdout=subprocess.PIPE, timeout=120)
    final = api(args.repo, f'releases/{args.release_id}')
    validate_receipt(receipt, final, args.source_commit, args.tag, args.release_id)
    downloaded = args.directory / 'receipt-readback.json'
    download(args.repo, inventory(final)[RECEIPT], downloaded)
    require(digest(downloaded) == digest(path), 'receipt changed during seal')
    print(f'held stable release={args.release_id} source={args.source_commit} receipt_sha256={digest(path)}')


def verify(args):
    require(SHA.fullmatch(args.source_commit) and TAG.fullmatch(args.tag) and HASH.fullmatch(args.receipt_sha256),
            'invalid manual promotion pins')
    require(not args.directory.exists(), 'verification directory must be fresh')
    args.directory.mkdir(parents=True)
    canonical(args.repo, args.release_id, args.tag)
    release = api(args.repo, f'releases/{args.release_id}')
    boundary(release, args.release_id, args.tag)
    assets = inventory(release)
    require(RECEIPT in assets and assets[RECEIPT]['size'] <= 1024**2, 'receipt missing/oversize')
    path = args.directory / RECEIPT
    download(args.repo, assets[RECEIPT], path)
    require(digest(path) == args.receipt_sha256, 'manual receipt hash mismatch')
    receipt = json.loads(path.read_text())
    validate_receipt(receipt, release, args.source_commit, args.tag, args.release_id)
    run = api(args.repo, f'actions/runs/{receipt["run_id"]}')
    # Per-attempt endpoint, all pages; never silently accept a truncated matrix.
    result = subprocess.run(['gh', 'api', '--paginate', '--slurp',
        f'repos/{args.repo}/actions/runs/{receipt["run_id"]}/attempts/{receipt["run_attempt"]}/jobs?per_page=100'],
        capture_output=True, check=True, timeout=120)
    jobs = [job for page in json.loads(result.stdout) for job in page['jobs']]
    validate_run(receipt, run, jobs, args.repo)
    for name, expected in receipt['assets'].items():
        download(args.repo, assets[name], args.directory / name)
        require(digest(args.directory / name) == expected['sha256'], 'asset bytes changed')
    # The build wrote its own repository name into every URL, and the seal recorded
    # that same name. Bind URLs to the receipt, not to today's name, so a draft built
    # before the org move keeps its exact old-owner URLs (GitHub redirects them).
    check_manifest(json.loads((args.directory / 'latest.json').read_text()), args.tag, receipt['repo'], args.directory)
    spec = importlib.util.spec_from_file_location('updater', Path(__file__).with_name('verify-updater-signatures.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    pairs = [(('echo.app.tar.gz' if i == 0 else name), str(args.directory / name),
              str(args.directory / (name + '.sig'))) for i, name in enumerate(names(args.tag))]
    evidence = module.verify(argparse.Namespace(config=args.config, pair=pairs,
                                               evidence_dir=args.directory / 'minisign'))
    require(evidence['public_key_sha256'] == receipt['public_key_sha256'], 'source updater key mismatch')
    # Compare final inventory again after long downloads/crypto checks.
    final = api(args.repo, f'releases/{args.release_id}')
    validate_receipt(receipt, final, args.source_commit, args.tag, args.release_id)
    require(inventory(final)[RECEIPT] == assets[RECEIPT], 'receipt asset replaced')
    print('held stable receipt, build matrix, manifest, asset IDs/hashes and all four signatures verified')
    canonical(args.repo, args.release_id, args.tag)
    return receipt, assets



def verify_latest(args, receipt, verified_assets):
    # GitHub's Latest route may lag the numeric release PATCH. Read-only retry;
    # a timeout/mismatch never triggers another write or reports success.
    for attempt in range(5):
        try:
            latest = api(args.repo, 'releases/latest', timeout=20)
            require(latest.get('id') == args.release_id and latest.get('tag_name') == args.tag and
                    latest.get('draft') is False and latest.get('prerelease') is False,
                    'Latest identity/channel mismatch')
            require(inventory(latest) == verified_assets, 'Latest asset inventory changed')
            validate_receipt(receipt, dict(latest, draft=True), args.source_commit, args.tag, args.release_id)
            return
        except (ValueError, subprocess.SubprocessError):
            if attempt < 4:
                time.sleep(2)
    raise ValueError('Latest publication readback did not converge to the approved release')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('seal', 'verify', 'publish'))
    parser.add_argument('--repo', required=True)
    parser.add_argument('--release-id', type=int, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--run-id', type=int)
    parser.add_argument('--run-attempt', type=int)
    parser.add_argument('--updater-evidence')
    parser.add_argument('--receipt-sha256')
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    require(own_repo(args.repo) and args.release_id > 0, 'invalid repository/release')
    if args.action == 'seal':
        seal(args)
    else:
        receipt, verified_assets = verify(args)
        if args.action == 'publish':
            if receipt['requires_platform_trust']:
                import os
                native_names = ['echo_aarch64.app.tar.gz', f'echo_{args.tag[1:]}_aarch64.dmg',
                                f'echo_{args.tag[1:]}_x64-setup.exe', f'echo_{args.tag[1:]}_arm64-setup.exe']
                for name, variable in zip(native_names, ('NATIVE_MAC_APP', 'NATIVE_MAC_DMG', 'NATIVE_WIN64', 'NATIVE_WINARM')):
                    require(os.environ.get(variable) == receipt['assets'][name]['sha256'],
                            'native trust hash missing/mismatched')
            result = api(args.repo, f'releases/{args.release_id}', '-X', 'PATCH',
                         '-f', f'tag_name={args.tag}', '-F', 'draft=false', '-F', 'prerelease=false', '-f', 'make_latest=true')
            require(result.get('id') == args.release_id and result.get('tag_name') == args.tag and
                    result.get('draft') is False and result.get('prerelease') is False,
                    'publication readback failed')
            require(inventory(result) == verified_assets == inventory(api(args.repo, f'releases/{args.release_id}')),
                    'publication asset readback changed')
            # Receipt assets and receipt identity remain identical; no asset mutation occurs here.
            published = dict(result, draft=True)
            validate_receipt(receipt, published, args.source_commit, args.tag, args.release_id)
            verify_latest(args, receipt, verified_assets)
            print('published same verified stable bytes as GitHub Latest; no rebuild/upload')
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'held stable refused: {error}', file=sys.stderr)
        raise SystemExit(1)
