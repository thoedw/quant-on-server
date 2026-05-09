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
spool xih03_seq.log;

@xih03m01_seq.sql
@xih03m11_seq.sql
@xih03m10_seq.sql
@xih03m20_seq.sql
@xih03m30_seq.sql
@xih03m12_seq.sql
@xih03m13_seq.sql
@xih03m21_seq.sql
@xih03m31_seq.sql

spool off
EOF

echo "======================END `date +%H:%S:%M`==========================="
exit
