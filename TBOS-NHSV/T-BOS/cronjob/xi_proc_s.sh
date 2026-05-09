. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/xif/bin

#main

    ${RUN_PATH}/xi_ctrl3 str_fp 31003 >> /dev/null 2>&1
#   ${RUN_PATH}/xi_ctrl3 str_fp 31004 >> /dev/null 2>&1
	${RUN_PATH}/xi_ctrl3 str_ap_fp 31002 > /VNST/log/xif/ho.log 2>&1
#   ${RUN_PATH}/xi_ctrl3 str_ap_dc 31004 > /VNST/log/xif/ho_dc.log 2>&1

	#process 32001 get data HNX30 from VFEP and store in xih02m11
	${RUN_PATH}/xi_ctrl3 str_fp 32001 >> /dev/null 2>&1
	#Process 32003 get data market infor from HNX VFEP
#	${RUN_PATH}/xi_ctrl3 str_fp 32003 >> /dev/null 2>&1
#   ${RUN_PATH}/xi_ctrl3 str_fp 32004 >> /dev/null 2>&1
	${RUN_PATH}/xi_ctrl3 str_ha 32002 > /VNST/log/xif/ha.log 2>&1
#   ${RUN_PATH}/xi_ctrl3 str_ha_dc 32004 > /VNST/log/xif/ha_dc.log 2>&1

	${RUN_PATH}/xi_ctrl3 str_sms 99999 >> /dev/null 2>&1
