#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

for ID in `ps -ef | grep -v grep | grep /VNST/vnst/bin/day/day_ts_ord_nproc | awk '{print $2}' | sort -r`
    do
        echo $ID
        echo kill day_ts_ord_nproc
        kill -9 $ID
    done