#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/bin/day

#main
	
	${RUN_PATH}/day_ss_stk_updt  >> /VNST/log/etc/stk_update.log 2>&1
	
	
	

