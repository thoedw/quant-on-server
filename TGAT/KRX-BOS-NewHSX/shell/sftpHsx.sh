#!/bin/bash

. ${HOME}/.bash_profile

if [[ -z $DFIFHOME ]] ; then
	BASE=$HOME
else
	BASE=$DFIFHOME
fi

BINDIR=${BASE}/bin
DATDIR=${BASE}/data
ETCBIN=${BINDIR}/etc
SFTPDIR=${DATDIR}/sftp
SHLDIR=${BINDIR}/shell
DCHKFILE=done.txt

SYSDT=`date +%Y%m%d`
NTIME=`date +%H%M%S`
RETRY_TIME=60

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
		${SHLDIR}/logmng -m7 -d -b
		exit
	fi
	;;
	i)
	${SHLDIR}/logmng -m7 -d -i
	exit
	;;
esac
done

echo "Today: $SYSDT"

FDTDT=`date +%Y%m%d`
NHOUR=`date +%-H`
if [[ ${NHOUR} -lt 15 ]] ; then
    if [ `date -d "$FDTDT" +%u` -eq 1 ]; then
        FDTDT=`date -d "$FDTDT - 3 day" +"%Y%m%d"`
    else
        FDTDT=`date -d "$FDTDT - 1 day" +"%Y%m%d"`
    fi
fi
echo "FdtDay: $FDTDT"

FILE_TO_WAIT_FOR="$SFTPDIR/hsx/$FDTDT/$DCHKFILE"

until [ -s "$FILE_TO_WAIT_FOR" ]; do
    sleep $RETRY_TIME
done

if [ -s "$FILE_TO_WAIT_FOR" ]; then
    echo "$FILE_TO_WAIT_FOR found. Processing..."
    ${ETCBIN}/procFdtHsx -d$FDTDT
    echo "Done"
else
    echo "Timeout reached. $FILE_TO_WAIT_FOR doesn't exist."
    exit 1
fi
