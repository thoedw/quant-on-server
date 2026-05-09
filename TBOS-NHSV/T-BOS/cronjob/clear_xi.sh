#!/bin/sh

. /VNST/vnst/.vnst_profile

find /VNST/log/xif -type f \( -name "xierrlog*" -o -name "xifprcvl31003*" \) -mtime +0 -exec rm -f {} \;
