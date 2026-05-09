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
    o_cnt                   NUMBER := 0;    
begin

    vn.pts_ts_realtime_cmr_err_chk(
        vn.vwdate,      -- i_dt        varchar2,
        'DAILY',        -- i_work_mn   varchar2,
        'SYSTEM',        -- i_work_trm  varchar2,
        o_cnt                   
    );
    
    commit;
end;
/
EOF

echo "======================END `date +%H:%S:%M`==========================="
exit