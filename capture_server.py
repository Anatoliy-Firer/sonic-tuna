import uvloop

uvloop.install()

import asyncio
import contextlib
import traceback
from urllib.parse import parse_qs

import numpy as np
from aiohttp import web
from playwright.async_api import async_playwright, Page, TimeoutError

import encoder

frame_clients = set()
FRAME_WIDTH = 256
FRAME_HEIGHT = 256
BLOCK_SIZE = 2
FRAME_SIZE_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 3

import os
import fcntl
import struct

# INIT
# sudo ip tuntap add dev tun0 mode tun user jawa
# sudo ip addr add 10.10.42.2/24 dev tun0
# sudo ip link set dev tun0 mtu 1400
# sudo ip link set dev tun0 up

TUNSETIFF = 0x400454ca
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000

tun = os.open("/dev/net/tun", os.O_RDWR)
ifr = struct.pack('16sH', b'tun0', IFF_TUN | IFF_NO_PI)
fcntl.ioctl(tun, TUNSETIFF, ifr)
os.set_blocking(tun, False)


def process_incoming_frame(frame_bytes: bytes, source_id: str):
    if len(frame_bytes) != FRAME_SIZE_BYTES:
        return

    img = np.frombuffer(frame_bytes, dtype=np.uint8).reshape((FRAME_HEIGHT, FRAME_WIDTH, 3))

    msg = encoder.decode(img, block_size=BLOCK_SIZE)
    if msg is not None:
        os.write(tun, msg)


async def broadcast_frame(frame_bytes: bytes):
    stale_clients = []
    for client in frame_clients:
        if client.closed:
            stale_clients.append(client)
            continue
        try:
            await client.send_bytes(frame_bytes)
        except Exception:
            stale_clients.append(client)

    for client in stale_clients:
        frame_clients.discard(client)


async def frame_stream(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    frame_clients.add(ws)

    try:
        async for _ in ws:
            pass
    finally:
        frame_clients.discard(ws)

    return ws


ip_queue = asyncio.Queue()


def read_ippack():
    try:
        line = os.read(tun, 2048)
        ip_queue.put_nowait(line)
    except EOFError as err:
        print(err)
        return


async def iptun_to_frames():
    while True:
        line = await ip_queue.get()
        payload = np.frombuffer(line, dtype=np.uint8)
        frame = encoder.encode(payload, block_size=BLOCK_SIZE)
        await broadcast_frame(frame.tobytes())


async def incoming_frames(request):
    ws = web.WebSocketResponse(max_msg_size=FRAME_SIZE_BYTES + 1024)
    await ws.prepare(request)
    source_id = parse_qs(request.rel_url.query_string).get("source_id", ["unknown"])[0]

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY:
                process_incoming_frame(msg.data, source_id)
    finally:
        await ws.close()

    return ws


async def handle_route(route):
    # CSP влияет на документ; остальные ресурсы не нужно проксировать через fetch/fulfill.
    if route.request.resource_type != "document":
        await route.continue_()
        return

    response = await route.fetch()
    headers = dict(response.headers)

    headers.pop("content-security-policy", None)
    headers.pop("content-security-policy-report-only", None)

    await route.fulfill(
        response=response,
        headers=headers
    )


async def work_with_page(page: Page):
    with open('up_webrtc.js', 'r') as fin:
        rtc_client_js = f"""
        (async () => {{
          {fin.read()}  
        }})();
        """

    # page.on("console", lambda msg: print("BROWSER LOG:", msg.text))

    await page.goto("https://telemost.yandex.ru/j/71720776790697")

    try:
        but_continue = page.get_by_text("Продолжить в браузере")
        await but_continue.wait_for(timeout=3000)
        await but_continue.click()
    except TimeoutError:
        pass

    but_mic = page.get_by_title("Выключить микрофон")
    await but_mic.wait_for()
    await but_mic.click()

    but_connect = page.get_by_text("Подключиться")
    await but_connect.wait_for()
    await but_connect.click()

    await page.evaluate(rtc_client_js)


async def start_browser():
    async with async_playwright() as p:
        page = None
        browser = None
        context = None
        try:
            with open('camera_bridge.js', 'r') as fin:
                camera_bridge_js = fin.read()

            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--use-fake-ui-for-media-stream",
                    "--use-fake-device-for-media-stream",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-sync",
                    "--disable-extensions",
                    "--disable-features=Translate,OptimizationHints",
                    "--disk-cache-size=1",
                    "--media-cache-size=33554432"
                ]
            )

            context = await browser.new_context(
                permissions=['camera', 'microphone']
            )
            await context.add_init_script(script=camera_bridge_js)

            await context.route("**/*", handle_route)

            page = await context.new_page()
            await work_with_page(page)
            # держим браузер живым
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            traceback.print_exc()
        finally:
            if page is not None:
                with contextlib.suppress(Exception):
                    await page.close()
            if context is not None:
                with contextlib.suppress(Exception):
                    await context.close()
            if browser is not None:
                with contextlib.suppress(Exception):
                    await browser.close()


async def main():
    app = web.Application()
    app.router.add_get("/frame-stream", frame_stream)
    app.router.add_get("/incoming-frames", incoming_frames)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, 'localhost', 8000)
    await site.start()

    browser_task = asyncio.create_task(start_browser(), name="start_browser")
    iptun_task = asyncio.create_task(iptun_to_frames(), name="iptun_to_frames")
    loop = asyncio.get_running_loop()
    loop.add_reader(tun, read_ippack)

    try:
        await asyncio.gather(browser_task, iptun_task)
    finally:
        loop.remove_reader(tun)
        for task in (browser_task, iptun_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(browser_task, iptun_task, return_exceptions=True)
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
