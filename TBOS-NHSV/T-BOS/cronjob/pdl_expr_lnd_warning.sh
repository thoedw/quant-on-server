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
	vn.pdl_expr_lnd_warning(
        '70',       -- i_lnd_tp    varchar2,
        '%',        -- i_acnt_no   varchar2    default '%',
        '%',        -- i_sub_no    varchar2    default '%',
        'DAILY',    -- i_work_mn   varchar2,
        '1.1.1.1'   -- i_work_trm  varchar2
    );
    
    vn.pdl_expr_lnd_warning(
        '80',       -- i_lnd_tp    varchar2,
        '%',        -- i_acnt_no   varchar2    default '%',
        '%',        -- i_sub_no    varchar2    default '%',
        'DAILY',    -- i_work_mn   varchar2,
        '1.1.1.1'   -- i_work_trm  varchar2
    );
    
    commit;
end;
/
EOF

echo "======================END `date +%H:%S:%M`==========================="
exit