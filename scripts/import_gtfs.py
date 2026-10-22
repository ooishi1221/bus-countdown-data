#!/usr/bin/env python3
"""Offline Toei GTFS normalizer. Standard library only; never downloads data.

Requires the acquisition sidecar (toei-download-metadata.json beside input, or
--metadata). Fails closed on unsupported timing/services; existing output is
never overwritten. Promote the DB + manifest as a pair after validation.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import tempfile
import zipfile
from zoneinfo import ZoneInfo

from content_hash import content_sha256

SOURCE = 'https://api-public.odpt.org/api/v4/files/Toei/data/ToeiBus-GTFS.zip'
CATALOG = 'https://ckan.odpt.org/dataset/b_bus_gtfs_jp-toei'
LICENSE = 'https://creativecommons.org/licenses/by/4.0/'
JST = ZoneInfo('Asia/Tokyo')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def day(value):
    require(bool(re.fullmatch(r'\d{8}', value)), 'Invalid service date')
    return dt.datetime.strptime(value, '%Y%m%d').date()


def seconds(value):
    require(bool(re.fullmatch(r'\d{1,2}:[0-5]\d:[0-5]\d', value)), 'Missing/invalid exact time: ' + value)
    h, m, s = map(int, value.split(':'))
    return h * 3600 + m * 60 + s


def digest(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def db_last_exception(path):
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        value = db.execute('SELECT max(date) FROM exceptions').fetchone()[0]
    finally:
        db.close()
    return f'{value[:4]}-{value[4:6]}-{value[6:]}' if value else None


def convert(source, output, metadata):
    output = Path(output)
    manifest_path = output.with_name('source-manifest.json')
    require(not output.exists() and not manifest_path.exists(), 'Use a new output directory; existing data is protected')
    acquisition = json.loads(Path(metadata).read_text())
    require(acquisition['url'] == SOURCE and acquisition['http_status'] == 200, 'Not the approved source')
    require(digest(source) == acquisition['sha256'], 'Acquisition hash mismatch')
    fetched = dt.datetime.fromisoformat(acquisition['retrieved_at'])
    require(fetched.tzinfo is not None, 'Acquisition time needs timezone')
    require(fetched <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5), 'Future acquisition time')
    with zipfile.ZipFile(source) as z:
        require(z.testzip() is None, 'ZIP CRC failure')
        require(len(z.namelist()) == len(set(z.namelist())), 'Duplicate ZIP member')
        require('frequencies.txt' not in z.namelist(), 'Frequency-based feeds are unsupported')
        def rows(name):
            with z.open(name + '.txt') as f:
                yield from csv.DictReader(io.TextIOWrapper(f, encoding='utf-8-sig', newline=''))
        agencies = list(rows('agency'))
        require(len(agencies) == 1 and agencies[0]['agency_id'] == '8000020130001'
                and agencies[0]['agency_timezone'] == 'Asia/Tokyo', 'Expected Toei Tokyo agency')
        agency = agencies[0]
        feed_rows = list(rows('feed_info'))
        require(len(feed_rows) == 1, 'Expected one feed_info')
        feed = feed_rows[0]
        start, end = day(feed['feed_start_date']), day(feed['feed_end_date'])
        require(start <= end, 'Reversed feed range')
        valid_from = dt.datetime.combine(start, dt.time(), JST)
        # 表示期限の上限。calendar_dates（祝日などの例外日）が最後に載っている日の翌日0時まで。
        # それより先は例外日が未掲載で、運行日を取り違えるおそれがあるため延長しても使わない。
        exception_days = sorted({day(r['date']) for r in rows('calendar_dates')})
        horizon = dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), JST)
        if exception_days:
            horizon = min(horizon, dt.datetime.combine(exception_days[-1] + dt.timedelta(days=1), dt.time(), JST))
        expires = min(horizon, fetched + dt.timedelta(days=14))
        require(valid_from < expires, 'Empty usable interval')
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output.parent) as temp:
            candidate = Path(temp) / output.name
            db = sqlite3.connect(candidate)
            db.executescript('''
                PRAGMA foreign_keys=ON;
                PRAGMA user_version=1;
                CREATE TABLE agency(id TEXT PRIMARY KEY, name TEXT NOT NULL, timezone TEXT NOT NULL);
                CREATE TABLE stops(id TEXT PRIMARY KEY, name TEXT NOT NULL, parent TEXT, platform TEXT NOT NULL,
                    kind INTEGER NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL);
                CREATE TABLE routes(id TEXT PRIMARY KEY, agency TEXT NOT NULL REFERENCES agency(id), name TEXT NOT NULL);
                CREATE TABLE services(id TEXT PRIMARY KEY);
                CREATE TABLE calendar(service TEXT PRIMARY KEY REFERENCES services(id), start TEXT NOT NULL,
                    end TEXT NOT NULL, weekdays INTEGER NOT NULL);
                CREATE TABLE exceptions(service TEXT REFERENCES services(id), date TEXT, type INTEGER CHECK(type IN (1,2)),
                    PRIMARY KEY(service,date)) WITHOUT ROWID;
                CREATE TABLE trips(id INTEGER PRIMARY KEY, gtfs_id TEXT UNIQUE NOT NULL, route TEXT REFERENCES routes(id),
                    service TEXT REFERENCES services(id), direction TEXT, headsign TEXT NOT NULL, shape TEXT NOT NULL,
                    description TEXT NOT NULL, pattern TEXT, origin TEXT);
                CREATE TABLE options(id INTEGER PRIMARY KEY, stop TEXT REFERENCES stops(id), route TEXT REFERENCES routes(id),
                    direction TEXT, headsign TEXT NOT NULL, pattern TEXT NOT NULL, origin TEXT NOT NULL,
                    UNIQUE(stop,route,direction,headsign,pattern));
                CREATE TABLE stop_times(trip INTEGER REFERENCES trips(id), seq INTEGER, stop TEXT REFERENCES stops(id),
                    arrival INTEGER NOT NULL, departure INTEGER NOT NULL, headsign TEXT NOT NULL,
                    pickup INTEGER NOT NULL CHECK(pickup BETWEEN 0 AND 3), dropoff INTEGER NOT NULL CHECK(dropoff BETWEEN 0 AND 3),
                    terminal INTEGER NOT NULL DEFAULT 0, option INTEGER REFERENCES options(id),
                    PRIMARY KEY(trip,seq)) WITHOUT ROWID;
            ''')
            db.execute('INSERT INTO agency VALUES(?,?,?)', (agency['agency_id'],agency['agency_name'],agency['agency_timezone']))
            for s in rows('stops'):
                lat, lon = float(s['stop_lat']), float(s['stop_lon'])
                require(math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180, 'Invalid coordinate')
                db.execute('INSERT INTO stops VALUES(?,?,?,?,?,?,?)', (s['stop_id'],s['stop_name'],s['parent_station'] or None,
                    s['platform_code'],int(s['location_type'] or '0'),lat,lon))
            require(not db.execute('SELECT 1 FROM stops s LEFT JOIN stops p ON s.parent=p.id WHERE s.parent IS NOT NULL AND p.id IS NULL').fetchone(), 'Missing parent')
            for r in rows('routes'):
                db.execute('INSERT INTO routes VALUES(?,?,?)', (r['route_id'],r['agency_id'],r['route_short_name'] or r['route_long_name']))
            calendars, exceptions = list(rows('calendar')), list(rows('calendar_dates'))
            db.executemany('INSERT INTO services VALUES(?)', [(v,) for v in sorted({r['service_id'] for r in calendars+exceptions})])
            for c in calendars:
                require(day(c['start_date'])<=day(c['end_date']), 'Reversed calendar')
                flags = [int(c[k]) for k in ['sunday','monday','tuesday','wednesday','thursday','friday','saturday']]
                require(all(f in (0,1) for f in flags), 'Invalid weekday')
                db.execute('INSERT INTO calendar VALUES(?,?,?,?)', (c['service_id'],c['start_date'],c['end_date'],sum(f<<i for i,f in enumerate(flags))))
            for e in exceptions:
                day(e['date'])
                db.execute('INSERT INTO exceptions VALUES(?,?,?)', (e['service_id'],e['date'],int(e['exception_type'])))
            trip_keys = {}
            for i,t in enumerate(rows('trips'),1):
                require(t['direction_id'] in ('','0','1'), 'Invalid direction')
                db.execute('INSERT INTO trips VALUES(?,?,?,?,?,?,?,?,?,?)', (i,t['trip_id'],t['route_id'],t['service_id'],
                    t['direction_id'] or None,t['trip_headsign'],t['shape_id'],t.get('jp_trip_desc',''),None,None))
                trip_keys[t['trip_id']] = i
            for s in rows('stop_times'):
                a, d = seconds(s['arrival_time']), seconds(s['departure_time'])
                require(a<=d, 'Departure before arrival')
                require(int(s['stop_sequence'])>=0, 'Negative sequence')
                require(s.get('timepoint','1') in ('','1'), 'Approximate stop time unsupported')
                db.execute('INSERT INTO stop_times(trip,seq,stop,arrival,departure,headsign,pickup,dropoff) VALUES(?,?,?,?,?,?,?,?)',
                    (trip_keys[s['trip_id']],int(s['stop_sequence']),s['stop_id'],a,d,s['stop_headsign'],int(s['pickup_type'] or '0'),int(s['drop_off_type'] or '0')))
            # Pattern identity includes ordered stops, pickup semantics and shape, never trip time/service.
            for trip, shape in db.execute('SELECT id,shape FROM trips').fetchall():
                times = db.execute('SELECT seq,stop,arrival,departure,pickup,dropoff FROM stop_times WHERE trip=? ORDER BY seq',(trip,)).fetchall()
                require(len(times)>=2, 'Trip has fewer than two stops')
                require(all(a[3]<=b[2] for a,b in zip(times,times[1:])), 'Nonmonotonic trip')
                identity = json.dumps([shape,[(t[1],t[4],t[5]) for t in times]],ensure_ascii=False,separators=(',',':'))
                pattern = shape + ':' + hashlib.sha256(identity.encode()).hexdigest()[:20]
                db.execute('UPDATE trips SET pattern=?,origin=? WHERE id=?',(pattern,times[0][1],trip))
                db.execute('UPDATE stop_times SET terminal=1 WHERE trip=? AND seq=?',(trip,times[-1][0]))
            # NULL direction is a real value; DISTINCT handles it without a wildcard.
            db.execute('''INSERT INTO options(stop,route,direction,headsign,pattern,origin)
                SELECT DISTINCT s.stop,t.route,t.direction,COALESCE(NULLIF(s.headsign,''),t.headsign),t.pattern,t.origin
                FROM stop_times s JOIN trips t ON t.id=s.trip JOIN stops p ON p.id=s.stop
                WHERE s.pickup=0 AND s.terminal=0 AND p.kind=0
                ORDER BY s.stop,t.route,t.direction,4,t.pattern''')
            require(not db.execute("SELECT 1 FROM options WHERE headsign='' LIMIT 1").fetchone(), 'Empty boarding headsign')
            db.execute('CREATE INDEX option_match ON options(stop,route,pattern,headsign,direction)')
            db.execute('''UPDATE stop_times AS s SET option=(SELECT o.id FROM trips t JOIN options o
                ON o.stop=s.stop AND o.route=t.route AND o.pattern=t.pattern AND o.direction IS t.direction
                AND o.headsign=COALESCE(NULLIF(s.headsign,''),t.headsign) WHERE t.id=s.trip)
                WHERE s.pickup=0 AND s.terminal=0''')
            db.execute('CREATE INDEX departures_for_option ON stop_times(option) WHERE option IS NOT NULL')
            require(not db.execute('PRAGMA foreign_key_check').fetchone(), 'Broken references')
            require(db.execute('PRAGMA integrity_check').fetchone()==('ok',), 'DB integrity failed')
            counts = {t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['stops','routes','trips','stop_times','options','calendar','exceptions']}
            counts['over_24h'] = db.execute('SELECT count(*) FROM stop_times WHERE departure>=86400').fetchone()[0]
            db.commit()
            db.execute('VACUUM')
            db.close()
            manifest = dict(schema_version=1,source_url=SOURCE,catalog_url=CATALOG,license_url=LICENSE,
                attribution='東京都交通局・公共交通オープンデータ協議会',attributions=list(rows('attributions')),
                fetched_at=fetched.timestamp(),verified_at=fetched.timestamp(),valid_from=valid_from.timestamp(),expires_at=expires.timestamp(),
                feed_start=start.isoformat(),feed_end=end.isoformat(),
                calendar_dates_end=exception_days[-1].isoformat() if exception_days else None,
                feed_version=feed['feed_version'],
                source_sha256=acquisition['sha256'],database_sha256=digest(candidate),
                content_sha256=content_sha256(candidate,end.isoformat()),counts=counts,
                freshness_policy='最後に公式データを確認した日（verified_at）から14日、またはfeed終了・例外日の掲載終了の翌日まで。BusCountdown側の方針であり提供者の保証ではありません。',
                transforms=['SQLite正規化・整数時刻化','stop_headsign優先','標柱と全停留所列・乗降条件・shapeによるパターン分離',
                    '終端・予約制pickup除外（元行は保持）','停留所・系統の抽出、残り分数の計算'],
                excluded_files=['shapes geometry','fares','translations','agency_jp','office_jp'])
            # manifest の calendar_dates_end（期限の上限）は、DB の例外日の最終日と必ず一致させる。
            stored=db_last_exception(candidate)
            require(stored==(exception_days[-1].isoformat() if exception_days else None),'calendar_dates_end does not match the database')
            candidate.rename(output)
            manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
            return manifest


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--metadata',type=Path)
    a=p.parse_args()
    result=convert(a.input,a.output,a.metadata or a.input.with_name('toei-download-metadata.json'))
    print(json.dumps(result['counts'],ensure_ascii=False))
