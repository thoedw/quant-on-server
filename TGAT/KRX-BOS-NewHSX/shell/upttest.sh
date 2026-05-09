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

sqlplus ${USER}/${PSWD}@${PSWD} << _EOF_
spool ${LOGFILE}

UPDATE VN.TSOTEST SET MKT_DRV_TP = '${2}' WHERE SB_KFX_TP = DECODE(${1}, 1, 'HOSTC', 'HASTC');
COMMIT WORK;

spool off

_EOF_


exit
