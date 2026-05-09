#!/bin/bash

. ${HOME}/.bash_profile

if [[ -z $DFIFHOME ]] ; then
	BASE=$HOME
else
	BASE=$DFIFHOME
fi

SHLDIR=${BASE}/bin/shell
source ${SHLDIR}/.dbcinfo

LOGDIR=${BASE}/../log/etc
PNM=$(basename $0)
LOGFILE=${LOGDIR}/${PNM%.*}.log

sqlplus ${USER}/${PSWD}@${DBNM} << _EOF_
spool ${LOGFILE}

SELECT 
	TO_CHAR(SYSDATE, 'YYYY/MM/DD HH24:MI:SS') TODAY, 
	VN.VWDATE WORKDAY
FROM DUAL;

UPDATE 	VN.TSO03M00
SET			MKT_MAIN_STS = '$1', MKT_DRV_TP = '$1', WORK_DTM = SYSDATE
WHERE		SB_KFX_TP IN ('HOSTC')
AND			VN.VWDATE = TO_CHAR(SYSDATE, 'YYYYMMDD')
;

spool off

_EOF_

echo "================ END[`date +%H:%M:%S`] ==================" >> ${LOGFILE}

exit
