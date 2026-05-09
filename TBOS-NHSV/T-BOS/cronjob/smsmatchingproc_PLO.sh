#!/bin/sh

. /VNST/vnst/.profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/bin/day

#main
		
	${RUN_PATH}/day_ts_matching_sms 180 PLO &
