#!/bin/sh

. /VNST/vnst/.vnst_profile

find /VNST/log/xif/fep.log   -exec   rm -rf {} \;
find /VNST/log/xif/ha.log   -exec   rm -rf {} \;
find /VNST/log/xif/upcom.log   -exec   rm -rf {} \;
find /VNST/log/etc/bank.log   -exec   rm -rf {} \;
find /VNST/log/etc/error.log   -exec   rm -rf {} \;
find /VNST/log/etc -type f -mtime +30 -exec rm -rf {} \;
find /VNST/log/xif -type f -mtime +15 -exec rm -rf {} \;
