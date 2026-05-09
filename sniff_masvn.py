import asyncio
import json
from playwright.async_api import async_playwright

async def sniff():
    print("Launching playwright to sniff MASVN WebSockets...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Listen to websocket connections and frames
        def on_websocket(ws):
            print(f"WebSocket created: {ws.url}")
            
            def on_frame(frame):
                payload = frame
                if len(payload) < 200:
                    print(f"[{ws.url}] Frame sent: {payload}")
                elif "event" in payload:
                    print(f"[{ws.url}] Frame sent (large event)")

            def on_frame_received(frame):
                payload = frame
                try:
                    data = json.loads(payload)
                    if isinstance(data, dict):
                        if data.get('event') == '#publish':
                            print(f">>> PUBLISH: {str(payload)[:100]}")
                        else:
                            print(f"[{ws.url}] Frame RECV: {str(payload)[:300]}")
                    else:
                        print(f"[{ws.url}] Frame RECV: {str(payload)[:100]}")
                except:
                    print(f"[{ws.url}] Frame RECV: {str(payload)[:100]}")

            ws.on('framesent', on_frame)
            ws.on('framereceived', on_frame_received)

        page.on('websocket', on_websocket)
        print("Navigating to https://mastrade.masvn.com/board/vn30")
        await page.goto("https://mastrade.masvn.com/board/vn30", wait_until="networkidle")
        
        # Keep waiting to collect data
        print("Waiting 10s for data...")
        await asyncio.sleep(10)
        await browser.close()

asyncio.run(sniff())
