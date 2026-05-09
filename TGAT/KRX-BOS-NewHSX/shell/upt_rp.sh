#!/bin/bash

. ${HOME}/.bash_profile

if [[ -z $DFIFHOME ]] ; then
	BASE=$HOME
else
	BASE=$DFIFHOME
fi

SHLDIR=${BASE}/bin/shell
source ${SHLDIR}/.dbcinfo

LOGDIR=${BASE}/../log/def
PNM=$(basename $0)
LOGFILE=${LOGDIR}/${PNM%.*}.log

sqlplus ${USER}/${PSWD}@${PSWD} << _EOF_
spool ${LOGFILE}

SELECT 
	TO_CHAR(SYSDATE, 'YYYY/MM/DD HH24:MI:SS') TODAY, 
	VN.VWDATE WORKDAY
FROM DUAL;

UPDATE 	VN.TSO03M00
SET			MKT_MAIN_STS = 'BM', MKT_DRV_TP = 'RP', WORK_DTM = SYSDATE
WHERE		SB_KFX_TP IN ('HOSTC')
AND			VN.VWDATE = TO_CHAR(SYSDATE, 'YYYYMMDD')
;

spool off

_EOF_

echo "================ END[`date +%H:%M:%S`] ==================" >> ${LOGFILE}

exit
