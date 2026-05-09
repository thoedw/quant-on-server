#!/bin/bash

. ${HOME}/.bash_profile

if [[ -z ${ITGWHOME} ]] ; then
	BASEDIR=${HOME}
else
	BASEDIR=${ITGWHOME}
fi

SYSDT=`date +%Y%m%d`
TODAY=${SYSDT}
NTIME=`date +%H%M%S`

#LOCAL INFORMATION
RUNDIR=${BASEDIR}/run
BINDIR=${RUNDIR}/bin
DATDIR=${RUNDIR}/data
LOGDIR=${RUNDIR}/log
SHLDIR=${RUNDIR}/script
SFTPDIR=${DATDIR}/sftp
BACKDIR=backup
SCHKFILE=getting.txt
DCHKFILE=done.txt
ECHKFILE=err.txt


#REMOTE INFORMATION
SVRIP=`${BINDIR}/gwcfgmng -m4 -s201`
USERID=`${BINDIR}/gwcfgmng -m4 -s202`
PASSWORD=`${BINDIR}/gwcfgmng -m4 -s203`
SVRDIR=`${BINDIR}/gwcfgmng -m4 -s204`

#FILES TO GET FROM EXCH
TRG_FILES=`gwcfgmng -m4 -s103 | sed 's/,/\n/g'`

SECID=`gwcfgmng -m1 -s101`
RENM_TR="TTSREP10009"

myUsage()
{
        echo $myPnm "[d]"
        echo "     >> -d[date]: date to receive from SFTP. if not entered -d, system date is used."
        exit 100
}

chk_login_info()
{
	if [[ ${USERID} =~ ${SECID} ]]; then
		return 0
	else
		echo "  *************************************************************"
		echo "  ERROR: Need to check USER ID. (SEC_ID:${SECID}, USER_ID:${USERID})"
		echo "  *************************************************************"
		echo
		#return 1
		exit 101
	fi
}

chk_rtn()
{
	#echo $1 ", " $2
	if [ $1 -ne 0 ]	; then
		echo "[$1] fail $2"
		exit 102
	else
		echo "[$1] succcess $2"
	fi
}

wrt_file()
{
	#echo "wrt_file" $#
	if [ $# -ne 2 ]; then
		echo "[$#] usage: wrt_file [file_nm] [contens]"
		return
	fi

cat >> $1 << EOF
$2
EOF
}

mv_logfnm()
{
	#echo "mv_logfnm : $#"

	if [ $# -ne 1 ]; then
		echo "[$#] usage: mv_logfnm [type]"
		echo "     type - 1: mv $SCHKFILE $DCHKFILE"
		echo "            2: mv $SCHKFILE $ECHKFILE"
		return
	fi

	case $1 in
		1) 
			mv $SCHKFILE $DCHKFILE
			;;
		2) 
			mv $SCHKFILE $ECHKFILE
			;;
		*)
			echo "[$1] mv_logfnm: unknow value."
			;;
	esac
}

mv_oldfiles()
{
	echo

	fcnt=`find -maxdepth 1 -type f | wc -l`
	if [ $fcnt -gt 0 ]	; then
		echo "files already exist. [$fcnt]"

		mkdir $BACKDIR
		for fn in `find -maxdepth 1 -type f`
		do
			echo "mv $fn ./${BACKDIR}/$fn.${SYSDT}_${NTIME}"
			mv $fn ./${BACKDIR}/$fn.${SYSDT}_${NTIME}
		done
	fi
}

chg_work_dir()
{
	echo 
	bakchk=0

	#change to base dir
	cd ${SFTPDIR}; rtn=$?; chk_rtn $rtn "chg dir $SFTPDIR"

	#make today dir
	if [ -d ./${TODAY} ] ; then
		echo "already created dir. [$TODAY in `pwd`]"
		bakchk=1
	else
		mkdir ${TODAY}; rtn=$?; chk_rtn $rtn "mkdir $TODAY"
	fi

	cd ${TODAY};rtn=$?; chk_rtn $rtn "chg dir $TODAY"

	#backup old files
	if [ $bakchk -gt 0 ]; then
		mv_oldfiles
	fi

	echo "current dir : " `pwd`
}

#get_data()
#{
#	echo
#
#expect  << EOF
#	set timeout 10
#
#	spawn sftp ${USERID}@${SVRIP}
#	## spawn sftp -oPORT=22 ${USERID}@${SVRIP} ##
#
#	expect "assword:" { send "${PASSWORD}\r"}
#	expect "sftp>" { send "prompt off\r"}
#
#	expect "sftp>" { send "cd ${SVRDIR}\r"}
#	expect "sftp>" { send "ls -ltr *_${TODAY}.txt\r"}
#	expect "sftp>" { send "mget *_${TODAY}.txt\r"}
#
#	expect "sftp>" { send "bye\r"}
#	expect eof
#EOF
#	return $?
#}

get_data()
{
	echo
	echo "SYSDT: ${SYSDSYSDT}"
	echo "BIZDT: ${TODAY}"
	echo "SVRIP: ${SVRIP}"
	echo "USER : ${USERID}"
	echo "PSWD : **********" 
	echo

	chk_login_info

sshpass -p${PASSWORD} sftp -oBatchMode=no -b - ${USERID}@${SVRIP} << EOF
cd ${SVRDIR}/${TODAY}
pwd
ls
mget *.TXT
bye
EOF

}

chk_getfiles()
{
	echo
	mcnt=0

	for fn in $TRG_FILES
	do
		fnm=$fn"_"${TODAY}.TXT
		ls $fnm
		rtn=$?
		if [ $rtn -ne 0 ]; then
			if [ $mcnt -eq 0 ]; then
				wrt_file $SCHKFILE "<<<< MISSING LIST >>>>"
			fi
			wrt_file $SCHKFILE $fnm
			mcnt=`expr $mcnt + 1`
		fi
	done

	if [ $mcnt -gt 0 ]; then
		echo "<<<<<< $mcnt Failed. >>>>>>"
	fi

	return $mcnt
}

rename_tr()
{
	#echo "$FUNCNAME : $1, SECID:$SECID"

	for fn in `find -maxdepth 1 -type f -exec basename {} \;`
	do
	#	echo ${fn} "," ${fn:0:17}
		if [ ${fn:0:17} =  ${1}"_00"${SECID} ]; then
			echo "$FUNCNAME: $fn  ->  ${1}_${TODAY}.TXT"
			cp -p $fn ${1}_${TODAY}.TXT
		fi
	done
}

proc_main()
{
	chg_work_dir

	echo
	echo "create $SCHKFILE"
	wrt_file $SCHKFILE $myPnm":"${SYSDT}_${NTIME} 
	cat $SCHKFILE

	get_data
	rtn=$?
	if [ $rtn -ne 0 ]; then
		case $rtn in
#			127) 
#				emsg="[$rtn] please check [expect package] is installed."
#				;;
			*)
				emsg="[$rtn] get_data() run error."
				;;
		esac

		echo
		echo $emsg
		echo

		wrt_file $SCHKFILE "$emsg"
		mv_logfnm 2
		exit 103
	fi

	echo
	rename_tr $RENM_TR
	chk_getfiles

	mv_logfnm 1
}

myPnm=`basename $0`

while getopts 'hd:' opt ; do
case $opt in
	d)
	TODAY=$OPTARG
	;;
	h)
	myUsage 
	;;
esac
done

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
