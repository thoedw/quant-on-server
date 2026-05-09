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

UPDATE 	DIR.FTS_SERIES
SET			TRAD_STAT = '1'
;

spool off

_EOF_


exit
