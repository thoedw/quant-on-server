#!/bin/ksh

. /VNST/vnst/.vnst_profile

# changed due to HoSE connection test (2008.08.14)
#/VNST/vnst/bin/def/ha_stockinfo &
#/VNST/vnst/bin/def/ho_stockinfo &

#/VNST/vnst/bin/def/ha_stockinfo > /dev/null 2>&1
#/VNST/vnst/bin/def/ho_stockinfo > /dev/null 2>&1

# added for HoSE direct access test
/VNST/deve/source/BIN_SRC/def/ss/ho_stockinfo_2 > /dev/null 2>&1 &
