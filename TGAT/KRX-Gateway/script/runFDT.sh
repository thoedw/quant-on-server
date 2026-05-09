#!/bin/bash

. ${HOME}/.bash_profile

if [[ -z ${ITGWHOME} ]] ; then
        BASEDIR=${HOME}
else
        BASEDIR=${ITGWHOME}
fi

#LOCAL INFORMATION
RUNDIR=${BASEDIR}/run
BINDIR=${RUNDIR}/bin
DATDIR=${RUNDIR}/data
LOGDIR=${RUNDIR}/log
SHLDIR=${RUNDIR}/script
SFTPDIR=${DATDIR}/sftp
INTERVAL=`gwcfgmng -m4 -s205`
SECID=`gwcfgmng -m1 -s101`

SYSDT=`date +%Y%m%d`
NTIME=`date +%H%M%S`

while getopts ':q:b:i' opt ; do
case $opt in
	:)
	EXEMODE=$OPTARG
	if [[ $EXEMODE == 'q' ]] ; then
		fnm=`basename $0`
	
		echo "($0) ==== try to teminate $fnm [$SYSDT $NTIME] ===="
		${SHLDIR}/killbyname -9 $fnm
		exit
	elif [[ $EXEMODE == 'b' ]] ; then
		${SHLDIR}/logmng -m4 -b
		exit
	fi
	;;
	i)
	${SHLDIR}/logmng -m4 -i
	exit
	;;
esac
done

#FDTMMDD=`date +%Y%m%d`
SYSMMDD=`date +%m%d`
TODAY=`date +%Y%m%d`
NHOUR=`date +%-H`
if [[ ${NHOUR} -lt 15 ]] ; then
    if [ `date -d "$TODAY" +%u` -eq 1 ]; then
        SYSMMDD=`date -d "$TODAY - 3 day" +"%m%d"`
    else
        SYSMMDD=`date -d "$TODAY - 1 day" +"%m%d"`
    fi
	TODAY=`date +%Y`$SYSMMDD
fi

FDTMMDD=`grep ${SYSMMDD}":" ${DATDIR}/tbl/sftp_today.tbl | awk -F: '{print $2}' | head -1`

if [[ -z ${FDTMMDD} ]] ; then
	FDTMMDD=$TODAY
fi

echo "($0) [TODAY: $FDTMMDD , INTERVAL: ${INTERVAL} ]"

SCCFLG=0
while :
do
	SYSDT=`date +%Y%m%d`
	NTIME=`date +%H%M%S`

	echo "($0) ==== try to get SFTP data. [$SYSDT $NTIME] ===="
	#${SHLDIR}/getSftpFile.sh  -d ${FDTMMDD} > ${LOGDIR}/getSftpFile.log
	${SHLDIR}/getSftpFile.sh  -d ${FDTMMDD}
	if [ $? -ne 0 ]; then
		sleep ${INTERVAL}
	else
		echo "($0) ==== success to get SFTP data. [$SYSDT $NTIME] ===="
		SCCFLG=1
		break
	fi
done

if [ ${SCCFLG} -eq 1 ]; then

	#push files to server
	${SHLDIR}/pushSFTPtoSvr.sh -d ${FDTMMDD} -sMCI
	${SHLDIR}/pushSFTPtoSvr.sh -d ${FDTMMDD} -sMCI2
	${SHLDIR}/pushSFTPtoSvr.sh -d ${FDTMMDD} -sOMS
	${SHLDIR}/pushSFTPtoSvr.sh -d ${FDTMMDD} -sEBOS
	#${SHLDIR}/pushSFTPtoSvr.sh -d ${FDTMMDD} -sDBOS

	#rename TTSREP10009 TR
	cd ${SFTPDIR}/${FDTMMDD}
	if [ $? -ne 0 ] ; then
		for fn in `find -maxdepth 1 -type f`
		do
			if [ ${fn:0:11} =  "TTSREP10009" ]; then
				echo ${fn:0:11} ":" $fn " -> " $fn_00${SECID}_${FDTMMDD}.TXT
			fi
		done
	fi

	#check status
	FCHK=`ls ${SFTPDIR}/${FDTMMDD}/done.txt | wc -l`

	if [ ${FCHK} -ne 1 ]; then
		echo "($0) ==== not found done.txt file ==== "
	fi
fi


################################################
#done
################################################
