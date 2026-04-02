import struct

import cv2
import numpy as np

from src.util.encoder import EncoderInterface


class DCTEncoder(EncoderInterface):
    def image_size(self) -> tuple[int, int]:
        return self.__width * 8, self.__height * 8

    __BLOCK_DENSITY = 2
    __MAGIC_HEADER = np.frombuffer(b'TUNA', dtype=np.uint8)
    __LUT_MAGIC = b'DCTL'
    __LUT_HEADER_FORMAT = '>4sf'

    __ENCODE_PATH = [(1, 0), (0, 1), (0, 2), (1, 1),
                     (2, 0), (3, 0), (2, 1), (1, 2),
                     (0, 3), (0, 4), (1, 3), (2, 2),
                     (3, 1), (4, 0), (5, 0), (0, 5)]

    __SIGNATURE_DISTANCE_THRESHOLD = 50

    __PATH_X = np.array([x for x, _ in __ENCODE_PATH], dtype=np.intp)
    __PATH_Y = np.array([y for _, y in __ENCODE_PATH], dtype=np.intp)
    __LUT_SHAPE = (1 << 16, 8, 8, 3)
    __LUT_SIZE = int(np.prod(__LUT_SHAPE))

    def __init__(self, width: int = 32, height: int = 32, amplitude: float = 20, lut_file: str | None = None):
        self.__width = width
        self.__height = height
        self.__amplitude = np.float32(amplitude)
        self.__positions = [
            pos for pos in np.ndindex(self.__width, self.__height)
            if pos not in {
                (0, 0),
                (self.__width - 1, 0),
                (0, self.__height - 1),
                (self.__width - 1, self.__height - 1),
            }
        ]
        if lut_file is None:
            self.__encode_lut = self.build_encode_lut(self.__amplitude)
        else:
            self.__encode_lut = self.__load_encode_lut(lut_file)

    @staticmethod
    def __idct2d(block):
        return cv2.idct(block)

    @staticmethod
    def __dct2d(block):
        return cv2.dct(block)

    @classmethod
    def build_encode_lut(cls, amplitude: float) -> np.ndarray:
        amplitude = np.float32(amplitude)
        lut = np.empty(cls.__LUT_SHAPE, dtype=np.uint8)

        for value in range(1 << 16):
            word = np.array([value >> 8, value & 0xFF], dtype=np.uint8)
            bits = np.unpackbits(word)
            y_mat = np.zeros((8, 8), dtype=np.float32)
            y_mat[cls.__PATH_X, cls.__PATH_Y] = np.where(bits, amplitude, -amplitude)
            y_mat = np.clip(cv2.idct(y_mat) + 128, 0, 255).astype(np.uint8)

            lut[value, :, :, 0] = y_mat
            lut[value, :, :, 1] = y_mat
            lut[value, :, :, 2] = y_mat

        return lut

    @classmethod
    def save_encode_lut(cls, path: str, amplitude: float) -> None:
        lut = cls.build_encode_lut(amplitude)
        with open(path, 'wb') as fp:
            fp.write(struct.pack(cls.__LUT_HEADER_FORMAT, cls.__LUT_MAGIC, float(amplitude)))
            fp.write(lut.tobytes())

    @classmethod
    def __load_encode_lut(cls, path: str) -> np.ndarray:
        header_size = struct.calcsize(cls.__LUT_HEADER_FORMAT)
        with open(path, 'rb') as fp:
            header = fp.read(header_size)
            if len(header) != header_size:
                raise ValueError("LUT file is truncated")
            magic, _amplitude = struct.unpack(cls.__LUT_HEADER_FORMAT, header)
            if magic != cls.__LUT_MAGIC:
                raise ValueError("Invalid LUT file magic")
            lut = np.frombuffer(fp.read(), dtype=np.uint8)

        if lut.size != cls.__LUT_SIZE:
            raise ValueError(f"Invalid LUT payload size: expected {cls.__LUT_SIZE}, got {lut.size}")

        return lut.reshape(cls.__LUT_SHAPE)

    def encode_block(self, word: np.ndarray) -> np.ndarray:
        """Преобразует 2 байт в матрицу 8x8 в формате RGB"""
        index = (int(word[0]) << 8) | int(word[1])
        return self.__encode_lut[index]

    def decode_block(self, y_block: np.ndarray) -> np.ndarray:
        """Преобразует 2 байт в матрицу 8x8 в формате RGB"""
        # получаем биты
        y_mat = self.__dct2d(y_block.astype(np.float32) - 128.0)
        return np.uint8(np.packbits(y_mat[self.__PATH_X, self.__PATH_Y] > 0.0))

    def encode(self, data: np.ndarray | list[np.ndarray]) -> np.ndarray:
        if isinstance(data, np.ndarray):
            data = [data]

        total_len = np.sum([len(x) for x in data])
        if total_len > self.max_data_size(len(data)):
            raise ValueError(
                f"Слишком много данных. Максимум для текущих настроек: {self.max_data_size(len(data))} байт, передано {total_len} байт")

        result = np.zeros((self.__width * 8, self.__height * 8, 3), dtype=np.uint8)
        # сигнатура кадра - 4 квадрата по углам, белый, красный, зеленый и синий
        result[:8, :8, :] = 255
        result[-8:, :8, 0] = 255
        result[:8, -8:, 1] = 255
        result[-8:, -8:, 2] = 255

        pgn = iter(self.__positions)

        for pack in data:
            header = np.concatenate(
                (self.__MAGIC_HEADER, np.frombuffer(struct.pack('>I', len(pack)), dtype=np.uint8))).reshape((4, 2))
            for h in header:
                pos = next(pgn)
                result[pos[0] * 8: pos[0] * 8 + 8, pos[1] * 8: pos[1] * 8 + 8] = self.encode_block(h)
            if len(pack) % 2 == 1:
                pack = np.pad(pack, (0, 1))
            pack = pack.reshape((len(pack) // 2, 2))
            for pair in pack:
                pos = next(pgn)
                result[pos[0] * 8: pos[0] * 8 + 8, pos[1] * 8: pos[1] * 8 + 8] = self.encode_block(pair)

        return result

    __LT = np.array([255, 255, 255], dtype=np.uint8)
    __RT = np.array([255, 0, 0], dtype=np.uint8)
    __LB = np.array([0, 255, 0], dtype=np.uint8)
    __RB = np.array([0, 0, 255], dtype=np.uint8)

    def decode(self, frame: np.ndarray) -> list[np.ndarray]:
        result = []
        # сначала сверяем сигнатуру
        lt = frame[:7, :7].mean(axis=(0, 1))
        rt = frame[-7:, :7].mean(axis=(0, 1))
        lb = frame[:7, -7:].mean(axis=(0, 1))
        rb = frame[-7:, -7:].mean(axis=(0, 1))
        if (np.linalg.norm(self.__LT - lt) > self.__SIGNATURE_DISTANCE_THRESHOLD or
                np.linalg.norm(self.__RT - rt) > self.__SIGNATURE_DISTANCE_THRESHOLD or
                np.linalg.norm(self.__LB - lb) > self.__SIGNATURE_DISTANCE_THRESHOLD or
                np.linalg.norm(self.__RB - rb) > self.__SIGNATURE_DISTANCE_THRESHOLD):
            return result

        y_plane = cv2.cvtColor(frame, cv2.COLOR_RGB2YCrCb)[:, :, 0]
        pgn = iter(self.__positions)
        max_size = self.max_data_size(1) * 2

        def next_2_bytes():
            pos = next(pgn, None)
            if pos is None:
                return None
            return self.decode_block(y_plane[pos[0] * 8: pos[0] * 8 + 8, pos[1] * 8: pos[1] * 8 + 8])

        while True:
            header = np.empty(8, dtype=np.uint8)
            for i in range(4):
                b2 = next_2_bytes()
                if b2 is None:
                    return result
                header[i * 2: i * 2 + 2] = b2
            size = struct.unpack('>I', header[4:].tobytes())[0]
            if not np.array_equal(self.__MAGIC_HEADER, header[:4]) or size > max_size:
                return result
            pack = np.empty(size, dtype=np.uint8)
            for i in range((size + 1) // 2):
                b2 = next_2_bytes()
                end = min(i * 2 + 2, size)
                pack[i * 2:end] = b2[:end - i * 2]
            result.append(pack)

    def max_data_size(self, count: int) -> int:
        # 4 блока под сигнатуру и 4 байта под заголовок
        return (self.__width * self.__height - 4) * self.__BLOCK_DENSITY - 8 * count


if __name__ == "__main__":
    c = DCTEncoder(lut_file="encoder_lut.hex")
    data = np.random.randint(0, 255, 1000, dtype=np.uint8)
    encoded = c.encode(data)
    from PIL import Image

    Image.fromarray(encoded).save('result.jpeg')
    img = np.array(Image.open('result.jpeg'), dtype=np.uint8)
    decoded = c.decode(img)
    print(np.array_equal(data, decoded[0]))
