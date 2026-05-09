#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/bin/day

#main
	
	${RUN_PATH}/bidv_1200_hn 100000 >> /VNST/log/etc/bidv_1200_hn.bank.log &
	
	sleep 60 

	${RUN_PATH}/bidv_1200_hcm 100000 >> /VNST/log/etc/bidv_1200_hcm.bank.log &