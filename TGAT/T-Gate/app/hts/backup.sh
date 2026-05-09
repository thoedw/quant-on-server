#!/bin/ksh


DATE=`date +%m%d`
SRCNAME=htssrc.tar.$DATE.Z
BINNAME=htsbin.tar.$DATE.Z

SRCDIR=/VNST/deve/source/BIN_SRC/HTS
BINDIR=/VNST/vnst/hts

cd $SRCDIR
/bin/rm -f hts*Z
tar cvf - * | compress > $SRCNAME

cd $BINDIR
tar cvf - * | compress > $BINNAME
mv $BINNAME $SRCDIR/.

exit

