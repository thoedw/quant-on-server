#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/bin/day

#main
# vcb
    ${RUN_PATH}/day_vcb_cw_io TAPI 1410 >> /VNST/log/etc/vcb_auto_get_data_end_day.log &
    sleep 2
	${RUN_PATH}/day_vcb_cw_io TAPI 1420 >> /VNST/log/etc/vcb_auto_get_data_end_day.log &
    sleep 2
    ${RUN_PATH}/day_vcb_cw_io TAPI 1430 >> /VNST/log/etc/vcb_auto_get_data_end_day.log &