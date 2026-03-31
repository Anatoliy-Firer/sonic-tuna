from contextlib import AbstractContextManager

import asyncio
import contextlib
import traceback
import getpass

from playwright.async_api import async_playwright, Page, TimeoutError


class Browser(AbstractContextManager):

    def __exit__(self, exc_type, exc_value, traceback, /):
        pass

    def __init__(self, port: int, frame_w, frame_h, scale, fps: int, call_url: str, user: str = None):
        self.__port = port
        self.__frame_w = frame_w
        self.__frame_h = frame_h
        self.__scale = scale
        self.__fps = fps
        self.__call_url = call_url
        self.__user = user or getpass.getuser()

    async def start_browser(self, headless: bool = True):
        async with async_playwright() as p:
            page = None
            browser = None
            context = None
            try:
                camera_bridge_js = f"""
                                    const INPUT_W = {self.__frame_w};
                                    const INPUT_H = {self.__frame_h};
                                    const SCALE_FACTOR = {self.__scale};
                                    const WS_URL = "ws://localhost:{self.__port}/frame-stream";
                                    const FPS = {self.__fps};
                                    
                                    """
                with open('src/camera_bridge.js', 'r') as fin:
                    camera_bridge_js += fin.read()

                browser = await p.chromium.launch(
                    headless=headless,
                    args=[
                        "--use-fake-ui-for-media-stream",
                        "--use-fake-device-for-media-stream",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--disable-sync",
                        "--disable-extensions",
                        "--disable-features=Translate,OptimizationHints"
                    ]
                )

                context = await browser.new_context(
                    permissions=['camera', 'microphone']
                )

                # context.on("console", lambda msg: print("BROWSER LOG:", msg.text))

                await context.add_init_script(script=camera_bridge_js)

                await context.route("**/*", Browser.__handle_route)

                page = await context.new_page()
                await self.__work_with_page(page)
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

    async def __work_with_page(self, page: Page):
        with open('src/input_cameras.js', 'r') as fin:
            rtc_client_js = fin.read()

        await page.goto(self.__call_url)

        try:
            but_continue = page.get_by_text("Продолжить в браузере")
            await but_continue.wait_for(timeout=3000)
            await but_continue.click()
        except:
            pass
        try:
            input_user = page.locator('input[value="Гость"]')
            await input_user.wait_for(timeout=3000)
            await input_user.fill(self.__user)
        except:
            pass

        try:
            allow_cookies = page.locator('div[id="gdpr-popup-v3-button-mandatory"]')
            await allow_cookies.wait_for(timeout=3000)
            await allow_cookies.nth(0).click()
        except:
            pass

        but_mic = page.get_by_title("Выключить микрофон")
        await but_mic.wait_for()
        await but_mic.click()
        but_connect = page.get_by_text("Подключиться")
        await but_connect.wait_for()
        await but_connect.click()

        await page.evaluate(rtc_client_js, [self.__frame_w, self.__frame_h])
        print("BROWSER STARTED")

    @staticmethod
    async def __handle_route(route):
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
