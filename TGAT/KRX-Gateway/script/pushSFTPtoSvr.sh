#!/bin/bash

. ${HOME}/.bash_profile

if [[ -z ${ITGWHOME} ]] ; then
        BASEDIR=${HOME}
else
        BASEDIR=${ITGWHOME}
fi

RUNDIR=${BASEDIR}/run
BINDIR=${RUNDIR}/bin
DATDIR=${RUNDIR}/data
SFTPDIR=${DATDIR}/sftp


SYSDT=`date +%Y%m%d`
TODAY=${SYSDT}
NTIME=`date +%H%M%S`

DCHKFILE=done.txt

myPnm=`basename $0`

myUsage()
{
	echo
        echo $myPnm "[ds]"
        echo "     >> -s[server]: Server name to push sftp. one of MCI, MCI2, EBOS, DBOS and OMS"
        echo "     >> -d[date]: date to receive from SFTP. if not entered -d, system date is used."
	echo
	echo

        exit 100
}

set_svrinfo()
{
	if   [[ ${SVRNM} == 'MCI' ]]; then
		DEST_IP=`${BINDIR}/gwcfgmng -m4 -s301`
		USERID=`${BINDIR}/gwcfgmng -m4 -s302`
		PASSWORD=`${BINDIR}/gwcfgmng -m4 -s303`
		DEST_DIR=`${BINDIR}/gwcfgmng -m4 -s304`
	elif   [[ ${SVRNM} == 'EBOS' ]]; then
		DEST_IP=`${BINDIR}/gwcfgmng -m4 -s305`
		USERID=`${BINDIR}/gwcfgmng -m4 -s306`
		PASSWORD=`${BINDIR}/gwcfgmng -m4 -s307`
		DEST_DIR=`${BINDIR}/gwcfgmng -m4 -s308`
	elif   [[ ${SVRNM} == 'DBOS' ]]; then
		DEST_IP=`${BINDIR}/gwcfgmng -m4 -s309`
		USERID=`${BINDIR}/gwcfgmng -m4 -s310`
		PASSWORD=`${BINDIR}/gwcfgmng -m4 -s311`
		DEST_DIR=`${BINDIR}/gwcfgmng -m4 -s312`
	elif   [[ ${SVRNM} == 'OMS' ]]; then
		DEST_IP=`${BINDIR}/gwcfgmng -m4 -s313`
		USERID=`${BINDIR}/gwcfgmng -m4 -s314`
		PASSWORD=`${BINDIR}/gwcfgmng -m4 -s315`
		DEST_DIR=`${BINDIR}/gwcfgmng -m4 -s316`
	elif   [[ ${SVRNM} == 'MCI2' ]]; then
		DEST_IP=`${BINDIR}/gwcfgmng -m4 -s317`
		USERID=`${BINDIR}/gwcfgmng -m4 -s318`
		PASSWORD=`${BINDIR}/gwcfgmng -m4 -s319`
		DEST_DIR=`${BINDIR}/gwcfgmng -m4 -s320`
	else
		myUsage
	fi
}

chk_dest_dir()
{
        echo
        echo "FUNC : CHK_DEST_DIR"
        echo "BIZDT : ${TODAY}"
        echo "DESTIP: ${DEST_IP}"
        echo "USER  : ${USERID}"
        echo "PSWD  : **********" 
        echo

	if [[ $PASSWORD == "-" ]]; then
        echo "Verify dest dir using public key."
        echo
		
sftp -oBatchMode=no -b - ${USERID}@${DEST_IP} << EOF
cd ${DEST_DIR}/${TODAY}
EOF

	else
        echo "Verify dest dir using password."
        echo
		
sshpass -p${PASSWORD} sftp -oBatchMode=no -b - ${USERID}@${DEST_IP} << EOF
cd ${DEST_DIR}/${TODAY}
EOF

	fi

}

