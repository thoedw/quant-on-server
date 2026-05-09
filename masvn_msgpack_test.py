import asyncio
import websockets
import msgpack

async def test_ws():
    uri = "wss://mastrade.masvn.com/ws/"
    async with websockets.connect(uri) as ws:
        # Handshake
        handshake = msgpack.packb({"e": ["#handshake", {"authToken": None}, 1]})
        await ws.send(handshake)
        print("Sent handshake")
        
        # Subs
        sub_msg = msgpack.packb({"e": ["#subscribe", {"channel": "market.init"}, 2]})
        await ws.send(sub_msg)
        print("Sent market.init subscribe")
        
        for _ in range(5):
            res = await ws.recv()
            if isinstance(res, bytes):
                try:
                    data = msgpack.unpackb(res, strict_map_key=False)
                    print(data)
                except Exception as e:
                    print(e)
            else:
                print("String frame:", res)

asyncio.run(test_ws())
