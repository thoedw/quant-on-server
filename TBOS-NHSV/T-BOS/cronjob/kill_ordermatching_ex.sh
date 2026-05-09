#!/bin/sh

. /VNST/vnst/.vnst_profile

#cd /VNST/vnst/cronjob

for ID in `ps -ef | grep def_ts_order_matching_ex | grep -v grep | awk '{print $2}' | sort -r`
        do
                echo kill def_ts_order_matching_ex
                kill $ID
        done

for ID in `ps -ef | grep def_ts_order_matching_ha | grep -v grep | awk '{print $2}' | sort -r`
        do
                echo kill def_ts_order_matching_ha
                kill $ID
        done

for ID in `ps -ef | grep def_ts_order_matching_up | grep -v grep | awk '{print $2}' | sort -r`
        do
                echo kill def_ts_order_matching_up
                kill $ID
        done



