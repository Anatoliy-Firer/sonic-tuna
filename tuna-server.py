import argparse
import asyncio

import uvloop, numpy as np

import src.encoder as encoder
from src.browser import Browser
from src.tun import Tunnel
from src.ws_server import WebSocketServer


async def tun_to_ws(tunnel: Tunnel, ws: WebSocketServer, w: int, h: int):
    async for pack in tunnel.packages():
        encoded = encoder.encode(np.frombuffer(pack, dtype=np.uint8), w, h, 1, 1)
        await ws.push_frame(encoded.tobytes())


async def ws_to_tun(tunnel: Tunnel, ws: WebSocketServer, w: int, h: int):
    async for frame in ws.frames():
        decoded = encoder.decode(np.frombuffer(frame, dtype=np.uint8).reshape((w, h, 3)), w, h, 1, 1)
        if decoded is not None:
            tunnel.push_package(decoded.tobytes())


async def main(args: argparse.Namespace):
    browser = Browser(args.port, args.frame_width, args.frame_height, args.frame_scale, args.fps, args.call_url, args.user)
    tunnel = Tunnel(args.device, args.mtu)
    websocket = WebSocketServer(args.frame_width * args.frame_height * 3, args.port)

    ws_server = asyncio.create_task(websocket.start())

    browser_task = asyncio.create_task(browser.start_browser(not args.show_gui))
    tun_to_ws_task = asyncio.create_task(tun_to_ws(tunnel, websocket, args.frame_width, args.frame_height))
    ws_to_tun_task = asyncio.create_task(ws_to_tun(tunnel, websocket, args.frame_width, args.frame_height))

    try:
        await asyncio.gather(browser_task, tun_to_ws_task, ws_to_tun_task)
    finally:
        try:
            async with asyncio.timeout(10):
                await browser_task
                await tun_to_ws_task
                await ws_to_tun_task
                await ws_server
                await websocket.stop()
        except asyncio.TimeoutError:
            for task in (browser_task, tun_to_ws_task, ws_to_tun_task, ws_server):
                if not task.done():
                    task.cancel()
        await asyncio.gather(browser_task, tun_to_ws_task, ws_to_tun_task, ws_server, return_exceptions=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog="tuna-server", description="Tuna Server")

    parser.add_argument("-d", "--device", default="tuna", help="Virtual interface name")
    parser.add_argument("--frame-width", type=int, default=64, help="Frame width")
    parser.add_argument("--call-url", type=str, default="https://telemost.yandex.ru/j/71720776790697",
                        help="Yandex Telemost conference url")
    parser.add_argument("--frame-height", type=int, default=64, help="Frame height")
    parser.add_argument("--frame-scale", type=int, default=8, help="Frame scale")
    parser.add_argument("--fps", type=int, default=20, help="Frame rate")
    parser.add_argument("-p", "--port", type=int, default=8042, help="Internal websocket port")
    parser.add_argument("--mtu", type=int, default=1400, help="MTU")
    parser.add_argument("--show-gui", action='store_true', help="Show chromium GUI")
    parser.add_argument("--user", type=str, default=None, help="Username at Yandex Telemost conference. Default is current system user")

    uvloop.run(main(parser.parse_args()))
