#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

for ID in `ps -ef | grep -v grep | grep exec_paa_sms_birth_dt | awk '{print $2}' | sort -r`
    do
        echo $ID
        echo kill exec_paa_sms_birth_dt
        kill $ID
    done