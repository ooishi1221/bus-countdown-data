#!/usr/bin/env python3
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from check_freshness import problems

NOW = dt.datetime(2026, 10, 25, 3, 20, tzinfo=dt.timezone.utc)


class FreshnessTests(unittest.TestCase):
    def check(self, **change):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'verified.json'
            data = dict(schema=1, feed_version='20261022_031145', content_sha256='c' * 64,
                        verified_at=(NOW - dt.timedelta(hours=6)).timestamp(), release='20261022_031145')
            data.update(change)
            path.write_text(json.dumps(data))
            return problems(path, NOW)

    def testRecentVerificationIsFine(self):
        self.assertEqual(self.check(), [])
        self.assertEqual(self.check(verified_at=(NOW - dt.timedelta(days=2, hours=23)).timestamp()), [])

    def testThreeDaysOldFails(self):
        self.assertEqual(len(self.check(verified_at=(NOW - dt.timedelta(days=3, minutes=1)).timestamp())), 1)

    def testExceptionRunwayUnderSevenDaysFails(self):
        self.assertEqual(self.check(calendar_dates_end='2026-12-16'), [])
        self.assertEqual(self.check(calendar_dates_end='2026-11-03'), [])
        self.assertEqual(len(self.check(calendar_dates_end='2026-10-30')), 1)
        self.assertEqual(len(self.check(calendar_dates_end='2026-10-20')), 1)
        self.assertEqual(len(self.check(calendar_dates_end='not-a-date')), 1)

    def testFutureAndWrongSchemaAndMissingFileFail(self):
        self.assertEqual(len(self.check(verified_at=(NOW + dt.timedelta(hours=1)).timestamp())), 1)
        self.assertEqual(len(self.check(schema=2)), 1)
        self.assertEqual(len(problems('/nonexistent/verified.json', NOW)), 1)


if __name__ == '__main__': unittest.main()
