#!/bin/sh

echo "======================START `date +%H:%S:%M`==========================="

#Declare variable
. ${HOME}/.bash_profile
BINETC=${HOME}/bin/etc

CONN_STR=`${BINETC}/boscfgmng -m1 -s21`
USER=`${BINETC}/boscfgmng -m1 -s22`
PSWD=`${BINETC}/boscfgmng -m1 -s23`

echo 'first param: ' $1

#Main process

sqlplus ${USER}/${PSWD}@${CONN_STR} << EOF
declare 
    o_cnt                   NUMBER := 0;
begin

  vn.pdl_backup_acnt_asset_info(vn.vwdate,         --i_dt
                                '$1',              --time_tp
                                '%',               --acnt_no
                                '%',               --sub_no
                                'auto',            --work_mn
                                'SYSTEM',          --work_dtm
                                o_cnt
                               );
    
    commit;
end;
/
EOF

echo "======================END `date +%H:%S:%M`==========================="
exit