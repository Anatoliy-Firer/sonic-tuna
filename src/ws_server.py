import asyncio
from contextlib import AbstractContextManager

from aiohttp import web


class WebSocketServer(AbstractContextManager):
    """Класс реализует вебсокет для общения с клиентской частью в браузере"""

    def __exit__(self, exc_type, exc_value, traceback, /):
        self.stop()

    def __init__(self, frame_size: int, port: int):
        self.__port = port
        self.__frame_size = frame_size
        self.__frame_clients = set()
        self.__incoming_frames = asyncio.Queue()

        app = web.Application()
        app.router.add_get("/frame-stream", self.frame_stream)

        self.__runner = web.AppRunner(app)

    async def start(self):
        await self.__runner.setup()

        site = web.TCPSite(self.__runner, 'localhost', self.__port)
        await site.start()

    async def stop(self):
        await self.__runner.cleanup()

    async def frames(self):
        while True:
            yield await self.__incoming_frames.get()

    async def push_frame(self, frame):
        stale_clients = set()
        for client in self.__frame_clients:
            if client.closed:
                stale_clients.add(client)
                continue
            try:
                await client.send_bytes(frame)
            except Exception as e:
                print(f"Error {e}")
                stale_clients.add(client)

        self.__frame_clients.difference_update(stale_clients)

    async def frame_stream(self, request):
        ws = web.WebSocketResponse(max_msg_size=self.__frame_size + 1024)
        await ws.prepare(request)

        self.__frame_clients.add(ws)

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.BINARY:
                    self.__incoming_frames.put_nowait(msg.data)
        finally:
            self.__frame_clients.discard(ws)
            await ws.close()

        return ws
