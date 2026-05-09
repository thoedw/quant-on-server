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

DROP SEQUENCE XIH02M01_SEQ;

CREATE SEQUENCE XIH02M01_SEQ
    START WITH 1
	INCREMENT BY 1;

GRANT SELECT ON XIH02M01_SEQ TO PUBLIC;


UPDATE  VN.SSI02M00
SET
TOT_TRD_QTY   = 0 ,
			  TOT_TRD_AMT   = 0 ,
			  HIGH_PRI      = 0 ,
			  LOW_PRI       = 0 ,
			  MAX_PRI       = DECODE(TM_MAX_PRI,0,MAX_PRI,TM_MAX_PRI),
			  DN_PRI        = DECODE(TM_DN_PRI,0,DN_PRI,TM_DN_PRI),
			  TM_MAX_PRI    = 0 ,
			  TM_DN_PRI     = 0 ,
			  STRT_PRI      = 0 ,
			  LST_PRE_PRI   = 0 ,
			  BUY_PRI_STP1  = 0 ,
			  BUY_QTY_STP1  = 0 ,
			  BUY_PRI_STP2  = 0 ,
			  BUY_QTY_STP2  = 0 ,
			  BUY_PRI_STP3  = 0 ,
			  BUY_QTY_STP3  = 0 ,
			  SELL_PRI_STP1 = 0 ,
			  SELL_QTY_STP1 = 0 ,
			  SELL_PRI_STP2 = 0 ,
			  SELL_QTY_STP2 = 0 ,
			  SELL_PRI_STP3 = 0 ,
			  SELL_QTY_STP3 = 0 ;
COMMIT WORK;

UPDATE VN.SSI01C00 SET LAST_SEQ = 0;
COMMIT WORK;

UPDATE VN.SSI02C00 SET LAST_SEQ = 0;
COMMIT WORK;

DELETE FROM VN.SSI02M10;
COMMIT WORK;

UPDATE VN.SSI00M00 SET LAST_SEQ = 0;
COMMIT WORK;

DELETE FROM VN.XIH02M01;
COMMIT WORK;

spool off

_EOF_

echo "================ END[`date +%H:%M:%S`] ==================" >> ${LOGFILE}

exit


