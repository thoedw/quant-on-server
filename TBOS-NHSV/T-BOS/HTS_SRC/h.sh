cd /user/stock/src/tux/tuxq
make clean
make
make install
cd /user/stock/src/dso/ac/OrderParams.SRC
make clean
make
cd /user/stock/src/dso/ac/SearchOrderParams.SRC
make clean
make
cd /user/stock/src/dss/ac/StockOrders.SRC
make clean
make
make install
cd /user/stock/run/tuxps
./tux -a
pmcmd init

