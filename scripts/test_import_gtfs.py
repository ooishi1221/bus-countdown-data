#!/usr/bin/env python3
"""Synthetic importer boundary tests; fixtures are generated outside app resources."""
import csv
import datetime as dt
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from import_gtfs import SOURCE, convert, digest, seconds, db_last_exception

class ImportTests(unittest.TestCase):
    def fixture(self, folder, change=None):
        today=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')
        tables={
            'agency':[dict(agency_id='8000020130001',agency_name='Synthetic',agency_timezone='Asia/Tokyo')],
            'feed_info':[dict(feed_start_date=today,feed_end_date='20991231',feed_version='synthetic-test')],
            'stops':[dict(stop_id=s,stop_name=s,stop_lat='35',stop_lon='139',parent_station='',platform_code='',location_type='0') for s in ['a','b','c']],
            'routes':[dict(route_id='r',agency_id='8000020130001',route_short_name='test')],
            'calendar':[dict(service_id='weekday',start_date=today,end_date='20991231',**{d:'1' for d in ['monday','tuesday','wednesday','thursday','friday','saturday','sunday']})],
            'calendar_dates':[dict(service_id='special',date=today,exception_type='1')],
            'trips':[dict(trip_id=t,route_id='r',service_id='special',trip_headsign='end',direction_id='',shape_id='same') for t in ['t1','t2']],
            'stop_times':[],
            'attributions':[dict(organization_name='Synthetic')]
        }
        for trip,stops in [('t1',['a','b','c']),('t2',['a','c','b'])]:
            for i,stop in enumerate(stops):
                tables['stop_times'].append(dict(trip_id=trip,stop_id=stop,stop_sequence=str(i+1),arrival_time=f'24:{10+i*10}:00',departure_time=f'24:{10+i*10}:00',stop_headsign='loop',pickup_type='2' if i==1 else '',drop_off_type=''))
        if change:change(tables)
        source=folder/'input.zip'
        with zipfile.ZipFile(source,'w') as z:
            for name,rows in tables.items():
                f=io.StringIO();w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows);z.writestr(name+'.txt',f.getvalue())
        metadata=folder/'meta.json'
        metadata.write_text(json.dumps(dict(url=SOURCE,http_status=200,sha256=digest(source),retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat())))
        return source,metadata

    def test_overnight_patterns_terminal_pickup_null_direction_and_exception_only(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);source,meta=self.fixture(folder);output=folder/'out'/'schedule.sqlite'
            m=convert(source,output,meta)
            with sqlite3.connect(output) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM options').fetchone()[0],2)
                self.assertEqual(db.execute('SELECT count(DISTINCT pattern) FROM trips').fetchone()[0],2)
                self.assertEqual(db.execute('SELECT min(departure) FROM stop_times').fetchone()[0],87000)
                self.assertEqual(db.execute('SELECT count(*) FROM options WHERE direction IS NULL AND headsign="loop"').fetchone()[0],2)
                self.assertEqual(db.execute('SELECT count(*) FROM stop_times WHERE option IS NOT NULL AND (terminal=1 OR pickup!=0)').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT count(*) FROM exceptions WHERE service="special"').fetchone()[0],1)
            self.assertEqual(m['database_sha256'],digest(output))
            before=output.read_bytes()
            with self.assertRaises(ValueError):convert(source,output,meta)
            self.assertEqual(before,output.read_bytes())

    def test_content_hash_ignores_rebuilt_zip_but_not_timetable_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            (folder/'a').mkdir();(folder/'b').mkdir();(folder/'c').mkdir()
            def run(name,change=None):
                source,meta=self.fixture(folder/name,change)
                return convert(source,folder/name/'out'/'schedule.sqlite',meta)
            a=run('a')
            # 同じ中身で、feed_version とZIPのバイト列だけが違う（公式が毎日作り直す場合）
            def rebuilt(t):
                t['feed_info'][0]['feed_version']='synthetic-test-rebuilt'
                t['stops'].reverse();t['trips'].reverse()
            b=run('b',rebuilt)
            self.assertNotEqual(a['source_sha256'],b['source_sha256'])
            self.assertEqual(a['content_sha256'],b['content_sha256'])
            changed=run('c',lambda t:t['stop_times'][0].update(departure_time='24:11:00',arrival_time='24:11:00'))
            self.assertNotEqual(a['content_sha256'],changed['content_sha256'])

    def test_expiry_is_capped_by_last_exception_day_and_records_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            source,meta=self.fixture(folder)
            m=convert(source,folder/'out'/'schedule.sqlite',meta)
            self.assertEqual(m['verified_at'],m['fetched_at'])
            last=dt.date.fromisoformat(m['calendar_dates_end'])
            cap=dt.datetime.combine(last+dt.timedelta(days=1),dt.time(),dt.timezone(dt.timedelta(hours=9))).timestamp()
            self.assertLessEqual(m['expires_at'],cap)
            self.assertLessEqual(m['expires_at'],m['fetched_at']+14*86400)
            self.assertEqual(m['calendar_dates_end'],db_last_exception(folder/'out'/'schedule.sqlite'))

    def test_stop_names_from_translations_are_added_and_optional(self):
        def translations(t):
            t['translations']=[dict(table_name='stops',field_name='stop_name',language=l,translation=v,record_id=r,record_sub_id='',field_value='')
                for r,l,v in [('a','ja','あ'),('a','ja-Hrkt','えー'),('a','en','Ay'),('b','ja-Hrkt','びー'),('zzz','ja-Hrkt','ふめい'),('c','en','Sea')]]
            t['translations'].append(dict(table_name='trips',field_name='trip_headsign',language='en',translation='End',record_id='t1',record_sub_id='',field_value=''))
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);source,meta=self.fixture(folder,translations);out=folder/'out'/'schedule.sqlite'
            m=convert(source,out,meta)
            with sqlite3.connect(out) as db:
                # 読みがある停留所だけ。英語名しかない c と、未知の停留所 zzz は行を作らない。
                self.assertEqual(db.execute('SELECT stop,reading,english FROM stop_names ORDER BY stop').fetchall(),[('a','えー','Ay'),('b','びー','')])
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(m['counts']['stop_names'],2)
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);source,meta=self.fixture(folder);out=folder/'out'/'schedule.sqlite'
            m=convert(source,out,meta)  # translations.txt が無い feed でも表は作る（空）
            with sqlite3.connect(out) as db:self.assertEqual(db.execute('SELECT count(*) FROM stop_names').fetchone()[0],0)
            self.assertEqual(m['counts']['stop_names'],0)

    def test_invalid_feed_is_rejected_without_published_output(self):
        cases=[lambda t:t['stop_times'][0].update(stop_id='missing'),
               lambda t:t['stop_times'][0].update(departure_time=''),
               lambda t:t['stop_times'][0].update(arrival_time='25:00:00',departure_time='25:00:00'),
               lambda t:t['stops'][0].update(stop_lat='NaN'),
               lambda t:t['trips'][0].update(service_id='missing'),
               lambda t:t['calendar_dates'].append(dict(t['calendar_dates'][0]))]
        for change in cases:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                folder=Path(temp);source,meta=self.fixture(folder,change);out=folder/'out'/'schedule.sqlite'
                with self.assertRaises((ValueError,sqlite3.IntegrityError)):convert(source,out,meta)
                self.assertFalse(out.exists());self.assertFalse(out.with_name('source-manifest.json').exists())

    def test_tampered_source_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);source,meta=self.fixture(folder)
            with source.open('ab') as f:f.write(b'changed')
            with self.assertRaises(ValueError):convert(source,folder/'out'/'schedule.sqlite',meta)

    def test_time_is_not_wrapped(self):
        self.assertEqual(seconds('48:10:00'),173400)
        for value in ['24:60:00','100:00:00','-1:00:00','']:
            with self.assertRaises(ValueError):seconds(value)

if __name__=='__main__':unittest.main()
