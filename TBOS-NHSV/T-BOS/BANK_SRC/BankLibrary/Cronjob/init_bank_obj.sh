#!/usr/bin/ksh

TODAY=`date +%Y%m%d`

. /VNST/vnst/.vnst_profile

echo ""
echo "================================================="
echo "==        START BANK BATCH ($(TODAY))          =="
echo "================================================="
echo ""


cd /VNST/vnst/cronjob

sqlplus vn/vn@vnst << _EOF_

DECLARE

	CNT		NUMBER;

BEGIN
        VN.PCW_BANK_TRANS_DT_CRET_P(TO_CHAR(SYSDATE, 'YYYYMMDD'), 'DAILY', CNT);
        COMMIT;
END;
/

DROP SEQUENCE CWW02M00_SEQ;

CREATE SEQUENCE CWW02M00_SEQ
        START WITH 1
        INCREMENT BY 1;

GRANT SELECT ON CWW02M00_SEQ TO PUBLIC;

/

DROP SEQUENCE CWW02M10_SEQ;

CREATE SEQUENCE CWW02M10_SEQ
        START WITH 1
        INCREMENT BY 1;

GRANT SELECT ON CWW02M10_SEQ TO PUBLIC;

/

DROP SEQUENCE CWW02M20_SEQ;

CREATE SEQUENCE CWW02M20_SEQ
        START WITH 1
        INCREMENT BY 1;

GRANT SELECT ON CWW02M20_SEQ TO PUBLIC;

/

_EOF_

echo ""
echo "================================================="
echo "==         END  BANK BATCH ($(TODAY))          =="
echo "================================================="
echo ""


exit
