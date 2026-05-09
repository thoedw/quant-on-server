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

UPDATE  DIR.FTS_SERIES
SET     MRKT_STATE = NVL(TO_CHAR($1), MRKT_STATE)
				, RESERVE_STATE = DECODE(TO_CHAR($1)
							, '2', '1', '3', '1'
							, '4', DECODE(MRKT_STATE, '2', '2', '3', '2', '3')
							, '5', '4', '6', '4'
							, RESERVE_STATE)
WHERE   TO_CHAR(SYSDATE, 'yyyymmdd') BETWEEN  START_DATE AND EXP_DATE;

COMMIT WORK;

spool off

_EOF_

echo "================ END[`date +%H:%M:%S`] ==================" >> ${LOGFILE}

exit

