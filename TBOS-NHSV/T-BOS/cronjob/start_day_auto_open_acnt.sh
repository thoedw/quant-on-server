#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/bin/day

#main
	for ID in `ps -ef | grep -v grep | grep /VNST/vnst/bin/day/day_auto_open_acnt | awk '{print $2}' | sort -r`
    do
		echo $ID
        echo kill day_auto_open_acnt
        kill $ID
    done
	${RUN_PATH}/day_auto_open_acnt 10 &