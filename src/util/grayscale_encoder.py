# кодировщик похож на Bitmap, но использует только оттенки серого, что позволяет добиться сразу двух вещей:
# избежать многих искажений от сжатия и уменьшить передаваемые между python и js данные в разы.
from itertools import product

import numpy as np

from src.util.encoder import EncoderInterface, Frame


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

        bits = np.unpackbits(raw)
        rest = self.__bpx - len(bits) % self.__bpx
        if rest > 0:
            bits = np.pad(bits, (0, rest))

        syms = bits.reshape((bits.size // self.__bpx, self.__bpx))
        syms = np.packbits(syms, axis=1) >> (8 - self.__bpx)
        syms = syms.flatten()

        for (y, x), sym in zip(filter(self.__use_pos, product(range(self.__width), range(self.__height))), syms):
            res[y, x] = self.__code_table[sym]

        return res

    def decode(self, frame: np.ndarray) -> list[bytes]:
        if frame[0, 0] < 240 or frame[-1, -1] < 240 or frame[-1, 0] > 15 or frame[0, -1] > 15:
            return []

        frame = (frame.astype(np.float32) / (255. / ((1 << self.__bpx) - 1))).round().astype(np.uint8)
        # plain = np.concatenate([frame[0, 1:-1], frame[1:-1, :].reshape(self.__width * (self.__height-2)), frame[-1, 1:-1]])
        bits = np.empty((self.__max_bytes() + 1) * 8, dtype=np.bool)
        for (y, x), i in zip(filter(self.__use_pos, product(range(self.__width), range(self.__height))),
                             range(0, bits.size, self.__bpx)):
            # print(bin(frame[y,x]))
            bits[i:i + self.__bpx] = np.unpackbits(frame[y, x])[-self.__bpx:]
        raw_data = np.packbits(bits)

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
