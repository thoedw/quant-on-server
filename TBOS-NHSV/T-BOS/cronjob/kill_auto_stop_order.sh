#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

for ID in `ps -ef | grep -v grep | grep /VNST/vnst/bin/day/auto_stop_order | awk '{print $2}' | sort -r`
    do
		echo $ID
        echo kill auto_stop_order
        kill $ID
    done
