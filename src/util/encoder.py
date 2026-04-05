import struct
from abc import ABC, abstractmethod

import numpy as np

# Магическое число для дополнительной валидации (4 байта)
MAGIC_HEADER = b'DATA'


class EncoderInterface(ABC):

    @abstractmethod
    def image_size(self) -> tuple[int, int]:
        return None

    @abstractmethod
    def encode(self, data: np.ndarray | list[np.ndarray]) -> np.ndarray:
        pass

    @abstractmethod
    def decode(self, frame: np.ndarray) -> list[np.ndarray]:
        pass

    @abstractmethod
    def max_data_size(self, count: int) -> int:
        pass


class BitmapEncoder(EncoderInterface):
    def image_size(self) -> tuple[int, int]:
        return self.width, self.height

    def __init__(
            self,
            width: int = 256,
            height: int = 256,
            block_size: int = 4,
            border_size: int = 4,
    ) -> None:
        self.width = width
        self.height = height
        self.block_size = block_size
        self.border_size = border_size

    def _logical_size(self) -> tuple[int, int]:
        log_w = (self.width - 2 * self.border_size) // self.block_size
        log_h = (self.height - 2 * self.border_size) // self.block_size
        return log_w, log_h

    def _payload_origin(self) -> tuple[int, int]:
        start_y = self.border_size + ((self.height - 2 * self.border_size) % self.block_size) // 2
        start_x = self.border_size + ((self.width - 2 * self.border_size) % self.block_size) // 2
        return start_y, start_x

    def max_data_size(self, count: int) -> int:
        log_w, log_h = self._logical_size()
        max_bits = log_w * log_h * 3
        max_bytes = max_bits // 8
        header_size = 8
        return max_bytes - header_size

    def encode(self, data: np.ndarray | list[np.ndarray]) -> np.ndarray:
        """
        Упаковывает 1D массив байт в RGB bitmap
        """
        if isinstance(data, list):
            raise ValueError("Кодирования списка пока не реализовано")

        if data.dtype != np.uint8:
            data = data.astype(np.uint8)

        log_w, log_h = self._logical_size()
        max_bits = log_w * log_h * 3
        max_data_size = self.max_data_size()
        if len(data) > max_data_size:
            raise ValueError(f"Слишком много данных. Максимум для текущих настроек: {max_data_size} байт.")

        length_bytes = np.frombuffer(struct.pack('>I', len(data)), dtype=np.uint8)
        magic_bytes = np.frombuffer(MAGIC_HEADER, dtype=np.uint8)
        full_data = np.concatenate((magic_bytes, length_bytes, data))

        bits = np.unpackbits(full_data)
        if len(bits) < max_bits:
            padding = np.zeros(max_bits - len(bits), dtype=np.uint8)
            bits = np.concatenate((bits, padding))

        payload_log = bits.reshape((log_h, log_w, 3)) * 255
        payload = np.repeat(np.repeat(payload_log, self.block_size, axis=0), self.block_size, axis=1)

        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        frame[:self.border_size, :, :] = [0, 255, 0]
        frame[-self.border_size:, :, :] = [255, 0, 255]
        frame[:, :self.border_size, :] = [255, 128, 0]
        frame[:, -self.border_size:, :] = [0, 128, 255]

        start_y, start_x = self._payload_origin()
        frame[start_y:start_y + payload.shape[0], start_x:start_x + payload.shape[1]] = payload
        return frame

    def decode(self, frame: np.ndarray) -> list[np.ndarray]:
        """
        Извлекает данные из RGB bitmap. Если кадр не валиден — возвращает None.
        """
        if frame.shape != (self.height, self.width, 3):
            return []

        top_green_mean = frame[:self.border_size, :, 1].mean()
        bottom_magenta_red_mean = frame[-self.border_size:, :, 0].mean()
        if top_green_mean < 150 or bottom_magenta_red_mean < 150:
            return []

        log_w, log_h = self._logical_size()
        start_y, start_x = self._payload_origin()
        payload_area = frame[
            start_y:start_y + log_h * self.block_size,
            start_x:start_x + log_w * self.block_size,
        ]

        offset = self.block_size // 2
        sampled = payload_area[offset::self.block_size, offset::self.block_size]
        bits = (sampled > 127).astype(np.uint8).flatten()
        all_bytes = np.packbits(bits)

        extracted_magic = bytes(all_bytes[:4])
        if extracted_magic != MAGIC_HEADER:
            return []

        data_length = struct.unpack('>I', all_bytes[4:8].tobytes())[0]
        if data_length > len(all_bytes) - 8:
            return []

        return [all_bytes[8:8 + data_length]]


if __name__ == '__main__':
    # Тест
    data = np.random.randint(0, 256, size=500, dtype=np.uint8)
    encoder = BitmapEncoder(block_size=6)
    img = encoder.encode(data)  # Делаем блоки покрупнее для сильного сжатия

    # Симулируем H264 сжатие: шум и размытие + небольшое смещение цвета
    noisy_img = img.copy()
    noisy_img = np.clip(noisy_img.astype(np.int16) + np.random.randint(-40, 40, img.shape), 0, 255).astype(np.uint8)

    decoded = encoder.decode(noisy_img)

    print(np.array_equal(data, decoded[0]))  # Выведет True
