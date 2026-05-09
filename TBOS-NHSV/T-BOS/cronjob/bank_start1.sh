. /VNST/vnst/.vnst_profile

cd /VNST/vnst/bin/bank1/bin

./BankMiddleware -s &

sleep 1

cp BankProcessor Bank_BIDV

./Bank_BIDV 0003 -s &

sleep 1

cp BankProcessor Bank_VCB
./Bank_VCB 0002 -s &

