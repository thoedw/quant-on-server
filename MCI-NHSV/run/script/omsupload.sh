#!/bin/bash

. /user/stock/.profile

OMSIP=172.33.11.21
OMSUSR=mmdstest
OMSPWD=adminnhsv@123
OMSSRC=/user/stock/run/data/oms
OMSTAR=/mmdstest/OrderNet/product/system/data
MAX_TRY=3

TODAY=`date +%Y%m%d`
MASTERFILE=master_${TODAY}.tbl
LOGFILE=/user/stock/run/log/omsupload.log

#make stock information for OMS
/user/stock/run/bin/omsupload

sleep 1

#upload a file to OMS server
n_try=0
while [ "$n_try" -lt "$MAX_TRY" ]; do
	((n_try++))
	echo "[`date '+%m/%d %T'`] scp ${OMSSRC}/${MASTERFILE} ${OMSUSR}@${OMSIP}:${OMSTAR}"
	tr_result=$(/usr/bin/sshpass -v -p${OMSPWD} scp -o StrictHostKeyChecking=no ${OMSSRC}/${MASTERFILE} ${OMSUSR}@${OMSIP}:${OMSTAR} 2>&1)
	exit_code=$?
	echo "Upload result = $tr_result" >> ${LOGFILE}

	if [ "$exit_code" -eq 0 ]; then
		echo 'Upload done' >> ${LOGFILE}
		date >> ${LOGFILE}
		break
	fi

	echo 'Upload failed. retry...' >> ${LOGFILE}
	date >> ${LOGFILE}
	sleep 1
done;

echo >> ${LOGFILE}

