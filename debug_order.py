from vnstock import Vnstock
stock = Vnstock().stock(symbol='FPT', source='VCI')
symbols_list = stock.listing.all_symbols()['symbol'].tolist()
print("First 10:", symbols_list[:10])
print("Index 290 to 295:", symbols_list[290:295])
