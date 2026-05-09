#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/bin/day

#main
	
	${RUN_PATH}/day_ts_realtime_cmr 100000 &
	#${RUN_PATH}/day_db_monitor 5 &
	
	

