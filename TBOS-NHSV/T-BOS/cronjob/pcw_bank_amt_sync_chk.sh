#!/bin/sh

echo 'first param: ' $1
echo 'first param: ' $2
echo 'first param: ' $3
echo 'first param: ' $4

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/bin/bank/bin

#main
    
    #Get list Hold to day
    ${RUN_PATH}/bidvutil l 02 $(date +\%d\%m\%Y) % %  >> /VNST/log/etc/HOLDTRANLIST.log &
    sleep 1 
    #Get list Unhold to day
    ${RUN_PATH}/bidvutil l 03 $(date +\%d\%m\%Y) % %  >> /VNST/log/etc/UNHOLDTRANLIST.log &
    sleep 60

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
	vn.pcw_bank_amt_sync_chk(vn.vwdate, 'DAILY', 'DAILY');
end;
/

EOF

echo "======================END `date +%H:%S:%M`==========================="
exit