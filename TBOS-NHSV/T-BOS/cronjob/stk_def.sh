#!/bin/ksh

. /VNST/vnst/.vnst_profile

BINDIR=/VNST/vnst/bin/def
TODAY=`date +%Y%m%d`
HOLITBL='/VNST/vnst/hts/data/download/holiday.tbl'

HOLIDAY=`awk '{print $1}' ${HOLITBL} | grep ${TODAY}`

pid=$$

#
# kill stk_*
#
stk_stop()
{
	if [ -n $HOLIDAY ]; then
		echo "@@ today is holiday"
		exit
	fi

#	$BINDIR/ha_stkinfo -q
#	/VNST/vnst/scripts/killbyname ho_stkinfo_get
	$BINDIR/ho_stkinfo -q
	$BINDIR/ho_tp -q
	$BINDIR/ho_ls -q
	sleep 1
	$BINDIR/def_log.sh
}

#
# init tbl & memory $ data
#
stk_init()
{

	if [ -n $HOLIDAY ]; then
		echo "@@ today is holiday"
		exit
	fi

#	$BINDIR/ha_init.sh
#	sleep 1
	$BINDIR/ho_stk_init
#	sleep 1
	$BINDIR/sndHoli.sh
}


#
# run stk_*
#
stk_start()
{
	if [ -n $HOLIDAY ]; then
		echo "@@ today is holiday"
		exit
	fi

#	$BINDIR/ha_stkinfo
#	$BINDIR/ho_stkinfo_get&
   $BINDIR/ho_stkinfo
   $BINDIR/ho_tp
   $BINDIR/ho_ls
}

#
# main
#

case $1 in
'init')
	stk_init
	;;
'stop')
	stk_stop
	;;
'start')
	stk_start
	;;
*)
	echo "Wrong Argument!!"
	;;
esac

exit

