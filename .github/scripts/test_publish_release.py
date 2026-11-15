import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('publisher', Path(__file__).with_name('publish_release.py'))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
SHA = 'a' * 40
REPO = 'worthdone/tdesktop-without-widgets'
ROOT = f'repos/{REPO}'
TAG = f'v7.4.4-without-widgets.{SHA[:12]}'

class PublisherTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        event_path = Path(self.directory.name) / 'event.json'
        event_path.write_text(json.dumps({'repository': {'default_branch': 'dev'}, 'workflow_run': {'head_sha': SHA}}))
        self.env = patch.dict(os.environ, {'GITHUB_REPOSITORY': REPO, 'GITHUB_EVENT_PATH': str(event_path), 'GITHUB_EVENT_NAME': 'workflow_run', 'GITHUB_STEP_SUMMARY': str(Path(self.directory.name) / 'summary'), 'SOURCE_SHA': '', 'RELEASE_TAG': '', 'PRERELEASE': 'true'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.calls = []
        self.runs = {}
        for index, platform in enumerate(publisher.PACKAGES):
            self.runs[platform] = {'id': index + 1, 'head_sha': SHA, 'head_branch': 'dev', 'head_repository': {'full_name': REPO}, 'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success', 'path': f'.github/workflows/release-{platform}.yml', 'html_url': f'https://github.com/{REPO}/actions/runs/{index + 1}'}
        event_path.write_text(json.dumps({'repository': {'default_branch': 'dev'}, 'workflow_run': self.runs['linux']}))
        self.metadata = {}
        self.release = None
        self.assets = []
        self.bad_digest = False
        self.expired = False
        self.extra_file = False
        self.api_patch = patch.object(publisher, 'api', side_effect=self.api)
        self.gh_patch = patch.object(publisher, 'gh', side_effect=self.gh)
        self.api_patch.start()
        self.gh_patch.start()
        self.addCleanup(self.api_patch.stop)
        self.addCleanup(self.gh_patch.stop)

    def api(self, path):
        if '/actions/workflows/' in path:
            platform = path.split('release-')[1].split('.yml')[0]
            return {'workflow_runs': [self.runs[platform]]}
        if '/contents/Telegram/build/version?' in path:
            return {'content': base64.b64encode(b'AppVersionStr      7.4.4\n').decode()}
        if path.startswith(f'{ROOT}/releases?'):
            return [self.release] if self.release else []
        if '/git/matching-refs/' in path:
            return []
        if '/artifacts?' in path:
            run_id = int(path.split('/runs/')[1].split('/')[0])
            platform = list(publisher.PACKAGES)[run_id - 1]
            result = [{'name': f'release-{platform}', 'expired': self.expired}]
            if run_id in self.metadata:
                result.append({'name': f'release-{platform}-source', 'expired': False})
            return {'artifacts': result}
        if '/releases/tags/' in path:
            return self.release
        if path.endswith('/assets'):
            return self.assets
        raise AssertionError(path)

    def gh(self, *args):
        self.calls.append(args)
        if args[:2] == ('run', 'download'):
            name = args[args.index('--name') + 1]
            platform = name.removeprefix('release-').removesuffix('-source')
            directory = Path(args[args.index('--dir') + 1])
            directory.mkdir(exist_ok=True)
            if name.endswith('-source'):
                (directory / 'source.json').write_text(json.dumps({'sha': self.metadata[int(args[2])]}))
                return ''
            (directory / publisher.PACKAGES[platform]).write_bytes(platform.encode())
            if self.extra_file:
                (directory / 'unexpected.txt').write_text('bad')
        elif args[:2] == ('release', 'create'):
            self.release = {'id': 10, 'tag_name': TAG, 'target_commitish': SHA, 'draft': True}
        elif args[:2] == ('release', 'upload'):
            if self.release['tag_name'] != args[2]:
                raise RuntimeError('Draft tag was lost during an update')
            for arg in args:
                path = Path(arg)
                if path.is_file():
                    self.assets.append({'name': path.name, 'size': path.stat().st_size, 'digest': 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()})
            if self.bad_digest:
                self.assets[0]['digest'] = 'sha256:wrong'
        elif args[:3] == ('api', '--method', 'PATCH'):
            tag_fields = [arg for arg in args if arg.startswith('tag_name=')]
            self.release['tag_name'] = tag_fields[0].split('=', 1)[1] if tag_fields else 'untagged-draft'
            if 'draft=false' in args:
                self.release.update(draft=False, immutable=True, html_url='https://example.test/release')
            return json.dumps(self.release)
        else:
            raise AssertionError(args)
        return ''

    def published(self):
        return any(call[:3] == ('api', '--method', 'PATCH') and 'draft=false' in call for call in self.calls)

    def test_all_platforms_publish_draft_only_after_digest_verification(self):
        publisher.main()
        self.assertTrue(self.published())
        create = next(call for call in self.calls if call[:2] == ('release', 'create'))
        self.assertIn('--draft', create)
        self.assertIn('--prerelease', create)
        self.assertEqual(create[create.index('--target') + 1], SHA)
        self.assertEqual(len(self.assets), 4)

    def test_mismatched_or_untrusted_runs_wait_without_mutations(self):
        for key, value in [('head_sha', 'b' * 40), ('head_branch', 'other'), ('event', 'pull_request'), ('conclusion', 'failure'), ('path', '.github/workflows/win.yml'), ('head_repository', {'full_name': 'other/fork'})]:
            with self.subTest(key=key):
                original = copy.deepcopy(self.runs['windows'])
                self.runs['windows'][key] = value
                publisher.main()
                self.assertEqual(self.calls, [])
                self.runs['windows'] = original

    def test_bad_upload_stays_draft(self):
        self.bad_digest = True
        with self.assertRaisesRegex(RuntimeError, 'verification failed'):
            publisher.main()
        self.assertFalse(self.published())
        self.assertTrue(self.release['draft'])

    def test_expired_artifact_is_rejected(self):
        self.expired = True
        with self.assertRaisesRegex(RuntimeError, 'artifact'):
            publisher.main()
        self.assertIsNone(self.release)

    def test_extra_artifact_file_is_rejected(self):
        self.extra_file = True
        with self.assertRaisesRegex(RuntimeError, 'contents'):
            publisher.main()
        self.assertIsNone(self.release)

    def test_published_immutable_release_is_idempotent(self):
        self.release = {'tag_name': TAG, 'target_commitish': SHA, 'draft': False, 'immutable': True, 'html_url': 'https://example.test/release'}
        publisher.main()
        self.assertEqual(self.calls, [])

    def test_existing_draft_can_resume(self):
        self.release = {'id': 10, 'tag_name': TAG, 'target_commitish': SHA, 'draft': True}
        publisher.main()
        self.assertTrue(self.published())
        self.assertEqual(self.release['tag_name'], TAG)
        self.assertEqual(self.release['target_commitish'], SHA)
        self.assertFalse(any(call[:2] == ('release', 'create') for call in self.calls))

    def test_manual_missing_build_fails(self):
        os.environ['GITHUB_EVENT_NAME'] = 'workflow_dispatch'
        os.environ['SOURCE_SHA'] = SHA
        self.runs['windows']['head_sha'] = 'b' * 40
        with self.assertRaisesRegex(RuntimeError, 'Waiting for'):
            publisher.main()
        self.assertEqual(self.calls, [])

    def test_windows_retry_can_use_new_workflow_with_original_source(self):
        self.runs['windows']['head_sha'] = 'b' * 40
        self.metadata[2] = SHA
        publisher.main()
        self.assertTrue(self.published())
        self.assertEqual(self.release['target_commitish'], SHA)

    def test_windows_event_uses_build_source_instead_of_workflow_commit(self):
        self.runs['windows']['head_sha'] = 'b' * 40
        self.metadata[2] = SHA
        Path(os.environ['GITHUB_EVENT_PATH']).write_text(json.dumps({'repository': {'default_branch': 'dev'}, 'workflow_run': self.runs['windows']}))
        publisher.main()
        self.assertTrue(self.published())
        self.assertEqual(self.release['target_commitish'], SHA)

    def test_mismatched_metadata_cannot_join_other_packages(self):
        self.metadata[2] = 'c' * 40
        publisher.main()
        self.assertFalse(self.published())
        self.assertIsNone(self.release)

    def test_malformed_source_metadata_is_rejected(self):
        self.metadata[2] = 'invalid'
        with self.assertRaisesRegex(ValueError, 'source commit metadata'):
            publisher.main()
        self.assertFalse(self.published())

    def test_foreign_repository_event_cannot_read_or_publish_artifacts(self):
        trigger = copy.deepcopy(self.runs['windows'])
        trigger['head_repository']['full_name'] = 'other/fork'
        Path(os.environ['GITHUB_EVENT_PATH']).write_text(json.dumps({'repository': {'default_branch': 'dev'}, 'workflow_run': trigger}))
        publisher.main()
        self.assertEqual(self.calls, [])

if __name__ == '__main__':
    unittest.main()
