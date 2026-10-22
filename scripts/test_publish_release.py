#!/usr/bin/env python3
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from publish_release import publish, REPOSITORY

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.assets=Path(self.temp.name)
        self.manifest=dict(feed_version='20261023_031145',source_sha256='a'*64)
        (self.assets/'source-manifest.json').write_text(json.dumps(self.manifest))
    def testWrongRepositoryMakesNoCall(self):
        with patch('publish_release.subprocess.check_output') as call:
            with self.assertRaises(ValueError):publish(self.assets,'ooishi1221/bus-countdown-ios')
            call.assert_not_called()
    def testExistingVersionIsNeverOverwritten(self):
        with patch('publish_release.subprocess.check_output',return_value=json.dumps([dict(tag_name=self.manifest['feed_version'])]).encode()),patch('publish_release.subprocess.run') as run:
            publish(self.assets,REPOSITORY);run.assert_not_called()
    def testUnchangedSourceDoesNotExtendExpiry(self):
        old=dict(tag_name='20261022_031145',draft=False,prerelease=False)
        with patch('publish_release.subprocess.check_output',side_effect=[json.dumps([old]).encode(),json.dumps(self.manifest).encode()]),patch('publish_release.subprocess.run') as run:
            publish(self.assets,REPOSITORY);run.assert_not_called()
    def testUploadFailureCannotMakeReleasePublic(self):
        with patch('publish_release.subprocess.check_output',return_value=b'[]'),patch('publish_release.subprocess.run',side_effect=RuntimeError('upload interrupted')) as run:
            with self.assertRaises(RuntimeError):publish(self.assets,REPOSITORY)
            self.assertEqual(run.call_count,1)
            self.assertIn('--draft',run.call_args.args[0])
    def testPublishesOnlyAfterAllAssetsUploaded(self):
        with patch('publish_release.subprocess.check_output',return_value=b'[]'),patch('publish_release.subprocess.run') as run:
            publish(self.assets,REPOSITORY)
            self.assertEqual(run.call_count,2)
            self.assertIn('--draft',run.call_args_list[0].args[0])
            self.assertIn('--draft=false',run.call_args_list[1].args[0])

if __name__=='__main__':unittest.main()
