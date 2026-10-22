# BusCountdown timetable distribution

Official Toei GTFS normalized for BusCountdown. Data attribution: 東京都交通局・公共交通オープンデータ協議会. Data license: https://creativecommons.org/licenses/by/4.0/
Source: https://ckan.odpt.org/dataset/b_bus_gtfs_jp-toei

Releases contain the gzip-compressed SQLite database, source provenance and attribution. Conversion changes and hashes are recorded in each source-manifest.json. No user locations or favorites are collected here. The fourteen-day expiry is an app freshness policy, not a transit-operator guarantee. This repository is not operated by the transport operator.

Preparation scripts normalize times and route patterns, preserve service calendars, and validate references. The scheduled workflow prepares a fresh candidate daily and only publishes a complete validated version. Existing version assets are never overwritten. Failed candidates leave the current release and its expiry unchanged. A new release is created only when the timetable content changes; on other days `verified.json` records that the official data was checked again (content hash and time only).
