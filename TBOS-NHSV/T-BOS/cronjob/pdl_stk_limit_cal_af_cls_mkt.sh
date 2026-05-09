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

    vn.pdl_stk_limit_cal_af_cls_mkt(
        vn.vhdate,      -- i_dt        varchar2,
        '%',            -- i_acnt_no   varchar2    default '%',
        '%',            -- i_sub_no    varchar2    default '%',
        'auto',        -- i_work_mn   varchar2,
        'SYSTEM',        -- i_work_trm  varchar2,
        o_cnt
    );
    
    commit;
end;
/
EOF

echo "======================END `date +%H:%S:%M`==========================="
exit