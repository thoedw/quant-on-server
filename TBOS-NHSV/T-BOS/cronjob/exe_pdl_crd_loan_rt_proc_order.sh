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
	for c1 in (
        select acnt_no, sub_no
        from tso01m00
        where sell_buy_tp = '2'
        and accp_tp <> 'X'
        and del_yn = 'N'
        and nmth_qty > 0
    )
    loop
        vn.pdl_crd_loan_rt_proc_order(
            c1.acnt_no,
            c1.sub_no
        );
    end loop;
    
    commit;
end;
/
EOF

echo "======================END `date +%H:%S:%M`==========================="
exit