#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

for ID in `ps -ef | grep -v grep | grep VirtualExchange | awk '{print $2}' | sort -r`
    do
        echo kill VirtualExchange
        kill $ID
    done
