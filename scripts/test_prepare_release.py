#!/usr/bin/env python3
import unittest
from prepare_release import approved_download, OfficialRedirect, SOURCE, compress_database
import gzip
import tempfile
from pathlib import Path
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

class CompressionTests(unittest.TestCase):
    def testGzipHasNoOptionalHeaderFieldsAndRoundTrips(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'schedule.sqlite';source.write_bytes(b'bus countdown '*5000)
            destination=Path(temp)/'schedule.sqlite.gz';compress_database(source,destination)
            raw=destination.read_bytes()
            self.assertEqual(raw[:4],b'\x1f\x8b\x08\x00')
            self.assertEqual(raw[4:8],b'\x00\x00\x00\x00','更新時刻を入れない（同じ中身から同じバイト列）')
            self.assertEqual(gzip.decompress(raw),source.read_bytes())

if __name__=='__main__':unittest.main()
