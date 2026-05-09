#!/bin/bash

. ${HOME}/.bash_profile

if [[ -z $DFIFHOME ]] ; then
	BASE=$HOME
else
	BASE=$DFIFHOME
fi

SHLDIR=${BASE}/bin/shell
source ${SHLDIR}/holiopt
source ${SHLDIR}/.dbcinfo

LOGDIR=${BASE}/../log/def
PNM=$(basename $0)
LOGFILE=${LOGDIR}/${PNM%.*}.log

sqlplus ${USER}/${PSWD}@${DBNM} << _EOF_
spool ${LOGFILE}

SELECT 
	TO_CHAR(SYSDATE, 'YYYY/MM/DD HH24:MI:SS') TODAY, 
	VN.VWDATE WORKDAY
FROM DUAL;

DELETE FROM VN.SSI03M14;
COMMIT WORK;

DELETE FROM VN.XIH03M40;
COMMIT WORK;

spool off

_EOF_

echo "================ END[`date +%H:%M:%S`] ==================" >> ${LOGFILE}

exit

