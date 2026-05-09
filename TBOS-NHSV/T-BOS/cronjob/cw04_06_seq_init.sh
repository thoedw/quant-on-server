#!/bin/sh

echo "======================START `date +%H:%S:%M`==========================="

#Declare variable
. ${HOME}/.bash_profile
BINETC=${HOME}/bin/etc

CONN_STR=`${BINETC}/boscfgmng -m1 -s21`
USER=`${BINETC}/boscfgmng -m1 -s22`
PSWD=`${BINETC}/boscfgmng -m1 -s23`

#Main process
cd /VNST/vnst/cronjob
sqlplus ${USER}/${PSWD}@${CONN_STR} << EOF
	spool cw04_06_seq_init.log;

	@cwd04m00_seq.sql
	@cwd06m00_seq.sql
	@drcwdm06_seq.sql
	@drcwdm22_seq.sql
	@drcwdm09_seq.sql
	@drcwdm11_seq.sql
	@cwd10m00_seq.sql
	@cww02m00_seq.sql
	@tso01m00_seq.sql
	@tso07m00_seq.sql
	@xih03m00_seq.sql
	@xih03m01_seq.sql
	@xih03m10_seq.sql
	@xfx_deal_id_seq.sql
	@xih03m11_seq.sql
	@kfx_accp_no_seq.sql
	@xih03m31_seq.sql
	@xih03m30_seq.sql
	@xih03m12_seq.sql
	@hnx_adv_no_seq.sql
	@hnx_accp_no_seq.sql
	@upcom_accp_no_seq.sql
	@tso01m50_seq.sql
	@cwd11m00_seq.sql
	spool off
EOF

echo "======================END `date +%H:%S:%M`==========================="
exit