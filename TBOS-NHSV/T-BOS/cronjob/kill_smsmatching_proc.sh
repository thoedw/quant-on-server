#!/bin/sh

. /VNST/vnst/.profile

cd /VNST/vnst/cronjob

for ID in `ps -ef | grep day_ts_matching_sms | awk '{print $2}' | sort -r`
    do
        echo kill day_ts_matching_sms
        kill $ID
    done
