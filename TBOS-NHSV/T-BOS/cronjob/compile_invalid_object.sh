#!/bin/sh

echo "======================START `date +%H:%S:%M`==========================="

#Declare variable
. ${HOME}/.bash_profile
BINETC=${HOME}/bin/etc

CONN_STR=`${BINETC}/boscfgmng -m1 -s21`
USER=`${BINETC}/boscfgmng -m1 -s22`
PSWD=`${BINETC}/boscfgmng -m1 -s23`

#Main process
cd /VNST/vnst/cronjob

sqlplus ${USER}/${PSWD}@${CONN_STR} << EOF

    spool compile_invalid_object_auto.log;
    @compile_invalid_object_auto.sql
    spool off
EOF

echo "======================END `date +%H:%S:%M`==========================="
exit

