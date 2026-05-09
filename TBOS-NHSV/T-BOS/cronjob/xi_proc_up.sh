#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/xif/bin

#main

	${RUN_PATH}/xi_ctrl3 str_fp 33003 >> /dev/null 2>&1  
	${RUN_PATH}/xi_ctrl3 str_fp 33004 >> /dev/null 2>&1  
	${RUN_PATH}/xi_ctrl3 str_up 33002 >> /dev/null 2>&1  
