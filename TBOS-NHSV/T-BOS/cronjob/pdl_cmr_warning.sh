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
    vn.pdl_calc_cmr_by_cur_pri(
        '%',        -- i_acnt_no           varchar2,
        '%',        -- i_sub_no            varchar2,
        '%',        -- i_mrgn_acnt_grp     varchar2,
        '%',        -- i_basket_cd         varchar2,
        '%',        -- i_branch_cd         varchar2,
        '%',        -- i_acnt_grp_tp       varchar2
        'DAILY',    -- i_work_mn           varchar2,
        'SYSTEM'    -- i_work_trm          varchar2
    );
    
    vn.pdl_cmr_warning(
        '%',            -- i_tp        varchar2,   -- 1: cmr < lmr     2: cmr < fmr    %: both
        vn.vwdate,      -- i_dt        varchar2,
        '%',            -- i_acnt_no   varchar2    default '%',
        '%',            -- i_sub_no    varchar2    default '%',
        'DAILY',        -- i_work_mn   varchar2,
        'SYSTEM'        -- i_work_trm  varchar2
    );
    
    commit;
end;
/
EOF

echo "======================END `date +%H:%S:%M`==========================="
exit