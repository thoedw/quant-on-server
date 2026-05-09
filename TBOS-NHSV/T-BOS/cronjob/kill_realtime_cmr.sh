#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob


for ID in `ps -ef | grep -v grep | grep day_ts_realtime_cmr | awk '{print $2}' | sort -r`
    do
        echo kill day_ts_realtime_cmr
        kill $ID
    done

for ID in `ps -ef | grep -v grep | grep day_db_monitor | awk '{print $2}' | sort -r`
    do
        echo kill day_ts_realtime_cmr
        kill $ID
    done
