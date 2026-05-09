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

begin
	vn.pxc_branch_open('061'); 
	commit;
end;
/

echo "======================END `date +%H:%S:%M`==========================="
exit