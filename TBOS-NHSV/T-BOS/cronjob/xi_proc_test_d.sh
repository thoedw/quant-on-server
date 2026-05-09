#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/xif/bin

#main
	
	${RUN_PATH}/xi_ctrl3 stp_fp 31001 &
	${RUN_PATH}/xi_ctrl3 stp_fp 31002 &
	${RUN_PATH}/xi_ctrl3 stp_fp 32001 &
	${RUN_PATH}/xi_ctrl3 stp_fp 32002 &
	
	
	

