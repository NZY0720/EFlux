"""Quick WS smoke test — connect, read 5 events, exit."""

import asyncio
import json

from websockets.asyncio.client import connect


async def main():
    async with connect("ws://127.0.0.1:8000/ws/market") as ws:
        for i in range(5):
            msg = await ws.recv()
            data = json.loads(msg)
            print(f"[{i + 1}] kind={data['kind']:<20} sim_ts={data.get('sim_ts')}")


if __name__ == "__main__":
    asyncio.run(main())
