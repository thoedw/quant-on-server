#!/bin/ksh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/bin/def

for ID in `ps -e | grep ho_stockinfo | awk '{print $1}' | sort -r`
     do 
		echo kill ho_stockinfo
		kill $ID
     done

for ID in `ps -e | grep ha_stockinfo | awk '{print $1}' | sort -r`
     do 
		echo kill ha_stockinfo
		kill $ID
     done

#/VNST/vnst/bin/def/kill_proc ho_stockinfo &
