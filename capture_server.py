import asyncio

import cv2
import numpy as np
from aiohttp import web
from playwright.async_api import async_playwright, Page, TimeoutError

import encoder

frame_clients = set()
FRAME_WIDTH = 256
FRAME_HEIGHT = 256
FRAME_SIZE_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 3

import os
import fcntl
import struct

# INIT
# sudo ip tuntap add dev tun0 mode tun user jawa
# sudo ip addr add 10.10.42.2/24 dev tun0
# sudo ip link set dev tun0 mtu 1000
# sudo ip link set dev tun0 up

TUNSETIFF = 0x400454ca
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000

tun = os.open("/dev/net/tun", os.O_RDWR)
ifr = struct.pack('16sH', b'tun0', IFF_TUN | IFF_NO_PI)
fcntl.ioctl(tun, TUNSETIFF, ifr)
os.set_blocking(tun, False)


def process_incoming_frame(frame_bytes: bytes):
    if len(frame_bytes) != FRAME_SIZE_BYTES:
        return

    img = np.frombuffer(frame_bytes, dtype=np.uint8).reshape((FRAME_HEIGHT, FRAME_WIDTH, 3))
    cv2.imshow("received-frame", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    cv2.waitKey(1)

    msg = encoder.decode(img)
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
        frame = encoder.encode(payload)
        await broadcast_frame(frame.tobytes())


async def incoming_frames(request):
    ws = web.WebSocketResponse(max_msg_size=FRAME_SIZE_BYTES + 1024)
    await ws.prepare(request)

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY:
                process_incoming_frame(msg.data)
    finally:
        await ws.close()

    return ws


async def handle_route(route):
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
    with open('check_cams.js', 'r') as fin:
        check_cams_js = f"""
        () => {{
          {fin.read()}  
        }};
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

    await page.wait_for_function(check_cams_js, timeout=60000)
    await page.evaluate(rtc_client_js)


async def start_browser():
    async with async_playwright() as p:
        page = None
        browser = None
        try:
            with open('camera_bridge.js', 'r') as fin:
                camera_bridge_js = fin.read()

            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--use-fake-ui-for-media-stream",
                    "--use-fake-device-for-media-stream"
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
        except Exception as e:
            print(e)
        finally:
            if page is not None:
                await page.close()
            if browser is not None:
                await browser.close()
            exit(0)


async def main():
    app = web.Application()
    app.router.add_get("/frame-stream", frame_stream)
    app.router.add_get("/incoming-frames", incoming_frames)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, 'localhost', 8000)
    await site.start()

    asyncio.create_task(start_browser())
    asyncio.create_task(iptun_to_frames())
    asyncio.get_event_loop().add_reader(tun, read_ippack)

    await asyncio.Event().wait()


if __name__ == '__main__':
    asyncio.run(main())
