#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

for ID in `ps -ef | grep -v grep | grep /VNST/vnst/bin/day/day_ds_auto_sell_setl_realtime | awk '{print $2}' | sort -r`
    do
		echo $ID
        echo kill day_ds_auto_sell_setl_realtime
        kill $ID
    done