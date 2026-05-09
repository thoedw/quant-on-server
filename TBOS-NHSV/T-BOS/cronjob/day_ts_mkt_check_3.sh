#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/bin/day

#main
	
	${RUN_PATH}/day_ts_mkt_check_bank 3 &
	
	
	

