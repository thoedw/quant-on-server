#!/bin/sh

. /VNST/vnst/.vnst_profile

sqlplus vn/vn << _EOF_

DECLARE
   BEGIN
          pts_update_stop_order('068');
   END;
/
_EOF_
