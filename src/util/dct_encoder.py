import struct
import zlib
from typing import Iterable

import cv2
import numpy as np
import random
from numba import njit

from src.util.encoder import EncoderInterface


class Frame:
    __MAGIC = b'TUNA'

    @staticmethod
    def serialize(data: list[bytes]) -> np.ndarray:
        N = len(data)

        # Считаем размер payload
        total_payload_len = sum(len(b) + 4 for b in data)

        header = bytearray()
        header.extend(Frame.__MAGIC)
        header.extend(struct.pack('>H', N))
        header.extend(struct.pack('>H', total_payload_len))

        # entries (offset, length)
        offset = 0
        entries = bytearray()
        for b in data:
            length = len(b)
            entries.extend(struct.pack('>HH', offset, length))
            offset += length + 4

        header.extend(entries)

        # CRC заголовка
        header_crc = zlib.crc32(header)
        header.extend(struct.pack('>I', header_crc))

        # payload
        payload = bytearray()
        for b in data:
            payload.extend(b)
            payload.extend(struct.pack('>I', zlib.crc32(b)))

        return np.frombuffer(header + payload, dtype=np.uint8)

    @staticmethod
    def parse_header(stream: Iterable[int]) -> tuple[int | None, list[tuple[int, int]] | None]:
        it = iter(stream)

        def read_n(n: int) -> bytes:
            return bytes([next(it) for _ in range(n)])

        try:
            magic = read_n(4)

            if magic != Frame.__MAGIC:
                return None, None

            n_bytes = read_n(2)
            N = struct.unpack('>H', n_bytes)[0]

            total_len_bytes = read_n(2)
            total_payload_len = struct.unpack('>H', total_len_bytes)[0]

            entries_raw = read_n(N * 4)

            header = bytearray()
            header.extend(magic)
            header.extend(n_bytes)
            header.extend(total_len_bytes)
            header.extend(entries_raw)

            stored_crc_bytes = read_n(4)
            stored_crc = struct.unpack('>I', stored_crc_bytes)[0]

            if zlib.crc32(header) != stored_crc:
                return None, None
            entries = []
            for i in range(N):
                offset, length = struct.unpack(
                    '>HH',
                    entries_raw[i * 4:(i + 1) * 4]
                )
                entries.append((offset, length))

            return total_payload_len, entries

        except StopIteration:
            return None, None

    @staticmethod
    def extract_payload(payload: np.ndarray, entries: list[tuple[int, int]]) -> list[bytes]:
        raw = payload.tobytes()

        result = []

        for offset, length in entries:
            start = offset
            end = start + length

            if end + 4 > len(raw):
                continue  # выход за границы
            data_bytes = raw[start:end]
            stored_crc = struct.unpack('>I', raw[end:end + 4])[0]
            if zlib.crc32(data_bytes) == stored_crc:
                result.append(data_bytes)

        return result


