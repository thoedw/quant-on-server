
#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/bin/day

#main
	
	${RUN_PATH}/bidv_1201_hn 100000 >> /VNST/log/etc/bidv_1201_hn.bank.log &

	sleep 60

	${RUN_PATH}/bidv_1201_hcm 100000 >> /VNST/log/etc/bidv_1201_hcm.bank.log &
	
	
	