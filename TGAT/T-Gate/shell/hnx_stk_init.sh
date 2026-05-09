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

DROP SEQUENCE XIH02M11_SEQ;
CREATE SEQUENCE XIH02M11_SEQ
    START WITH 1
	INCREMENT BY 1;
GRANT SELECT ON XIH02M11_SEQ TO PUBLIC;

DROP SEQUENCE XIH02M10_SEQ;
CREATE SEQUENCE XIH02M10_SEQ
    START WITH 1
	INCREMENT BY 1;
GRANT SELECT ON XIH02M10_SEQ TO PUBLIC;

DROP SEQUENCE XIH02M20_SEQ;
CREATE SEQUENCE XIH02M20_SEQ
    START WITH 1
	INCREMENT BY 1;
GRANT SELECT ON XIH02M20_SEQ TO PUBLIC;

UPDATE VN.SSI03M00
SET    
	MAX_PRI       	= DECODE(TM_MAX_PRI,0,MAX_PRI,TM_MAX_PRI),
	DN_PRI        	= DECODE(TM_DN_PRI,0,DN_PRI,TM_DN_PRI),
	STD_PRI      		= DECODE(TM_STD_PRI,0,STD_PRI,TM_STD_PRI),
	TM_MAX_PRI    	= 0 ,
	TM_DN_PRI     	= 0 ,
	TM_STD_PRI    	= 0 ,
	-- CLS_PRI      = 0,
	BUY_ORD_PRI     = 0,
	SELL_ORD_PRI    = 0,
	-- AVG_PRI      = 0,
	LST_PRE_PRI     = 0,
	BEST_BID_PRI    = 0,
	BEST_BID_QTY    = 0,
	BEST_OFF_QTY    = 0,
	BEST_OFF_PRI    = 0,
	TOT_BID_QTY     = 0,
	TOT_OFF_QTY     = 0,
	MTH_QTY         = 0,
	MTH_PRI         = 0,
	TOT_TRD_QTY     = 0,
	TOT_TRD_AMT     = 0,
	TOT_BID_CNT     = 0,
	NM_TRD_AMT      = 0,
	PT_MTH_QTY      = 0,
	PT_MTH_PRI      = 0,
	PT_TOT_QTY      = 0,
	PT_TOT_AMT      = 0,
	TOT_BUY_AMT     = 0,
	TOT_BUY_QTY     = 0,
	TOT_SELL_AMT    = 0,
	TOT_SELL_QTY    = 0,
	FRGN_BUY_QTY    = 0,
	FRGN_BUY_AMT    = 0,
	FRGN_SELL_QTY   = 0,
	FRGN_SELL_AMT   = 0,
	FRGN_BUY_AVLB   = 0,
	TOT_BUY_CNT     = 0,
	TOT_SELL_CNT    = 0,
	EXP_MTH_PRI     = 0,
	EXP_MTH_QTY     = 0,
	FAC_PRI         = 0,
	STRT_PRI        = 0,
	BEF_STRT_PRI    = 0,
	BEF_CLS_PRI     = 0,
	STK_TRD_UNIT    = 0,
	LIST_STK_QTY    = 0,
	TRD_DT_CNT      = 0,
	HIGH_PRI        = 0,
	LOW_PRI         = 0,
	NM_TRD_QTY      = 0,
	TOT_OFF_CNT     = 0,
	OD_TOT_BUY_QTY  = 0,
	OD_TOT_SELL_QTY = 0,
	BEF_MTH_PRI     = 0,
	MKT_MAIN_STS    = '0',
	MKT_DRV_TP      = '-';
COMMIT WORK;


UPDATE VN.SSI03M10
SET 
	MAX_PRI       	= DECODE(TM_MAX_PRI,0,MAX_PRI,TM_MAX_PRI),
	DN_PRI        	= DECODE(TM_DN_PRI,0,DN_PRI,TM_DN_PRI),
	STD_PRI      		= DECODE(TM_STD_PRI,0,STD_PRI,TM_STD_PRI),
	TM_MAX_PRI    	= 0 ,
	TM_DN_PRI     	= 0 ,
	TM_STD_PRI    	= 0 ,
	-- CLS_PRI      = 0,
	BUY_ORD_PRI     = 0,
	SELL_ORD_PRI    = 0,
	-- AVG_PRI      = 0,
	LST_PRE_PRI     = 0,
	BEST_BID_PRI    = 0,
	BEST_BID_QTY    = 0,
	BEST_OFF_QTY    = 0,
	BEST_OFF_PRI    = 0,
	TOT_BID_QTY     = 0,
	TOT_OFF_QTY     = 0,
	MTH_QTY         = 0,
	MTH_PRI         = 0,
	TOT_TRD_QTY     = 0,
	TOT_TRD_AMT     = 0,
	TOT_BID_CNT     = 0,
	NM_TRD_AMT      = 0,
	PT_MTH_QTY      = 0,
	PT_MTH_PRI      = 0,
	PT_TOT_QTY      = 0,
	PT_TOT_AMT      = 0,
	TOT_BUY_AMT     = 0,
	TOT_BUY_QTY     = 0,
	TOT_SELL_AMT    = 0,
	TOT_SELL_QTY    = 0,
	FRGN_BUY_QTY    = 0,
	FRGN_BUY_AMT    = 0,
	FRGN_SELL_QTY   = 0,
	FRGN_SELL_AMT   = 0,
	FRGN_BUY_AVLB   = 0,
	TOT_BUY_CNT     = 0,
	TOT_SELL_CNT    = 0,
	EXP_MTH_PRI     = 0,
	EXP_MTH_QTY     = 0,
	FAC_PRI         = 0,
	STRT_PRI        = 0,
	BEF_STRT_PRI    = 0,
	BEF_CLS_PRI     = 0,
	STK_TRD_UNIT    = 0,
	LIST_STK_QTY    = 0,
	TRD_DT_CNT      = 0,
	HIGH_PRI        = 0,
	LOW_PRI         = 0,
	NM_TRD_QTY      = 0,
	TOT_OFF_CNT     = 0,
	OD_TOT_BUY_QTY  = 0,
	OD_TOT_SELL_QTY = 0,
	BEF_MTH_PRI     = 0,
	MKT_MAIN_STS    = '0',
	MKT_DRV_TP      = '-';
COMMIT WORK;

DELETE FROM VN.SSI03M01;
COMMIT WORK;

DELETE FROM VN.XIH02M10;
COMMIT WORK;

DELETE FROM VN.XIH02M11;
COMMIT WORK;

DELETE FROM VN.XIH02M20;
COMMIT WORK;

spool off

_EOF_

echo "================ END[`date +%H:%M:%S`] ==================" >> ${LOGFILE}

exit

