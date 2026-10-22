#!/usr/bin/env python3
import unittest
from prepare_release import approved_download, OfficialRedirect, SOURCE
import urllib.request

class DownloadBoundaryTests(unittest.TestCase):
    def testOfficialSourceAndPreviouslyReviewedBlobAreAllowed(self):
        self.assertTrue(approved_download(SOURCE))
        self.assertTrue(approved_download('https://dataodpt.blob.core.windows.net/files-open/Toei/data/ToeiBus-GTFS.zip?sv=public-access'))
    def testUnexpectedHostsPathsAndCredentialsRejected(self):
        for url in ['http://dataodpt.blob.core.windows.net/files-open/Toei/data/ToeiBus-GTFS.zip',
                    'https://example.com/ToeiBus-GTFS.zip',
                    'https://dataodpt.blob.core.windows.net/another-file.zip',
                    'https://user:pass@api-public.odpt.org/api/v4/files/Toei/data/ToeiBus-GTFS.zip']:
            self.assertFalse(approved_download(url))
    def testRedirectFailsBeforeFollowingUnknownHost(self):
        with self.assertRaises(ValueError):
            OfficialRedirect().redirect_request(urllib.request.Request(SOURCE),None,302,'Found',{},'https://example.com/file.zip')

if __name__=='__main__':unittest.main()
