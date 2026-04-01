import argparse
import asyncio
import struct

import numpy as np
import uvloop

import src.encoder as encoder
from src.browser import Browser
from src.tun import Tunnel
from src.util.batch_generator import chunk_from_queue
from src.ws_server import WebSocketServer


def serialize_arrays(arrays):
    packed = b""
    for arr in arrays:
        packed += struct.pack("I", len(arr)) + arr
    return packed


def deserialize_arrays(buffer):
    arrays = []
    offset = 0
    while offset < len(buffer):
        size = struct.unpack_from("I", buffer, offset)[0]
        offset += 4
        arr = np.frombuffer(buffer, dtype=np.uint8, count=size, offset=offset)
        arrays.append(arr.tobytes())
        offset += size
    return arrays


async def tun_to_ws(tunnel: Tunnel, ws: WebSocketServer, w: int, h: int, fps: int):
    __max_size = (w - 2) * (h - 2) * 3 // 8 - 8  # максимальная длина массива байт, принимаемого функцией encoder.encode
    __timeout = 1.0 / (fps * 2)

    predicate = lambda count, length: length + count * 8 < __max_size

    async for packs in chunk_from_queue(tunnel.packages(), predicate, __timeout):
        encoded = encoder.encode(np.frombuffer(serialize_arrays(packs), dtype=np.uint8), w, h, 1, 1)
        await ws.push_frame(encoded.tobytes())


async def ws_to_tun(tunnel: Tunnel, ws: WebSocketServer, w: int, h: int):
    async for frame in ws.frames():
        decoded = encoder.decode(np.frombuffer(frame, dtype=np.uint8).reshape((w, h, 3)), w, h, 1, 1)
        if decoded is not None:
            for pack in deserialize_arrays(decoded):
                tunnel.push_package(pack)


async def main(args: argparse.Namespace):
    browser = Browser(args.port, args.frame_width, args.frame_height, args.frame_scale, args.fps, args.call_url,
                      args.user)
    tunnel = Tunnel(args.device, args.mtu)
    websocket = WebSocketServer(args.frame_width * args.frame_height * 3, args.port)

    ws_server = asyncio.create_task(websocket.start())

    browser_task = asyncio.create_task(browser.start_browser(not args.show_gui))
    tun_to_ws_task = asyncio.create_task(tun_to_ws(tunnel, websocket, args.frame_width, args.frame_height, args.fps))
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
    parser.add_argument("--user", type=str, default=None,
                        help="Username at Yandex Telemost conference. Default is current system user")

    uvloop.run(main(parser.parse_args()))
