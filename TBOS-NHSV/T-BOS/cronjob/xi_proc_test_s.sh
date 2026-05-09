#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/xif/bin
#main
	
#	${RUN_PATH}/xi_ctrl3 str_fp 31001 >> /dev/null 2>&1  
#	${RUN_PATH}/xi_ctrl3 str_fp 31002 >> /dev/null 2>&1  
	${RUN_PATH}/xi_ctrl3 str_fp 32001 >> /dev/null 2>&1  
	${RUN_PATH}/xi_ctrl3 str_fp 32002 >> /dev/null 2>&1  
	
	
	

