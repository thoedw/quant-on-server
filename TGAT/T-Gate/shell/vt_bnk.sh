#!/bin/bash

. ${HOME}/.bash_profile

if [[ -z $DFIFHOME ]] ; then
	BASE=$HOME
else
	BASE=$DFIFHOME
fi


SHLDIR=${BASE}/bin/shell
source ${SHLDIR}/holiopt

BINDIR=${BASE}/bin/fep
pid=$$

#
# kill vt_bnk_*
#
vt_bnk_stop()
{
	$BINDIR/virtualBidv -q
	$BINDIR/virtualVcb -q
	$BINDIR/virtualDab -q
}

#
# run vt_bnk_*
#
vt_bnk_start()
{
	$BINDIR/virtualBidv
	$BINDIR/virtualVcb
	$BINDIR/virtualDab
}

#
# print vt_bnk_* info
#
vt_bnk_print()
{
	echo "------------------------------------------------------------------------"
	echo "  Program           Start Time          PID           Status "
	echo "------------------------------------------------------------------------"
	$BINDIR/virtualBidv -s
	$BINDIR/virtualVcb -s
	$BINDIR/virtualDab -s
}


#
# main
#


case $1 in
'stop')
	vt_bnk_stop
;;
'info')
	vt_bnk_print
;;
*)
	vt_bnk_start
;;
esac

exit



