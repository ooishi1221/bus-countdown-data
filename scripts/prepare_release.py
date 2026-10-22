#!/usr/bin/env python3
"""Prepare local distribution assets from the approved Toei source; never publishes."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import urllib.request
import urllib.parse
from import_gtfs import convert, SOURCE


def approved_download(url):
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme == 'https' and parsed.username is None and parsed.password is None and (
        (parsed.hostname == 'api-public.odpt.org' and parsed.path == '/api/v4/files/Toei/data/ToeiBus-GTFS.zip') or
        (parsed.hostname == 'dataodpt.blob.core.windows.net' and parsed.path == '/files-open/Toei/data/ToeiBus-GTFS.zip'))

class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, newurl):
        if not approved_download(newurl):
            raise ValueError('Unexpected redirect target')
        return super().redirect_request(request, file, code, message, headers, newurl)


def prepare(output, source=None, metadata=None):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a new directory; existing assets are protected')
    output.mkdir(parents=True)
    if source is None:
        source = output / 'ToeiBus-GTFS.zip'
        request = urllib.request.Request(SOURCE, headers={'User-Agent': 'BusCountdown-data-preparation/1'})
        with urllib.request.build_opener(OfficialRedirect()).open(request, timeout=60) as response:
            if response.status != 200 or not approved_download(response.url):
                raise ValueError('Unexpected source response or redirect')
            size = 0
            with source.open('wb') as file:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 64 * 1024 * 1024:
                        raise ValueError('Source too large')
                    file.write(chunk)
        metadata = output / 'acquisition.json'
        with source.open('rb') as file:
            digest = hashlib.file_digest(file, 'sha256').hexdigest()
        metadata.write_text(json.dumps(dict(url=SOURCE, http_status=200,
            retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat(), sha256=digest)))
    if metadata is None:
        raise ValueError('Acquisition metadata required for local input')
    assets = output / 'assets'
    assets.mkdir()
    manifest = convert(source, assets / 'schedule.sqlite', metadata)
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    if not re.fullmatch(r'\d{8}_\d{6}', manifest['feed_version']):
        raise ValueError('Unsupported version format')
    if not manifest['valid_from'] <= now < manifest['expires_at']:
        raise ValueError('Feed not currently usable')
    db = sqlite3.connect(f"file:{assets / 'schedule.sqlite'}?mode=ro", uri=True)
    if db.execute('PRAGMA integrity_check').fetchone() != ('ok',) or db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Invalid database')
    if any(manifest['counts'][name] <= 0 for name in ['stops','routes','trips','stop_times','options']):
        raise ValueError('Empty data')
    if db.execute('SELECT count(*) FROM stop_times WHERE option IS NOT NULL AND (pickup!=0 OR terminal=1)').fetchone()[0]:
        raise ValueError('Nonboarding departure')
    db.close()
    acquisition = json.loads(Path(metadata).read_text())
    (assets / 'acquisition.json').write_text(json.dumps({key: acquisition[key] for key in
        ['url', 'http_status', 'retrieved_at', 'sha256']}, indent=2)+'\n')
    (assets / 'ATTRIBUTION.md').write_text('''# Toei timetable data

データ提供: 東京都交通局・公共交通オープンデータ協議会
Source: https://ckan.odpt.org/dataset/b_bus_gtfs_jp-toei
License: https://creativecommons.org/licenses/by/4.0/

BusCountdown normalizes GTFS to SQLite, separates boarding poles and trip patterns,
retains calendars and exceptions, and excludes terminal/reservation-only departures
from boarding options. source-manifest.json records source hashes, acquisition time,
feed version and transformations. This is not an official transport-operator app.
The seven-day expiry is an app policy, not an operator guarantee of accuracy.
No user locations or favorites are included.
''')
    print(json.dumps(dict(version=manifest['feed_version'], assets=str(assets),
        database_bytes=(assets/'schedule.sqlite').stat().st_size,
        source_sha256=manifest['source_sha256'],database_sha256=manifest['database_sha256'])))
    return manifest

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--input',type=Path)
    parser.add_argument('--metadata',type=Path)
    args=parser.parse_args()
    prepare(args.output,args.input,args.metadata)
