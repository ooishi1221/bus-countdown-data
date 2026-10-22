#!/usr/bin/env python3
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from publish_release import publish, REPOSITORY

CONTENT = 'c' * 64


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.assets = Path(self.temp.name) / 'assets'; self.assets.mkdir()
        self.verified = Path(self.temp.name) / 'verified.json'
        self.manifest = dict(feed_version='20261023_031145', source_sha256='a' * 64, content_sha256=CONTENT, fetched_at=1792700000.5,
                             calendar_dates_end='2026-12-16', feed_end='2029-10-21',
                             counts=dict(stops=5365, trips=54344, stop_times=1043046, options=14222))
        (self.assets / 'source-manifest.json').write_text(json.dumps(self.manifest))
        self.old = dict(tag_name='20261022_031145', draft=False, prerelease=False)

    def check_output(self, releases, old_manifest=None):
        answers = [json.dumps(releases).encode()] + ([json.dumps(old_manifest).encode()] if old_manifest is not None else [])
        return patch('publish_release.subprocess.check_output', side_effect=answers)

    def testWrongRepositoryMakesNoCall(self):
        with patch('publish_release.subprocess.check_output') as call:
            with self.assertRaises(ValueError): publish(self.assets, 'ooishi1221/bus-countdown-ios')
            call.assert_not_called()

    def testSameContentDifferentFeedVersionPublishesNothingButRecordsVerification(self):
        with self.check_output([self.old], dict(content_sha256=CONTENT)), patch('publish_release.subprocess.run') as run:
            self.assertEqual(publish(self.assets, REPOSITORY, self.verified), '20261022_031145')
            run.assert_not_called()
        verified = json.loads(self.verified.read_text())
        self.assertEqual(verified, dict(schema=1, feed_version='20261022_031145', content_sha256=CONTENT,
            verified_at=1792700000.5, calendar_dates_end='2026-12-16', release='20261022_031145'))

    def testOldReleaseWithoutContentHashIsHashedFromItsDatabase(self):
        with self.check_output([self.old], dict(feed_version='20261022_031145', feed_end='2029-10-21')), \
             patch('publish_release.tag_content_hash', return_value=CONTENT) as old_hash, \
             patch('publish_release.subprocess.run') as run:
            publish(self.assets, REPOSITORY, self.verified)
            old_hash.assert_called_once_with(REPOSITORY, '20261022_031145', '2029-10-21')
            run.assert_not_called()

    def testChangedContentIsPublishedOnlyAfterAllAssetsUploadedAndVerificationNamesNewRelease(self):
        with self.check_output([self.old], dict(content_sha256='d' * 64)), patch('publish_release.subprocess.run') as run:
            publish(self.assets, REPOSITORY, self.verified)
            self.assertEqual(run.call_count, 2)
            create = run.call_args_list[0].args[0]
            self.assertIn('--draft', create)
            self.assertTrue(any(a.endswith('schedule.sqlite.gz') for a in create))
            self.assertFalse(any(a.endswith('schedule.sqlite') for a in create), 'DBそのもの（約70MB）は配らない')
            self.assertIn('--draft=false', run.call_args_list[1].args[0])
        self.assertEqual(json.loads(self.verified.read_text())['release'], '20261023_031145')

    def testFirstEverReleaseIsPublished(self):
        with self.check_output([]), patch('publish_release.subprocess.run') as run:
            publish(self.assets, REPOSITORY, self.verified)
            self.assertEqual(run.call_count, 2)

    def testPublishedSameTagWithDifferentContentFailsLoudlyAndNeverOverwrites(self):
        releases = [self.old, dict(tag_name='20261023_031145', draft=False, prerelease=False)]
        with self.check_output(releases, dict(content_sha256='d' * 64)), patch('publish_release.subprocess.run') as run:
            with self.assertRaises(ValueError): publish(self.assets, REPOSITORY, self.verified)
            run.assert_not_called()
        self.assertFalse(self.verified.exists())

    def testLeftoverDraftOfTheSameTagIsDeletedAndRecreated(self):
        releases = [self.old, dict(tag_name='20261023_031145', draft=True, prerelease=False)]
        with self.check_output(releases, dict(content_sha256='d' * 64)), patch('publish_release.subprocess.run') as run:
            publish(self.assets, REPOSITORY, self.verified)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(commands[0][:3], ['gh', 'release', 'delete'])
            self.assertIn('--yes', commands[0])
            self.assertEqual(len(commands), 3)
            self.assertIn('--draft', commands[1])
            self.assertIn('--draft=false', commands[2])

    def testShrunkTimetableIsNotPublishedAndLeavesVerificationUntouched(self):
        for name in ['stops', 'trips', 'stop_times', 'options']:
            new = dict(self.manifest); new['counts'] = dict(self.manifest['counts']); new['counts'][name] = int(new['counts'][name] * 0.89)
            (self.assets / 'source-manifest.json').write_text(json.dumps(new))
            with self.check_output([self.old], dict(content_sha256='d' * 64, counts=self.manifest['counts'])), \
                 patch('publish_release.subprocess.run') as run:
                with self.assertRaises(ValueError): publish(self.assets, REPOSITORY, self.verified)
                run.assert_not_called()
            self.assertFalse(self.verified.exists())

    def testAllowShrinkSkipsOnlyTheCountGate(self):
        new = dict(self.manifest); new['counts'] = dict(self.manifest['counts']); new['counts']['options'] = 10
        (self.assets / 'source-manifest.json').write_text(json.dumps(new))
        with self.check_output([self.old], dict(content_sha256='d' * 64, counts=self.manifest['counts'])), \
             patch('publish_release.subprocess.run') as run:
            publish(self.assets, REPOSITORY, self.verified, allow_shrink=True)
            self.assertEqual(run.call_count, 2)
        # 古い版より前の日付、同じタグの公開済みは、allow_shrink でも止まる
        older = dict(tag_name='20261024_031145', draft=False, prerelease=False)
        with self.check_output([older], dict(content_sha256='d' * 64, counts=self.manifest['counts'])), patch('publish_release.subprocess.run') as run:
            with self.assertRaises(ValueError): publish(self.assets, REPOSITORY, allow_shrink=True)
            run.assert_not_called()

    def testSlightChangeAndFirstReleaseAreNotBlockedByTheGate(self):
        new = dict(self.manifest); new['counts'] = dict(self.manifest['counts']); new['counts']['trips'] = int(new['counts']['trips'] * 0.91)
        (self.assets / 'source-manifest.json').write_text(json.dumps(new))
        with self.check_output([self.old], dict(content_sha256='d' * 64, counts=self.manifest['counts'])), \
             patch('publish_release.subprocess.run') as run:
            publish(self.assets, REPOSITORY, self.verified)
            self.assertEqual(run.call_count, 2)

    def testOlderFeedIsRefused(self):
        older = dict(tag_name='20261024_031145', draft=False, prerelease=False)
        with self.check_output([older], dict(content_sha256='d' * 64)), patch('publish_release.subprocess.run') as run:
            with self.assertRaises(ValueError): publish(self.assets, REPOSITORY)
            run.assert_not_called()

    def testUploadFailureCannotMakeReleasePublicOrRecordVerification(self):
        with self.check_output([]), patch('publish_release.subprocess.run', side_effect=RuntimeError('upload interrupted')) as run:
            with self.assertRaises(RuntimeError): publish(self.assets, REPOSITORY, self.verified)
            self.assertEqual(run.call_count, 1)
            self.assertIn('--draft', run.call_args.args[0])
        self.assertFalse(self.verified.exists())

    def testManifestWithoutContentHashIsRejected(self):
        (self.assets / 'source-manifest.json').write_text(json.dumps(dict(feed_version='20261023_031145')))
        with patch('publish_release.subprocess.check_output') as call:
            with self.assertRaises(ValueError): publish(self.assets, REPOSITORY)
            call.assert_not_called()


if __name__ == '__main__': unittest.main()