class DCTEncoder(EncoderInterface):

    __ENCODE_PATH = [(1, 0), (0, 1), (0, 2), (1, 1),
                     (2, 0), (3, 0), (2, 1), (1, 2),
                     (0, 3), (0, 4), (1, 3), (2, 2),
                     (3, 1), (4, 0), (5, 0), (0, 5),
                     (1, 5), (1, 4), (2, 4), (2, 3),
                     (3, 3), (3, 2), (4, 2), (4, 1)]

    __BLOCK_DENSITY = len(__ENCODE_PATH) // 8

    __SIGNATURE_DISTANCE_THRESHOLD = 50

    __PATH_X = np.array([x for x, _ in __ENCODE_PATH], dtype=np.intp)
    __PATH_Y = np.array([y for _, y in __ENCODE_PATH], dtype=np.intp)

    def __init__(self, width: int = 32, height: int = 32, amplitude: float = 40, use_reed_solomon: bool = True):
        self.__width = width
        self.__height = height
        self.__amplitude = np.float32(amplitude)
        rnd = random.Random(42)
        poses = list([
            pos for pos in np.ndindex(self.__width, self.__height)
            if pos not in {
                (0, 0),
                (self.__width - 1, 0),
                (0, self.__height - 1),
                (self.__width - 1, self.__height - 1),
            }
        ])
        rnd.shuffle(poses)
        self.__positions = np.array(poses)
        self.__use_rs = use_reed_solomon
        self.__dct_core = DCTEncoder.create_dtc_core()
        self.__dct_core_t = self.__dct_core.T

        if use_reed_solomon:
            from src.util.my_reed_solo import MyReedSolo
            self.__RSC = MyReedSolo()

    @staticmethod
    def create_dtc_core():
        core = np.zeros((8, 8))
        for i in range(8):
            for j in range(8):
                if i == 0:
                    core[i, j] = 1 / np.sqrt(8)
                else:
                    core[i, j] = np.sqrt(2 / 8) * np.cos(np.pi * i * (2 * j + 1) / (2 * 8))
        return core

    @staticmethod
    @njit(fastmath=True)
    def fast_dct_blocks_numba(matrix, c, ct):
        matrix -= 128.0
        n = matrix.shape[0]
        num_blocks = n // 8
        res = np.empty((n, n), dtype=matrix.dtype)
        block = np.empty((8, 8), dtype=matrix.dtype)
        # В Numba вложенные циклы компилируются в эффективный машинный код
        for i in range(num_blocks):
            row_offset = i * 8
            for j in range(num_blocks):
                col_offset = j * 8

                # Извлекаем блок
                block[:, :] = matrix[row_offset:row_offset + 8, col_offset:col_offset + 8]

                # Матричное умножение DCT: C * Block * C.T
                # Оператор @ поддерживается Numba
                res[row_offset:row_offset + 8, col_offset:col_offset + 8] = c @ block @ ct

        return res

    @staticmethod
    @njit(fastmath=True)
    def fast_idct_blocks_numba(matrix, c, ct):
        h = matrix.shape[0]
        w = matrix.shape[1]
        num_blocks_h = h // 8
        num_blocks_w = w // 8
        res = np.empty((h, w), dtype=matrix.dtype)
        block = np.empty((8, 8), dtype=matrix.dtype)
        for i in range(num_blocks_h):
            row_offset = i * 8
            for j in range(num_blocks_w):
                col_offset = j * 8
                block[:, :] = matrix[row_offset:row_offset + 8, col_offset:col_offset + 8]
                res[row_offset:row_offset + 8, col_offset:col_offset + 8] = ct @ block @ c
        return res

    @staticmethod
    @njit(fastmath=True)
    def decode_block(y_block: np.ndarray, path_x: np.ndarray, path_y: np.ndarray) -> np.ndarray:
        """Преобразует 2 байт в матрицу 8x8 в формате RGB"""
        # получаем биты
        # y_mat = self.__dct2d(y_block.astype(np.float32) - 128.0)
        bits = np.empty(24, dtype=np.bool)
        for i in range(24):
            bits[i] = y_block[path_x[i], path_y[i]] > 0.0
        result = np.empty(3, dtype=np.uint8)
        result[0] = (bits[0] << 7) | (bits[1] << 6) | (bits[2] << 5) | (bits[3] << 4) | (bits[4] << 3) | (
                bits[5] << 2) | (bits[6] << 1) | (bits[7])
        result[1] = (bits[8] << 7) | (bits[9] << 6) | (bits[10] << 5) | (bits[11] << 4) | (bits[12] << 3) | (
                bits[13] << 2) | (bits[14] << 1) | (bits[15])
        result[2] = (bits[16] << 7) | (bits[17] << 6) | (bits[18] << 5) | (bits[19] << 4) | (bits[20] << 3) | (
                bits[21] << 2) | (bits[22] << 1) | (bits[23])
        return result

    @staticmethod
    @njit(fastmath=True)
    def pre_decode(count: int, density: int, y: np.ndarray, path: np.ndarray, path_x, path_y, dcd) -> np.ndarray:
        result = np.empty((count, density), dtype=np.uint8)
        for i in range(count):
            pos = path[i]
            result[i] = dcd(y[pos[0] * 8: pos[0] * 8 + 8, pos[1] * 8: pos[1] * 8 + 8], path_x, path_y)
        return result.reshape(count * density)

    def encode(self, data: list[bytes]) -> np.ndarray:
        if isinstance(data, bytes):
            data = [data]

        total_len = np.sum([len(x) for x in data])
        if total_len > self.max_data_size(len(data)):
            raise ValueError(
                f"Слишком много данных. Максимум для текущих настроек: {self.max_data_size(len(data))} байт, передано {total_len} байт")

        ser = Frame.serialize(data)
        pack = np.frombuffer(self.__RSC.encode(ser) if self.__use_rs else ser, dtype=np.uint8)
        if len(pack) % 3 == 1:
            pack = np.pad(pack, (0, 2))
        elif len(pack) % 3 == 2:
            pack = np.pad(pack, (0, 1))
        pack = pack.reshape((len(pack) // 3, 3))
        coeffs = np.zeros((self.__width * 8, self.__height * 8), dtype=np.float64)
        bits = np.unpackbits(pack, axis=1)
        values = np.where(bits > 0, self.__amplitude, -self.__amplitude).astype(np.float64)
        for i, pos in enumerate(self.__positions[:len(pack)]):
            row = pos[0] * 8
            col = pos[1] * 8
            block = coeffs[row:row + 8, col:col + 8]
            block[self.__PATH_X, self.__PATH_Y] = values[i]

        y_plane = DCTEncoder.fast_idct_blocks_numba(coeffs, self.__dct_core, self.__dct_core_t)
        y_plane = np.clip(y_plane + 128.0, 0, 255).astype(np.uint8)

        result = np.empty((self.__width * 8, self.__height * 8, 3), dtype=np.uint8)
        result[:, :, 0] = y_plane
        result[:, :, 1] = y_plane
        result[:, :, 2] = y_plane

        # сигнатура кадра - 4 квадрата по углам, белый, красный, зеленый и синий
        result[:8, :8, :] = 255
        result[-8:, :8, :] = 0
        result[:8, -8:, :] = 0
        result[-8:, -8:, :] = 255

        return result

    __LT = np.array([255, 255, 255], dtype=np.uint8)
    __RT = np.array([0, 0, 0], dtype=np.uint8)
    __LB = np.array([0, 0, 0], dtype=np.uint8)
    __RB = np.array([255, 255, 255], dtype=np.uint8)

    def decode(self, frame: np.ndarray) -> list[bytes]:
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

        y_plane = cv2.cvtColor(frame, cv2.COLOR_RGB2YCrCb)[:, :, 0].astype(np.float64)
        y_plane = DCTEncoder.fast_dct_blocks_numba(y_plane, self.__dct_core, self.__dct_core_t)

        raw_data = DCTEncoder.pre_decode(self.__get_total_blocks(), self.__BLOCK_DENSITY, y_plane, self.__positions,
                                         self.__PATH_X, self.__PATH_Y, DCTEncoder.decode_block)

        # скрывает процесс декодирования кода Рида Соломона от последующих этапов алгоритма
        def get_byte_decoded():
            rd = raw_data.reshape((self.__get_total_bytes() // 255, 255))
            for buf in rd:
                dec = self.__RSC.decode(buf)
                for bt in dec:
                    yield bt

        gen = get_byte_decoded() if self.__use_rs else iter(raw_data)

        total, offsets = Frame.parse_header(gen)
        if not total or not offsets:
            return []
        raw = np.fromiter(gen, dtype=np.uint8, count=total)
        return Frame.extract_payload(raw, offsets)

    def image_size(self) -> tuple[int, int]:
        return self.__width * 8, self.__height * 8

    def __get_total_blocks(self):
        return self.__width * self.__height - 4

    def __get_total_bytes(self):
        return self.__get_total_blocks() * self.__BLOCK_DENSITY

    def max_data_size(self, count: int) -> int:
        sz = self.__get_total_bytes()
        # у заголовка обязательно есть 12 байт плюс 4 байта на каждую запись
        # каждая запись так же содержит CRC код на 4 байта
        sz -= 12 + 8 * count
        # всё сообщение целиком будет закодировано кодами Рида Соломона, что значит, что на 255 байт информации
        # будет 32 байта защиты
        return sz - (1 + sz // 255) * 32 if self.__use_rs else sz

    def warmup(self):
        datas = np.empty((1000, 1000), dtype=np.uint8)
        encoded = []
        for data in datas:
            encoded.append(self.encode(data.tobytes))
        for enc in encoded:
            self.decode(enc)

if __name__ == "__main__":
    c = DCTEncoder()
    print(c.max_data_size(1))
    data = np.random.randint(0, 255, 1000, dtype=np.uint8)
    encoded = c.encode(data.tobytes())

    cv2.imwrite('result.jpeg', encoded, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
    img = cv2.imread('result.jpeg')
    decoded = c.decode(img)
    print(np.array_equal(data, np.frombuffer(decoded[0], dtype=np.uint8)))
