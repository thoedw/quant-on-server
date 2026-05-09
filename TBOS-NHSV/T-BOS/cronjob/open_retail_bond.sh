#!/bin/sh

echo "======================START `date +%H:%S:%M`==========================="

#Declare variable
. ${HOME}/.bash_profile
BINETC=${HOME}/bin/etc

CONN_STR=`${BINETC}/boscfgmng -m1 -s21`
USER=`${BINETC}/boscfgmng -m1 -s22`
PSWD=`${BINETC}/boscfgmng -m1 -s23`

#Main process

sqlplus ${USER}/${PSWD}@${CONN_STR} << EOF

DECLARE
   BEGIN
          update vn.tso03m00 set MKT_MAIN_STS = '1' , MKT_DRV_TP = 'LIS_CON_NML',WORK_DTM = sysdate	
		  where SB_KFX_TP = 'HASTC' and STK_TP in ( '22')  
		  ;
   END;
/

EOF

echo "======================END `date +%H:%S:%M`==========================="
exit