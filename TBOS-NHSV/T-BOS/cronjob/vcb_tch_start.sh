#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/bin/day

#main
# vcb
    ${RUN_PATH}/day_vcb_cw_io REAL 1100 >> /VNST/log/etc/VCB_1100.log &

	${RUN_PATH}/day_vcb_cw_io REAL 1200 >> /VNST/log/etc/VCB_1200.log &
	

