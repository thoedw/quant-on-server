#!/bin/sh

. ${HOME}/.bash_profile
BASE=${HOME}/cronjob
LOGFILE=${BASE}/sync_inst_trade.log
BINDIR=${HOME}/bin 
BINETC=${BINDIR}/etc
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

spool ${LOGFILE}
copy from $USER/$PSWD@vnst to $USER/$PSWD@vnst_sim insert drcsvm01 using SELECT * FROM drcsvm01 WHERE SUBSTR(RGT_VSD_SEQ, 1, 8) = to_char(sysdate,'yyyymmdd');
copy from $USER/$PSWD@vnst to $USER/$PSWD@vnst_sim insert drvsdm02 using SELECT * FROM drvsdm02 WHERE SUBSTR(FILE_NM, 1, 8) = to_char(sysdate,'yyyymmdd') AND REPT_TP = 'POSITION';
copy from $USER/$PSWD@vnst to $USER/$PSWD@vnst_sim insert drcsvm07 using SELECT * FROM drcsvm07 WHERE SUBSTR(RGT_VSD_SEQ, 1, 8) = to_char(sysdate,'yyyymmdd');
copy from $USER/$PSWD@vnst to $USER/$PSWD@vnst_sim insert drvsdm02 using SELECT * FROM drvsdm02 WHERE SUBSTR(FILE_NM, 1, 8) = to_char(sysdate,'yyyymmdd') AND REPT_TP = 'INST_TRADE';
/
EOF
sqlplus ${USER}/${PSWD}@${CONN_STR} << EOF
UPDATE drcsvm07 SET DR_STATUS = null WHERE SUBSTR(RGT_VSD_SEQ, 1, 8) = to_char(sysdate,'yyyymmdd');
/
EOF

echo "======================END `date +%H:%S:%M`==========================="
exit
