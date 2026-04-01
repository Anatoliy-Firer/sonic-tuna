import struct

import numpy as np

# Магическое число для дополнительной валидации (4 байта)
MAGIC_HEADER = b'DATA'


def encode(
        data: np.ndarray,
        width: int = 256,
        height: int = 256,
        border_size: int = 4,
        n: int = 1
) -> np.ndarray:
    """
    Упаковывает 1D массив байт в RGB bitmap
    """
    if not 1 <= n <= 8:
        raise ValueError("Аргумент n должен быть в диапазоне от 1 до 8.")

    if data.dtype != np.uint8:
        data = data.astype(np.uint8)

    # Вычисляем полезную площадь в пикселях
    log_w = width - 2 * border_size
    log_h = height - 2 * border_size

    # Максимальная вместимость в битах и байтах
    max_symbols = log_w * log_h * 3  # 3 канала (RGB)
    max_bits = max_symbols * n
    max_bytes = max_bits // 8

    # 4 байта магия + 4 байта длина
    header_size = 8
    if len(data) > max_bytes - header_size:
        raise ValueError(f"Слишком много данных. Максимум для текущих настроек: {max_bytes - header_size} байт.")

    # Формируем заголовок: [MAGIC] + [LENGTH]
    length_bytes = np.frombuffer(struct.pack('>I', len(data)), dtype=np.uint8)
    magic_bytes = np.frombuffer(MAGIC_HEADER, dtype=np.uint8)

    full_data = np.concatenate((magic_bytes, length_bytes, data))

    # Переводим байты в биты (супер-быстрая сишная операция)
    bits = np.unpackbits(full_data)

    # Добиваем нулями до полного заполнения кадра
    if len(bits) < max_bits:
        padding = np.zeros(max_bits - len(bits), dtype=np.uint8)
        bits = np.concatenate((bits, padding))

    # Группируем по n бит на канал и растягиваем по всей шкале 0..255.
    grouped_bits = bits.reshape((-1, n))
    bit_weights = (1 << np.arange(n - 1, -1, -1, dtype=np.uint16))
    symbols = grouped_bits.dot(bit_weights).astype(np.uint16)
    levels = (symbols * 255 + ((1 << n) - 2) // 2) // ((1 << n) - 1)
    payload_log = levels.astype(np.uint8).reshape((log_h, log_w, 3))

    # Создаем итоговый холст
    frame = np.zeros((height, width, 3), dtype=np.uint8)

    # РИСУЕМ СИГНАТУРУ (РАМКУ)
    # Верхняя рамка зеленая, нижняя пурпурная. Устойчиво к H264.
    frame[:border_size, :, :] = [0, 255, 0]
    frame[-border_size:, :, :] = [255, 0, 255]
    frame[:, :border_size, :] = [255, 128, 0]  # Левая оранжевая
    frame[:, -border_size:, :] = [0, 128, 255]  # Правая голубая

    # Вставляем полезную нагрузку внутрь рамки
    frame[border_size:border_size + log_h, border_size:border_size + log_w] = payload_log

    return frame


def decode(
        image: np.ndarray,
        width: int = 256,
        height: int = 256,
        border_size: int = 4,
        n: int = 1
) -> np.ndarray | None:
    """
    Извлекает данные из RGB bitmap. Если кадр не валиден — возвращает None.
    """
    if not 1 <= n <= 8:
        raise ValueError("Аргумент n должен быть в диапазоне от 1 до 8.")

    if image.shape != (height, width, 3):
        return None

    # БЫСТРАЯ ПРОВЕРКА СИГНАТУРЫ (РАМКИ)
    # Берем среднее значение цвета рамки с учетом погрешности сжатия
    top_green_mean = image[:border_size, :, 1].mean()
    bottom_magenta_red_mean = image[-border_size:, :, 0].mean()

    # Если рамка искажена или ее нет (напр. обычное видео) - сразу дропаем
    if top_green_mean < 150 or bottom_magenta_red_mean < 150:
        return None

    log_w = width - 2 * border_size
    log_h = height - 2 * border_size

    # Вырезаем область payload
    sampled = image[border_size:border_size + log_h, border_size:border_size + log_w]

    max_symbol = (1 << n) - 1
    quantized = np.rint(sampled.astype(np.float32) * max_symbol / 255.0).astype(np.uint8)
    bits = np.unpackbits(quantized.reshape(-1, 1), axis=1)[:, -n:].reshape(-1)

    # Собираем байты из битов
    all_bytes = np.packbits(bits)

    if len(all_bytes) < 8:
        return None

    # Проверяем "магическое число" в заголовке (второй этап валидации)
    extracted_magic = bytes(all_bytes[:4])
    if extracted_magic != MAGIC_HEADER:
        return None

    # Читаем длину реальных данных
    data_length = struct.unpack('>I', all_bytes[4:8].tobytes())[0]

    # Защита от битых кадров, где длина расшифровалась как космическое число
    if data_length > len(all_bytes) - 8:
        return None

    return all_bytes[8:8 + data_length]
