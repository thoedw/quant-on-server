. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/xif/bin

#main
	
	${RUN_PATH}/xi_ctrl3 stp_fp 31001 &
	${RUN_PATH}/xi_ctrl3 stp_fp 31002 &
	${RUN_PATH}/xi_ctrl3 stp_fp 31004 &
	${RUN_PATH}/xi_ctrl3 stp_fp 31003 &
	${RUN_PATH}/xi_ctrl3 stp_fp 32001 &
	${RUN_PATH}/xi_ctrl3 stp_fp 32002 &
	${RUN_PATH}/xi_ctrl3 stp_fp 32003 &
	${RUN_PATH}/xi_ctrl3 stp_fp 32004 &
	${RUN_PATH}/xi_ctrl3 stp_fp 33003 &
	${RUN_PATH}/xi_ctrl3 stp_fp 33004 &
	${RUN_PATH}/xi_ctrl3 stp_ap_fp 31002 & 
	${RUN_PATH}/xi_ctrl3 stp_ap_dc 31004 & 
	${RUN_PATH}/xi_ctrl3 stp_ha_dc 32004 & 
	${RUN_PATH}/xi_ctrl3 stp_ha 32002 &
	${RUN_PATH}/xi_ctrl3 stp_up 33002 &
	${RUN_PATH}/xi_ctrl3 stp_up_dc 33004 &
	${RUN_PATH}/xi_ctrl3 stp_sms 99999 &
	
	

