"""Behavioral mutations of independently approved stable draft evidence."""
from pathlib import Path
import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('held', ROOT / 'scripts/held-stable.py')
held = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(held)


class HeldStableTests(unittest.TestCase):
    def setUp(self):
        self.source = 'a' * 40; self.tag = 'v0.6.11'; self.repo = 'Subunit-UG/echo-releases'
        names = held.names(self.tag)
        names += [name + '.sig' for name in names]
        names += ['latest.json', 'echo_0.6.11_aarch64.dmg']
        self.assets = {name: {'id': i+1, 'size': 4, 'sha256': 'b'*64} for i, name in enumerate(names)}
        self.receipt = dict(schema=1,repo=self.repo,source_commit=self.source,tag=self.tag,release_id=20,
                            assets=self.assets,requires_platform_trust=False,run_id=55,run_attempt=1,
                            workflow_commit='c'*40)
        self.release = dict(id=20,tag_name=self.tag,draft=True,prerelease=False,body=held.MARKER,
                            assets=[dict(name=name,state='uploaded',id=a['id'],size=a['size']) for name,a in self.assets.items()]
                            + [dict(name=held.RECEIPT,state='uploaded',id=100,size=100)])
        self.run = dict(id=55,run_attempt=1,path='.github/workflows/build.yml',event='workflow_dispatch',
                        status='completed',conclusion='success',head_sha='c'*40)
        self.jobs = [dict(name=f'build ({platform}, --features local)',conclusion='success') for platform in
                     ('ubuntu-24.04','windows-latest','windows-11-arm','macos-14')]
        self.jobs += [dict(name=name,conclusion='success') for name in ('verify_updater_trust','platform_trust','publish')]

    def validate(self):
        return held.validate_receipt(self.receipt,self.release,self.source,self.tag,20)

    def test_valid_exact_inventory(self):
        self.validate()
        held.validate_run(self.receipt,self.run,self.jobs,self.repo)

    def test_release_boundary_mutations(self):
        for key,value in [('id',21),('tag_name','v0.6.12'),('draft',False),('prerelease',True),('body','')]:
            with self.subTest(key=key):
                original=self.release[key]; self.release[key]=value
                with self.assertRaises(ValueError): self.validate()
                self.release[key]=original

    def test_source_and_policy_mutations(self):
        for key,value in [('schema',2),('schema',True),('source_commit','d'*40),('tag','v0.6.12'),('release_id',21),('requires_platform_trust',True),('requires_platform_trust',0)]:
            with self.subTest(key=key):
                original=self.receipt[key]; self.receipt[key]=value
                with self.assertRaises(ValueError): self.validate()
                self.receipt[key]=original

    def test_asset_id_size_state_and_name_mutations(self):
        asset=self.release['assets'][0]
        for key,value in [('id',999),('size',9),('state','new'),('name','../escape')]:
            with self.subTest(key=key):
                original=asset[key]; asset[key]=value
                with self.assertRaises(ValueError): self.validate()
                asset[key]=original

    def test_duplicate_and_extra_assets(self):
        self.release['assets'].append(copy.deepcopy(self.release['assets'][0]))
        with self.assertRaises(ValueError): self.validate()
        self.release['assets'].pop()
        self.release['assets'].append(dict(name='extra.exe',state='uploaded',id=200,size=4))
        with self.assertRaises(ValueError): self.validate()

    def test_missing_installer_or_signature_refused(self):
        for name in ['echo_0.6.11_aarch64.dmg','echo_0.6.11_x64-setup.exe.sig','latest.json']:
            with self.subTest(name=name):
                original=self.receipt['assets'].pop(name)
                with self.assertRaises(ValueError): self.validate()
                self.receipt['assets'][name]=original

    def test_invalid_asset_hash_refused(self):
        for value in ('not-a-hash', None, 123, []):
            self.assets['latest.json']['sha256']=value
            with self.assertRaises(ValueError): self.validate()

    def test_failed_missing_or_duplicate_platforms(self):
        for action in ('fail','remove','duplicate'):
            with self.subTest(action=action):
                jobs=copy.deepcopy(self.jobs)
                if action=='fail': jobs[0]['conclusion']='failure'
                elif action=='remove': jobs.pop(0)
                else: jobs.append(copy.deepcopy(jobs[0]))
                with self.assertRaises(ValueError): held.validate_run(self.receipt,self.run,jobs,self.repo)

    def test_run_attempt_and_revision_and_event_pins(self):
        for key,value in [('run_attempt',2),('head_sha','d'*40),('conclusion','failure'),('event','schedule'),('path','.github/workflows/pr-check.yml')]:
            with self.subTest(key=key):
                run=dict(self.run,**{key:value})
                with self.assertRaises(ValueError): held.validate_run(self.receipt,run,self.jobs,self.repo)

    def test_native_policy_requires_both_jobs(self):
        receipt=dict(self.receipt,requires_platform_trust=True)
        with self.assertRaises(ValueError): held.validate_run(receipt,self.run,self.jobs,self.repo)
        jobs=self.jobs+[dict(name=name,conclusion='success') for name in ('verify_macos_platform_trust','verify_windows_platform_trust')]
        held.validate_run(receipt,self.run,jobs,self.repo)

    def test_manifest_binds_all_aliases_urls_signatures(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory); platforms={}
            aliases=[('darwin-aarch64','darwin-aarch64-app'),('linux-x86_64','linux-x86_64-appimage'),
                     ('windows-x86_64','windows-x86_64-nsis'),('windows-aarch64','windows-aarch64-nsis')]
            for name,keys in zip(held.names(self.tag),aliases):
                (directory/(name+'.sig')).write_text('actual-signature')
                for key in keys: platforms[key]=dict(url=f'https://github.com/{self.repo}/releases/download/{self.tag}/{name}',signature='actual-signature')
            manifest=dict(version='0.6.11',platforms=platforms)
            held.check_manifest(manifest,self.tag,self.repo,directory)
            for key in ('version','platforms'):
                bad=copy.deepcopy(manifest)
                if key=='version': bad[key]='0.6.12'
                else: bad[key]['windows-x86_64']['url']='https://evil.example/installer'
                with self.assertRaises(ValueError): held.check_manifest(bad,self.tag,self.repo,directory)

    def test_sha_manual_policy_rejects_automatic_and_tag_refs(self):
        for event,ref,tag in [('schedule',self.source,self.tag),('repository_dispatch',self.source,self.tag),
                              ('workflow_dispatch','v0.6.11',self.tag),('workflow_dispatch',self.source,'v0.6.11-rc.1'),
                              ('workflow_dispatch',self.source,self.source)]:
            result=subprocess.run(['bash',str(ROOT/'scripts/release_policy.sh'),event,ref,tag,'draft_stable'],capture_output=True)
            self.assertNotEqual(result.returncode,0)
        result=subprocess.run(['bash',str(ROOT/'scripts/release_policy.sh'),'workflow_dispatch',self.source,self.tag,'draft_stable'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0); self.assertIn('prerelease=false',result.stdout)

    def test_duplicate_canonical_release_refused(self):
        response=type('Response',(),{'stdout':json.dumps([[dict(id=20,tag_name=self.tag),dict(id=21,tag_name=self.tag)]]).encode()})()
        with patch.object(held.subprocess,'run',return_value=response):
            with self.assertRaises(ValueError): held.canonical(self.repo,20,self.tag)

    def test_promotion_workflow_never_builds_or_uploads(self):
        text=(ROOT/'.github/workflows/promote-stable.yml').read_text()
        self.assertIn('group: echo-release-build',text)
        self.assertNotIn('repository_dispatch:',text); self.assertNotIn('schedule:',text)
        for forbidden in ('tauri-action','npm ci','cargo build','upload-release-asset','git tag','git push'):
            self.assertNotIn(forbidden,text)
        self.assertIn('--receipt-sha256',text); self.assertIn('NATIVE_MAC_APP:',text)
        self.assertIn('needs.proof.result',text)

class PromotionWriteBoundary(unittest.TestCase):
    def test_failed_verification_cannot_patch_or_upload(self):
        argv=['held-stable.py','publish','--repo','Subunit-UG/echo-releases','--release-id','20',
              '--source-commit','a'*40,'--tag','v0.6.11','--directory','unused',
              '--receipt-sha256','b'*64,'--config','unused-config']
        with patch.object(held.sys,'argv',argv), patch.object(held,'verify',side_effect=ValueError('bad crypto')), patch.object(held,'api') as api:
            with self.assertRaises(ValueError): held.main()
            api.assert_not_called()

    def test_successful_promotion_performs_only_one_release_patch(self):
        case=HeldStableTests(); case.setUp()
        published=dict(case.release,draft=False)
        args=['held-stable.py','publish','--repo',case.repo,'--release-id','20',
              '--source-commit',case.source,'--tag',case.tag,'--directory','unused']
        calls=[]
        def api(repo, route, *extra, **kwargs):
            calls.append((route,extra)); return published
        with patch.object(held.sys,'argv',args), patch.object(held,'verify',return_value=(case.receipt,held.inventory(case.release))), patch.object(held,'api',side_effect=api):
            self.assertEqual(held.main(),0)
        self.assertEqual(len(calls),3)
        self.assertEqual(calls[0][0],'releases/20')
        self.assertIn('PATCH',calls[0][1]); self.assertIn('draft=false',calls[0][1])
        self.assertEqual(calls[1],('releases/20',()))
        self.assertEqual(calls[2],('releases/latest',()))
        self.assertFalse(any('/assets' in route for route,_ in calls))

    def test_old_or_wrong_tag_latest_never_repatches_or_reports_success(self):
        import contextlib, io
        case=HeldStableTests(); case.setUp(); published=dict(case.release,draft=False)
        args=['held-stable.py','publish','--repo',case.repo,'--release-id','20',
              '--source-commit',case.source,'--tag',case.tag,'--directory','unused']
        for latest in (dict(published,id=19),dict(published,tag_name='v0.6.10'),
                       dict(published,draft=True),dict(published,assets=[])):
            with self.subTest(latest=latest.get('tag_name')):
                calls=[]; output=io.StringIO()
                def api(repo, route, *extra, **kwargs):
                    calls.append((route,extra,kwargs))
                    return latest if route=='releases/latest' else published
                with patch.object(held.sys,'argv',args), patch.object(held,'verify',return_value=(case.receipt,held.inventory(case.release))), patch.object(held,'api',side_effect=api), patch.object(held.time,'sleep') as sleep, contextlib.redirect_stdout(output):
                    with self.assertRaisesRegex(ValueError,'did not converge'): held.main()
                self.assertEqual(sum('PATCH' in extra for _,extra,_ in calls),1)
                reads=[call for call in calls if call[0]=='releases/latest']
                self.assertEqual(len(reads),5); self.assertTrue(all(call[2]=={'timeout':20} for call in reads))
                self.assertEqual(sleep.call_count,4); self.assertEqual(output.getvalue(),'')
                self.assertFalse(any('/assets' in route for route,_,_ in calls))

    def test_latest_lag_converges_with_gets_only(self):
        import argparse
        case=HeldStableTests(); case.setUp(); published=dict(case.release,draft=False)
        args=argparse.Namespace(repo=case.repo,release_id=20,source_commit=case.source,tag=case.tag)
        with patch.object(held,'api',side_effect=[dict(published,id=19),published]) as api, patch.object(held.time,'sleep') as sleep:
            held.verify_latest(args,case.receipt,held.inventory(case.release))
            self.assertEqual(api.call_count,2); sleep.assert_called_once_with(2)
            for call in api.call_args_list:
                self.assertEqual(call.args,(case.repo,'releases/latest'))
                self.assertEqual(call.kwargs,{'timeout':20})

    def test_latest_connection_failure_is_bounded(self):
        import argparse
        case=HeldStableTests(); case.setUp()
        args=argparse.Namespace(repo=case.repo,release_id=20,source_commit=case.source,tag=case.tag)
        with patch.object(held,'api',side_effect=subprocess.TimeoutExpired('gh',20)) as api, patch.object(held.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'did not converge'):
                held.verify_latest(args,case.receipt,held.inventory(case.release))
            self.assertEqual(api.call_count,5)

    def test_v1_publish_requires_exact_native_hashes(self):
        case=HeldStableTests(); case.setUp(); case.receipt['requires_platform_trust']=True
        args=['held-stable.py','publish','--repo',case.repo,'--release-id','20',
              '--source-commit',case.source,'--tag',case.tag,'--directory','unused']
        with patch.object(held.sys,'argv',args), patch.object(held,'verify',return_value=(case.receipt,held.inventory(case.release))), patch.object(held,'api') as api, patch.dict('os.environ',{},clear=True):
            with self.assertRaises(ValueError): held.main()
            api.assert_not_called()


OLD, NEW = 'subunit-ai/echo-releases', 'Subunit-UG/echo-releases'
ALIASES = [('darwin-aarch64','darwin-aarch64-app'),('linux-x86_64','linux-x86_64-appimage'),
           ('windows-x86_64','windows-x86_64-nsis'),('windows-aarch64','windows-aarch64-nsis')]


class OrgMoveContract(unittest.TestCase):
    """2026-10-30: echo-releases moved from user subunit-ai to org Subunit-UG (one repository)."""

    @staticmethod
    def manifest(directory, url_repo, tag='v0.6.11'):
        platforms = {}
        for name, keys in zip(held.names(tag), ALIASES):
            (directory/(name+'.sig')).write_text('sig-'+name+'\n')
            for key in keys:
                platforms[key] = dict(url=f'https://github.com/{url_repo}/releases/download/{tag}/{name}', signature='sig-'+name)
        return dict(version=tag[1:], platforms=platforms)

    @staticmethod
    def argv(action, repo, case):
        return ['held-stable.py', action, '--repo', repo, '--release-id', '20', '--source-commit', case.source,
                '--tag', case.tag, '--directory', 'unused']

    def test_only_this_repository_under_old_user_or_new_org(self):
        for value in (OLD, NEW, 'subunit-ug/echo-releases', 'SUBUNIT-AI/Echo-Releases'):
            with self.subTest(value=value): self.assertTrue(held.own_repo(value))
        for value in ('evil/echo-releases', 'subunit-ai-evil/echo-releases', 'Subunit-UG-evil/echo-releases',
                      'Subunit-UG/echo-app', 'subunit-ai/echo-tauri', 'Subunit-UG/echo-releases-fork',
                      'Subunit-UG/echo-releases/', ' Subunit-UG/echo-releases', 'Subunit-UG/echo-releases\n',
                      'Subunit-UG', 'echo-releases', '', None, 42, 'Subunit-UG\\echo-releases',
                      'github.com/Subunit-UG/echo-releases', 'Subunit-UG/echo-releases/../evil',
                      # Unicode look-alikes that plain re.IGNORECASE would fold onto ASCII letters.
                      'ſubunit-ai/echo-releases', 'Subunit-UG/echo-releaſes', 'subunit‐ai/echo-releases'):
            with self.subTest(value=value): self.assertFalse(held.own_repo(value))

    def test_main_accepts_both_owners_and_refuses_foreign_owner_before_any_call(self):
        case = HeldStableTests(); case.setUp()
        for repo in (OLD, NEW, 'subunit-ug/ECHO-RELEASES'):
            with self.subTest(repo=repo), patch.object(held.sys,'argv',self.argv('verify',repo,case)), \
                    patch.object(held,'verify',return_value=(case.receipt,{})) as verify, patch.object(held,'api') as api:
                self.assertEqual(held.main(), 0)
                self.assertEqual(verify.call_args.args[0].repo, repo); api.assert_not_called()
        for repo in ('evil/echo-releases', 'Subunit-UG/echo-app', 'subunit-ai/echo-releases-fork', 'ſubunit-ai/echo-releases'):
            for action in ('seal', 'verify', 'publish'):
                with self.subTest(repo=repo, action=action), patch.object(held.sys,'argv',self.argv(action,repo,case)), \
                        patch.object(held,'seal') as seal, patch.object(held,'verify') as verify, patch.object(held,'api') as api:
                    with self.assertRaisesRegex(ValueError, 'invalid repository'): held.main()
                    seal.assert_not_called(); verify.assert_not_called(); api.assert_not_called()

    def test_receipt_owner_may_differ_only_between_old_user_and_new_org(self):
        case = HeldStableTests(); case.setUp()
        for sealed, current in ((OLD, NEW), (NEW, NEW), (OLD, OLD), (NEW, OLD)):
            with self.subTest(sealed=sealed, current=current):
                held.validate_run(dict(case.receipt, repo=sealed), case.run, case.jobs, current)
        for sealed, current in (('evil/echo-releases', NEW), (OLD, 'evil/echo-releases'), (None, NEW),
                                ('Subunit-UG/echo-app', NEW), (NEW, '')):
            with self.subTest(sealed=sealed, current=current), self.assertRaisesRegex(ValueError, 'repository mismatch'):
                held.validate_run(dict(case.receipt, repo=sealed), case.run, case.jobs, current)

    def test_manifest_urls_bind_to_the_exact_sealed_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for owner in (OLD, NEW):
                held.check_manifest(self.manifest(directory, owner), 'v0.6.11', owner, directory)
            for url_repo, bound in ((OLD, NEW), (NEW, OLD), ('evil/echo-releases', 'evil/echo-releases'),
                                    ('evil/echo-releases', NEW)):
                with self.subTest(url_repo=url_repo, bound=bound), self.assertRaises(ValueError):
                    held.check_manifest(self.manifest(directory, url_repo), 'v0.6.11', bound, directory)
            mixed = self.manifest(directory, NEW)
            mixed['platforms']['windows-x86_64']['url'] = mixed['platforms']['windows-x86_64']['url'].replace(NEW, OLD)
            with self.assertRaises(ValueError): held.check_manifest(mixed, 'v0.6.11', NEW, directory)

    def test_draft_sealed_before_move_verifies_after_move_end_to_end(self):
        import argparse, contextlib, hashlib, io, types
        tag, source = 'v0.6.11', 'a'*40
        case = HeldStableTests(); case.setUp()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); staging = root/'staging'; staging.mkdir()
            # Built and sealed under the old owner: receipt and every manifest URL say subunit-ai.
            manifest = self.manifest(staging, OLD, tag)
            files = {name+'.sig': (staging/(name+'.sig')).read_bytes() for name in held.names(tag)}
            files.update({name: b'artifact '+name.encode() for name in held.names(tag)})
            files['latest.json'] = json.dumps(manifest).encode(); files['echo_0.6.11_aarch64.dmg'] = b'dmg'
            ids = {name: index+1 for index, name in enumerate(sorted(files))}
            receipt = dict(schema=1, repo=OLD, release_id=20, tag=tag, source_commit=source, run_id=55, run_attempt=1,
                           workflow_commit='c'*40, requires_platform_trust=False, public_key_sha256='d'*64,
                           assets={name: dict(id=ids[name], size=len(data), sha256=hashlib.sha256(data).hexdigest())
                                   for name, data in files.items()})
            files[held.RECEIPT] = (json.dumps(receipt, sort_keys=True, indent=2)+'\n').encode(); ids[held.RECEIPT] = 100
            by_id = {ids[name]: data for name, data in files.items()}
            release = dict(id=20, tag_name=tag, draft=True, prerelease=False, body=held.MARKER,
                           assets=[dict(name=name, state='uploaded', id=ids[name], size=len(data)) for name, data in files.items()])
            calls = []
            def api(repo, route, *extra, **kwargs):
                calls.append(repo); return {'releases/20': release, 'actions/runs/55': case.run}[route]
            def download(repo, asset, path):
                calls.append(repo); path.write_bytes(by_id[asset['id']])
            def run(command, **kwargs):
                calls.append(command[-1].split('/')[1]+'/'+command[-1].split('/')[2])
                return types.SimpleNamespace(stdout=json.dumps([{'jobs': case.jobs}]).encode())
            updater = types.SimpleNamespace(verify=lambda namespace: {'public_key_sha256': 'd'*64})
            # Promoted after the move: github.repository is now the org name.
            args = argparse.Namespace(repo=NEW, release_id=20, source_commit=source, tag=tag, directory=root/'proof',
                                      receipt_sha256=hashlib.sha256(files[held.RECEIPT]).hexdigest(), config=root/'config')
            with patch.object(held,'canonical',side_effect=lambda repo,*rest: calls.append(repo)), \
                    patch.object(held,'api',side_effect=api), patch.object(held,'download',side_effect=download), \
                    patch.object(held.subprocess,'run',side_effect=run), \
                    patch.object(held.importlib.util,'spec_from_file_location'), \
                    patch.object(held.importlib.util,'module_from_spec',return_value=updater), \
                    contextlib.redirect_stdout(io.StringIO()):
                sealed, assets = held.verify(args)
            self.assertEqual(sealed['repo'], OLD)
            self.assertEqual(set(calls), {NEW})
            self.assertEqual(set(assets), set(files))

if __name__=='__main__': unittest.main()
