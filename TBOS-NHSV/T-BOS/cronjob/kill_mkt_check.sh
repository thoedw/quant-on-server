#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

for ID in `ps -ef | grep -v grep | grep day_ts_mkt_check | awk '{print $2}' | sort -r`
    do
        echo kill day_ts_mkt_check
        kill $ID
    done
