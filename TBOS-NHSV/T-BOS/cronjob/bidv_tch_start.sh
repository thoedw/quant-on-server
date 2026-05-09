#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/bin/day

#main
    
    ${RUN_PATH}/day_bidv_cw_io HSX 1200 >> /VNST/log/etc/TCH_1200.log &
    sleep 1 
    # ${RUN_PATH}/day_bidv_cw_io HSX 1201 >> /VNST/log/etc/TCH_1201.log &
    # sleep 1 
    ${RUN_PATH}/day_bidv_cw_io HNX 1200 >> /VNST/log/etc/TCH_1200.log &
    sleep 1 
    ${RUN_PATH}/day_bidv_cw_io HNX 1201 >> /VNST/log/etc/TCH_1201.log &
    sleep 1
    ${RUN_PATH}/day_wrb_cw_io HSX 1200 >> /VNST/log/etc/wrb_TCH_1200.log &
    sleep 1
    ${RUN_PATH}/day_wrb_cw_io HSX 1201 >> /VNST/log/etc/wrb_TCH_1201.log &
    sleep 1
    ${RUN_PATH}/day_wrb_cw_io HNX 1200 >> /VNST/log/etc/wrb_TCH_1200.log &
    sleep 1
    ${RUN_PATH}/day_wrb_cw_io HNX 1201 >> /VNST/log/etc/wrb_TCH_1201.log &
    sleep 1
    ${RUN_PATH}/day_bank_auto_io APP >> /VNST/log/etc/BANK_APPR_AUTO.log &