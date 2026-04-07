# кодировщик похож на Bitmap, но использует только оттенки серого, что позволяет добиться сразу двух вещей:
# избежать многих искажений от сжатия и уменьшить передаваемые между python и js данные в разы.

import numpy as np
from numba import njit

from src.util.encoder import EncoderInterface, Frame


@njit(fastmath=True)
def _fast_unpack_bits1(byte) -> np.ndarray:
    result = np.empty(8, dtype=np.bool)
    result[0] = byte & 0x80 != 0
    result[1] = byte & 0x40 != 0
    result[2] = byte & 0x20 != 0
    result[3] = byte & 0x10 != 0
    result[4] = byte & 0x08 != 0
    result[5] = byte & 0x04 != 0
    result[6] = byte & 0x02 != 0
    result[7] = byte & 0x01 != 0
    return result


@njit(fastmath=True)
def _fast_unpack_bits(arr: np.ndarray, div: int) -> np.ndarray:
    ns = arr.size * 8
    rest = (div - ns % div) % div
    result = np.empty(arr.size * 8 + rest, dtype=np.bool)
    for i in range(arr.size):
        result[i * 8: (i + 1) * 8] = _fast_unpack_bits1(arr[i])
    return result


@njit(fastmath=True)
def _fast_pack_bits(arr: np.ndarray) -> np.ndarray:
    bts = arr.shape[1]
    result = np.empty(arr.shape[0], dtype=np.uint8)

    for i in range(arr.shape[0]):
        bt = 0
        for j in range(bts):
            bt <<= 1
            bt |= (arr[i, j] != 0)
        result[i] = bt

    return result


@njit(fastmath=True)
def _encode(raw: np.ndarray, res: np.ndarray, bpx: int, width: int, height: int, code_table: np.ndarray):
    bits = _fast_unpack_bits(raw, bpx)

    syms = bits.reshape((bits.size // bpx, bpx))
    syms = _fast_pack_bits(syms)
    i = 0
    wmo = width - 1
    hmo = height - 1
    for y in range(height):
        for x in range(width):
            if ((x == 0 and y == 0) or
                    (x == wmo and y == 0) or
                    (x == 0 and y == hmo) or
                    (x == wmo and y == hmo)):
                continue
            res[y, x] = code_table[syms[i]]
            i += 1


@njit(fastmath=True)
def _pre_decode(frame, width, height, bpx, max_bytes) -> np.ndarray:
    frame = frame.astype(np.float32) / (255. / ((1 << bpx) - 1))
    bits = np.empty((max_bytes + 1) * 8, dtype=np.uint8)
    i = 0
    wmo = width - 1
    hmo = height - 1
    for y in range(height):
        for x in range(width):
            if ((x == 0 and y == 0) or
                    (x == wmo and y == 0) or
                    (x == 0 and y == hmo) or
                    (x == wmo and y == hmo)):
                continue
            bits[i:i + bpx] = _fast_unpack_bits1(np.uint8(round(frame[y, x])))[-bpx:]
            i += bpx
    raw_data = _fast_pack_bits(bits.reshape((-1, 8)))
    return raw_data


class GrayscaleEncoder(EncoderInterface):

    def __init__(self, width: int = 64, height: int = 64, bits_per_pixel: int = 4):
        self.__width = width
        self.__height = height
        self.__bpx = bits_per_pixel
        self.__code_table = GrayscaleEncoder.__init_code_table(bits_per_pixel)

    @staticmethod
    def __init_code_table(depth) -> np.ndarray:
        c = 1 << depth
        step = 255 / (c - 1)
        res = np.empty(c, dtype=np.float32)
        cur = 0
        for i in range(c):
            res[i] = cur
            cur += step
        return res.astype(np.uint8)

    def image_size(self) -> tuple[int, int]:
        return self.__width, self.__height

    def __use_pos(self, pos: tuple[int, int]) -> bool:
        x, y = pos
        wmo = self.__width - 1
        hmo = self.__height - 1
        if ((x == 0 and y == 0) or
                (x == wmo and y == 0) or
                (x == 0 and y == hmo) or
                (x == wmo and y == hmo)):
            return False
        return True

    def encode(self, data: list[bytes]) -> np.ndarray:
        if not isinstance(data, list):
            data = [data]
        res = np.zeros((self.__height, self.__width), dtype=np.uint8)
        res[0, 0] = 255
        res[-1, 0] = 0
        res[0, -1] = 0
        res[-1, -1] = 255
        raw = Frame.serialize(data)

        _encode(raw, res, self.__bpx, self.__width, self.__height, self.__code_table)
        return res

    def decode(self, frame: np.ndarray) -> list[bytes]:
        if frame[0, 0] < 240 or frame[-1, -1] < 240 or frame[-1, 0] > 15 or frame[0, -1] > 15:
            return []

        raw_data = _pre_decode(frame, self.__width, self.__height, self.__bpx, self.__max_bytes())
        gen = iter(raw_data)

        total, offsets = Frame.parse_header(gen)
        if not total or not offsets:
            return []
        raw = np.fromiter(gen, dtype=np.uint8, count=total)
        return Frame.extract_payload(raw, offsets)

    def __max_bytes(self):
        return (self.__width * self.__height - 4) * self.__bpx // 8

    def max_data_size(self, count: int) -> int:
        return self.__max_bytes()

if __name__ == "__main__":
    coder = GrayscaleEncoder(64, 64, 4)
    print(coder.max_data_size(1))

    encoded = coder.encode([b'Hello, World!'])
    import cv2

    for q in range(100, 0, -10):
        cv2.imwrite('result.jpeg', cv2.resize(encoded, (512, 512), interpolation=cv2.INTER_NEAREST),
                    [int(cv2.IMWRITE_JPEG_QUALITY), q])
        loaded = cv2.resize(cv2.imread('result.jpeg'), (64, 64), interpolation=cv2.INTER_NEAREST)[:, :, 0]
        print(q, coder.decode(loaded))
