import asyncio
import fcntl
import os
import struct
from contextlib import AbstractContextManager


class Tunnel(AbstractContextManager):
    """Класс реализует взаимодействие с виртуальным устройством туннеля"""

    __TUNSETIFF = 0x400454ca
    __IFF_TUN = 0x0001
    __IFF_NO_PI = 0x1000

    def __init__(self, device: str, mtu: int):
        self.__buffer_size = Tunnel.__next_power_of_2(mtu)
        self.__tun = os.open("/dev/net/tun", os.O_RDWR)
        ifr = struct.pack('16sH', device.encode("ascii"), self.__IFF_TUN | self.__IFF_NO_PI)
        fcntl.ioctl(self.__tun, self.__TUNSETIFF, ifr)
        os.set_blocking(self.__tun, False)

    async def packages(self):
        loop = asyncio.get_running_loop()
        ip_queue = asyncio.Queue()

        def read_ippack():
            try:
                ip_queue.put_nowait(os.read(self.__tun, self.__buffer_size))
            except Exception as err:
                ip_queue.put_nowait(err)

        loop.add_reader(self.__tun, read_ippack)
        try:
            while True:
                item = await ip_queue.get()
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            loop.remove_reader(self.__tun)

    def push_package(self, data: bytes):
        try:
            os.write(self.__tun, data)
        except OSError as err:
            print(f'Tun error: {err}')

    def close(self):
        os.close(self.__tun)

    def __exit__(self, exc_type, exc_value, traceback, /):
        self.close()

    @staticmethod
    def __next_power_of_2(n):
        if n <= 0:
            return 1
        return 1 << (n - 1).bit_length()
