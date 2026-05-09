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

spool xih02_seq.log;

@xih02m00_seq.sql
@xih02m10_seq.sql
@xih02m01_seq.sql

spool off
EOF

echo "======================END `date +%H:%S:%M`==========================="
exit
