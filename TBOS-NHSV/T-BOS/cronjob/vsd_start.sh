#!/bin/sh

. /VNST/vnst/.vnst_profile

cd /VNST/vnst/cronjob

RUN_PATH=/VNST/vnst/bin/vsd/bin/

cd ${RUN_PATH}
	def_vsd -s N >> /VNST/vnst/bin/vsd/log/all.log &
    def_vsd -s E >> /VNST/vnst/bin/vsd/log/all.log &
