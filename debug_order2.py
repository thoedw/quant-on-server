from vnstock import Vnstock
stock = Vnstock().stock(symbol='FPT', source='VCI')
symbols_list = stock.listing.all_symbols()['symbol'].tolist()
try:
    print("VMG index:", symbols_list.index('VMG'))
    print("VHH index:", symbols_list.index('VHH'))
    print("DFF index:", symbols_list.index('DFF'))
except Exception as e:
    print(e)
