#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

for ID in `ps -e | grep def_vsd | awk '{print $1}' | sort -r`
	do
		echo kill def_vsd
		kill $ID
	done
