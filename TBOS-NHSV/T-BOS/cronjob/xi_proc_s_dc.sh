#!/bin/sh

#. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/xif/bin

#main
	
    ${RUN_PATH}/xi_ctrl3 stp_ap_fp 31002 &
#	${RUN_PATH}/xi_ctrl3 str_fp 31003 >> /dev/null 2>&1  
	${RUN_PATH}/xi_ctrl3 str_fp 31004 >> /dev/null 2>&1  
	${RUN_PATH}/xi_ctrl3 str_ap_dc 31004 >> /dev/null 2>&1  
	
	
	

