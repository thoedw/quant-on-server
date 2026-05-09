#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/xif/bin

${RUN_PATH}/def_ts_order_matching_ex >> /dev/null 2>&1 &
#${RUN_PATH}/def_ts_order_matching_ha >> /dev/null 2>&1 &
#${RUN_PATH}/def_ts_order_matching_up >> /dev/null 2>&1 &

