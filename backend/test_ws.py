import asyncio
import websockets

async def test():
    uri = "ws://localhost:8000/api/v1/voice/stream"
    try:
        async with websockets.connect(uri) as websocket:
            print(f"Connected to {uri}")
            await websocket.send("hello")
            response = await websocket.recv()
            print(f"Received: {response}")
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(test())
