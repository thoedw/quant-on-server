#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

for ID in `ps -ef | grep -v grep | grep /VNST/vnst/bin/day/day_bidv_cw_io | awk '{print $2}' | sort -r`
    do
		echo $ID
        echo kill day_bidv_cw_io
        kill $ID
    done

for ID in `ps -ef | grep -v grep | grep /VNST/vnst/bin/day/day_wrb_cw_io | awk '{print $2}' | sort -r`
    do
        echo $ID
        echo kill day_wrb_cw_io
        kill $ID
    done

for ID in `ps -ef | grep -v grep | grep /VNST/vnst/bin/day/day_bank_auto_io | awk '{print $2}' | sort -r`
    do
        echo $ID
        echo kill day_bank_auto_io
        kill $ID
    done
