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

declare
	errno   number;
	msg     varchar2(1000);
	cnt     number;
begin
	vn.pts_bat_tso02h00_ins(vn.vwdate, errno, msg, cnt); 
	vn.pts_bat_tso04h00_ins(vn.vwdate, errno, msg, cnt);
	commit;
end;
/

EOF

echo "======================END `date +%H:%S:%M`==========================="
exit