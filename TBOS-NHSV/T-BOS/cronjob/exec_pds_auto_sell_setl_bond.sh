#!/bin/sh

echo "======================START `date +%H:%S:%M`==========================="

#Declare variable
. ${HOME}/.bash_profile
BINETC=${HOME}/bin/etc

CONN_STR=`${BINETC}/boscfgmng -m1 -s21`
USER=`${BINETC}/boscfgmng -m1 -s22`
PSWD=`${BINETC}/boscfgmng -m1 -s23`

sqlplus ${USER}/${PSWD}@${CONN_STR} << EOF
begin
	vn.pds_auto_sell_setl(vn.fxc_sec_cd('R'), '1', 'auto', 'auto', '20,');
end;
/
EOF

echo "======================END `date +%H:%S:%M`==========================="
exit
