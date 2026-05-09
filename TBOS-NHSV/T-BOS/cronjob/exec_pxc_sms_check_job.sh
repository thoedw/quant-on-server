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
    vn.pxc_sms_by_proc_tp(
        vn.vwdate,                                                  -- send_dt
        '99991',                                                    -- proc_tp,
        '999',                                                      -- work_tp,
        '999',                                                      -- work_tp,
        'Check list daily all system before start new day, plz!',   -- err_msg,
        'DAILY',                                                    -- work_mn,
         '1.1.1.1'                                                  -- work_trm
        );
    commit;
end;
/
EOF

echo "======================END `date +%H:%S:%M`==========================="
exit