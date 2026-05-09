#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

for ID in `ps -ef | grep -v grep | grep day_ts_advs_order | awk '{print $2}' | sort -r`
    do
        echo kill iday_ts_advs_order pre-open
        kill $ID
    done