rm_olddir()
{
        echo
        echo "FUNC : RM_OLDDIR"
        echo "BIZDT : ${TODAY}"
        echo "DESTIP: ${DEST_IP}"
        echo "USER  : ${USERID}"
        echo "PSWD  : **********" 
        echo

REMOTE_PATH=${DEST_DIR}/${TODAY}

	if [[ $PASSWORD == "-" ]]; then
        echo "Remove old dir using public key."
        echo

# 1. Remove all files within the target directory
file_list=$(sftp -o BatchMode=no -b - ${USERID}@${DEST_IP} << EOF
    cd "$REMOTE_PATH"
    ls -1
EOF
)
cleaned_file_list=$(echo "$file_list" | egrep -v 'sftp>')
while read -r file; do
sftp -o BatchMode=no -b - ${USERID}@${DEST_IP} << EOF
    rm "$REMOTE_PATH/$file"
EOF
done <<< "$cleaned_file_list"

# 2. Remove the main target directory
sftp -o BatchMode=no -b - ${USERID}@${DEST_IP} << EOF
    rmdir "$REMOTE_PATH"
EOF

	else
        echo "Remove old dir using password."
        echo
		
# 1. Remove all files within the target directory
file_list=$(sshpass -p${PASSWORD} sftp -o BatchMode=no -b - ${USERID}@${DEST_IP} << EOF
    cd "$REMOTE_PATH"
    ls -1
EOF
)
cleaned_file_list=$(echo "$file_list" | egrep -v 'sftp>')
while read -r file; do
sshpass -p${PASSWORD} sftp -o BatchMode=no -b - ${USERID}@${DEST_IP} << EOF
    rm "$REMOTE_PATH/$file"
EOF
done <<< "$cleaned_file_list"

# 2. Remove the main target directory
sshpass -p${PASSWORD} sftp -o BatchMode=no -b - ${USERID}@${DEST_IP} << EOF
    rmdir "$REMOTE_PATH"
EOF

	fi

}

push_data()
{     
        echo
        echo "FUNC : PUSH_DATA"
        echo "BIZDT : ${TODAY}"
        echo "DESTIP: ${DEST_IP}"
        echo "USER  : ${USERID}"
        echo "PSWD  : **********" 
        echo

	if [[ $PASSWORD == "-" ]]; then
        echo "Push files using public key."
        echo
		
sftp -oBatchMode=no -b - ${USERID}@${DEST_IP} << EOF
cd ${DEST_DIR}
mkdir ${TODAY}
cd ${TODAY}
pwd
ls
mput *.TXT
mput *.txt
bye
EOF

	else
        echo "Push files using password."
        echo
		
sshpass -p${PASSWORD} sftp -oBatchMode=no -b - ${USERID}@${DEST_IP} << EOF
cd ${DEST_DIR}
mkdir ${TODAY}
cd ${TODAY}
pwd
ls
mput *.TXT
mput *.txt
bye
EOF

	fi

}


proc_main()
{
	#change to base dir
	cd ${SFTPDIR}/${TODAY};
	rtn=$?
        if [ $rtn -ne 0 ] ; then
                echo "[$rtn] fail change dir. ($SFTPDIR)"
                exit 101
        fi

	pwd

	ls ${DCHKFILE}
	rtn=$?
        if [ $rtn -ne 0 ] ; then
		echo
                echo "[$rtn] there is no ${DCHKFILE}. need to check sftp data."
                exit 102
        fi

	echo "done.txt: " $rtn

	chk_dest_dir
	rtn=$?
	if [ $rtn -eq 0 ]; then
		rm_olddir
	fi
	
	push_data
}

while getopts 'hd:s:' opt ; do
case $opt in
	d)
	TODAY=$OPTARG
	;;
	s)
	SVRNM=$OPTARG
	;;
	h)
        myUsage
        ;;
esac
done

set_svrinfo
echo "<<<DEST CONNECTION INFO>>>"
echo "SVRNM  : ${SVRNM}"
echo "SYSDT  : ${SYSDT}"
echo "BIZDT  : ${TODAY}"
echo "DEST_IP: ${DEST_IP}"
echo "USER   : ${USERID}"

echo
echo "---------------------------------------"
echo " START [$0:$SYSDT"_"$NTIME] (TODAY:${TODAY}) "
echo "---------------------------------------"
echo

proc_main

echo
echo "---------------------------------------"
echo " END [$0:$SYSDT"_"$NTIME] "
echo "---------------------------------------"
echo
