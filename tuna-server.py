import argparse
import asyncio
import logging
import struct
from asyncio import CancelledError

import numpy as np
import uvloop
import yaml

from src.browser import Browser
from src.tun import Tunnel
from src.util.batch_generator import chunk_from_queue
from src.util.dct_encoder import DCTEncoder, EncoderInterface
from src.ws_server import WebSocketServer

_PACKET_SIZE = struct.Struct("I")


async def tun_to_ws(tunnel: Tunnel, ws: WebSocketServer, fps: int, codec: EncoderInterface):
    __timeout = 1.0 / fps

    predicate = lambda count, length: length < codec.max_data_size(count)

    async for packs in chunk_from_queue(tunnel.packages(), predicate, __timeout):
        encoded = codec.encode(packs)
        await ws.push_frame(encoded.tobytes())


async def ws_to_tun(tunnel: Tunnel, ws: WebSocketServer, codec: EncoderInterface):
    w, h = codec.image_size()
    async for frame in ws.frames():
        decoded = codec.decode(np.frombuffer(frame, dtype=np.uint8).reshape((w, h, 3)))
        for pack in decoded:
            try:
                tunnel.push_package(pack)
            except CancelledError:
                raise
            except Exception as e:
                logging.debug('Packet dropped {}', e)
                pass


async def main(args: argparse.Namespace):
    logging.basicConfig(
        level=args.log_level,
        format='[%(asctime)s] [%(levelname)s] %(message)s'
    )
    width, height = args.frame_size

    browser = Browser(args.port, width, height, args.frame_scale, args.fps, args.call_url, args.username)
    tunnel = Tunnel(args.device, args.mtu)
    websocket = WebSocketServer(width * height * 3, args.port)

    ws_server = asyncio.create_task(websocket.start())

    browser_task = asyncio.create_task(browser.start_browser(not args.show_gui))

    codec = DCTEncoder(width // 8, height // 8, 40,
                       use_reed_solomon=not args.disable_reed_solomon)

    tun_to_ws_task = asyncio.create_task(tun_to_ws(tunnel, websocket, args.fps, codec))
    ws_to_tun_task = asyncio.create_task(ws_to_tun(tunnel, websocket, codec))

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


def parse_resolution(value: str) -> tuple[int, int]:
    try:
        size = value.lower().split("x", 1)
        width = int(size[0])
        height = int(size[1]) if len(size) > 1 else width
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Resolution must look like <int> or <int>x<int>, got {value!r}"
        ) from exc

    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("Resolution values must be positive")
    if width % 16 != 0 or height % 16 != 0:
        raise argparse.ArgumentTypeError("Resolution width and height must be divisible by 16")

    return width, height


def parse_log_lever(lev: str) -> int:
    return logging.getLevelNamesMapping()[lev.strip().upper()]


def load_config_from_yaml(config_file):
    """Load configuration from YAML file and return as command-line arguments."""
    try:
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        raise RuntimeError(f"Config file not found: {config_file}")
    except yaml.YAMLError as e:
        raise RuntimeError(f"Invalid YAML file: {config_file}: {e}")
    
    if not isinstance(config, dict):
        raise RuntimeError(f"Config file must contain a dictionary at root level")
    
    return config

if __name__ == '__main__':

    parser = argparse.ArgumentParser(prog="tuna-server", description="Tuna Server")
    
    parser.add_argument("--config", type=str, help="Path to YAML config file (ignores other CLI arguments if provided)")

    parser.add_argument("-d", "--device", default="tuna", help="Virtual interface name")
    parser.add_argument("--call-url", type=str, default="https://telemost.yandex.ru/j/71720776790697",
                        help="Yandex Telemost conference url")
    parser.add_argument("--frame-scale", type=int, default=1, help="Frame scale")
    parser.add_argument(
        "--frame-size",
        type=parse_resolution,
        default=(256, 256),
        help=f"Video resolution in WIDTHxHEIGHT format. Default: 256x256",
    )
    parser.add_argument("--fps", type=int, default=20, help="Frame rate")
    parser.add_argument("-p", "--port", type=int, default=8042, help="Internal websocket port")
    parser.add_argument("--mtu", type=int, default=1400, help="MTU")
    parser.add_argument("--show-gui", action='store_true', help="Show chromium GUI")
    parser.add_argument("--username", type=str, default=None,
                        help="Username at Yandex Telemost conference. Default is current system user")
    parser.add_argument("--disable-reed-solomon", action='store_true',
                        help="Disable Reed Solomon error correction (for weak computers)")

    parser.add_argument("-l", "--log-level", type=parse_log_lever, default=logging.INFO,
                        choices=logging.getLevelNamesMapping().values(), help="Log level")

    args = parser.parse_args()
    
    # Handle --config parameter
    if args.config:
        try:
            config = load_config_from_yaml(args.config)
        except RuntimeError as error:
            print(error)
            exit(1)
        
        # Build command-line arguments from config
        reconstructed_argv = []
        
        if 'device' in config:
            reconstructed_argv.extend(['-d', config['device']])
        if 'call_url' in config:
            reconstructed_argv.extend(['--call-url', config['call_url']])
        if 'frame_scale' in config:
            reconstructed_argv.extend(['--frame-scale', str(config['frame_scale'])])
        if 'frame_size' in config:
            reconstructed_argv.extend(['--frame-size', config['frame_size']])
        if 'fps' in config:
            reconstructed_argv.extend(['--fps', str(config['fps'])])
        if 'port' in config:
            reconstructed_argv.extend(['-p', str(config['port'])])
        if 'mtu' in config:
            reconstructed_argv.extend(['--mtu', str(config['mtu'])])
        if config.get('show_gui'):
            reconstructed_argv.append('--show-gui')
        if 'username' in config:
            reconstructed_argv.extend(['--username', config['username']])
        if config.get('disable_reed_solomon'):
            reconstructed_argv.append('--disable-reed-solomon')
        if 'log_level' in config:
            reconstructed_argv.extend(['-l', config['log_level']])
        
        args = parser.parse_args(reconstructed_argv)

    uvloop.run(main(args))
