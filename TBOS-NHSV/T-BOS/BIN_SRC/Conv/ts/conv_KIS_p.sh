#!/usr/bin/ksh

. /VNST/vnst/.profile

cd /VNST/deve/source/BIN_SRC/Conv/ts

RUN_PATH=/VNST/deve/source/BIN_SRC/Conv/ts

#main
	
	find /VNST/deve/source/BIN_SRC/Conv/ts/*.log   -exec   rm -rf {} \;	
	${RUN_PATH}/conv_KIS_tso01h00 20110524 99999999  >> //VNST/deve/source/BIN_SRC/Conv/ts/tso01h00.log
	#${RUN_PATH}/conv_KIS_tso01h10 20110524 99999999  >> //VNST/deve/source/BIN_SRC/Conv/ts/tso01h10.log
