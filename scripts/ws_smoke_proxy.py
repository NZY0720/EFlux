"""WS smoke test via Vite proxy (port 5173)."""

import asyncio
import json

from websockets.asyncio.client import connect


async def main():
    async with connect("ws://localhost:5173/ws/market") as ws:
        for i in range(5):
            d = json.loads(await ws.recv())
            print(f"[{i + 1}] {d['kind']:<20} sim_ts={d['sim_ts']}")


if __name__ == "__main__":
    asyncio.run(main())
