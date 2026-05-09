HOME_DIR=`pwd` 
cd $HOME_DIR/LIB_SRC/vncommon
make clean
make
make install
 
cd $HOME_DIR/LIB_SRC/order/lib_ts01
make clean
make
make install
 
cd $HOME_DIR/LIB_SRC/order/lib_ts04
make clean
make
make install
 
cd $HOME_DIR/LIB_SRC/order/lib_ts05
make clean
make
make install
 
cd $HOME_DIR/LIB_SRC/order/lib_ds01
make clean
make
make install
 
cd $HOME_DIR/BANK_SRC/BankLibrary/lib
make clean
make
make install
 
cd $HOME_DIR/BANK_SRC/BankMiddleware/lib/libcom
make clean
make
make install
 
cd $HOME_DIR/BANK_SRC/BankMiddleware/lib/libtool
make clean
make
make install
 
cd $HOME_DIR/xif/lib/util
make clean
make
make install
 
cd $HOME_DIR/xif/lib/fep
make clean
make
make install
 
cd $HOME_DIR/xif/lib/bank
make clean
make
make install
 
cd $HOME_DIR/VSD_SRC/lib/libvsd
make clean
make
make install

