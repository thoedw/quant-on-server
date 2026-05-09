#!/bin/bash

. ${HOME}/.bash_profile

if [[ -z $DFIFHOME ]] ; then
	BASE=$HOME
else
	BASE=$DFIFHOME
fi


TODAY=`date +%Y%m%d`
NOW=`date +%H`
BINDIR=${BASE}/bin
DEFBIN=${BINDIR}/def
ETCBIN=${BINDIR}/etc


${ETCBIN}/reqAdmTbl -s'HOLIDAY' -fholiday
sleep 1
${DEFBIN}/mdf_adm_req -s'HOLIDAY'

if [[ ${NOW} -ge 15 ]] ; then
sleep 1
${DEFBIN}/mdf_adm_drrpt -s'ACC' -t${TODAY}
${DEFBIN}/mdf_adm_drrpt -s'TRD' -f${TODAY:0:6}01 -t${TODAY}
fi

exit
