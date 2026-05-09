#!/bin/sh

. /VNST/vnst/.vnst_profile

RUN_PATH=/VNST/deve/vnbgcha/backup

#main
	monthlybackup=$(date +%d)
	
	if [ $monthlybackup -eq 01 ]; then
		${RUN_PATH}/backup_fo.sh
	fi

