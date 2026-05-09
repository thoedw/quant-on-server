#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/bin/day

#main
	
	${RUN_PATH}/day_ds_auto_sell_setl_realtime 60 &
	
	
	

