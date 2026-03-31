import struct

import numpy as np

# Магическое число для дополнительной валидации (4 байта)
MAGIC_HEADER = b'DATA'


def encode(
        data: np.ndarray,
        width: int = 256,
        height: int = 256,
        block_size: int = 4,
        border_size: int = 4
) -> np.ndarray:
    """
    Упаковывает 1D массив байт в RGB bitmap
    """
    if data.dtype != np.uint8:
        data = data.astype(np.uint8)

    # Вычисляем полезную площадь (в "логических" пикселях-блоках)
    log_w = (width - 2 * border_size) // block_size
    log_h = (height - 2 * border_size) // block_size

    # Максимальная вместимость в битах и байтах
    max_bits = log_w * log_h * 3  # 3 канала (RGB)
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

    # Формируем логическое изображение: 1 -> 255, 0 -> 0
    # Reshape в (H, W, 3 канала)
    payload_log = bits.reshape((log_h, log_w, 3)) * 255

    # Масштабируем до реальных размеров (аппаратное дублирование пикселей)
    payload = np.repeat(np.repeat(payload_log, block_size, axis=0), block_size, axis=1)

    # Создаем итоговый холст
    frame = np.zeros((height, width, 3), dtype=np.uint8)

    # РИСУЕМ СИГНАТУРУ (РАМКУ)
    # Верхняя рамка зеленая, нижняя пурпурная. Устойчиво к H264.
    frame[:border_size, :, :] = [0, 255, 0]
    frame[-border_size:, :, :] = [255, 0, 255]
    frame[:, :border_size, :] = [255, 128, 0]  # Левая оранжевая
    frame[:, -border_size:, :] = [0, 128, 255]  # Правая голубая

    # Вставляем полезную нагрузку по центру
    # (центрируем, если из-за деления остались лишние пиксели)
    start_y = border_size + ((height - 2 * border_size) % block_size) // 2
    start_x = border_size + ((width - 2 * border_size) % block_size) // 2

    frame[start_y:start_y + payload.shape[0], start_x:start_x + payload.shape[1]] = payload

    return frame


def decode(
        image: np.ndarray,
        width: int = 256,
        height: int = 256,
        block_size: int = 4,
        border_size: int = 4
) -> np.ndarray | None:
    """
    Извлекает данные из RGB bitmap. Если кадр не валиден — возвращает None.
    """
    if image.shape != (height, width, 3):
        return None

    # БЫСТРАЯ ПРОВЕРКА СИГНАТУРЫ (РАМКИ)
    # Берем среднее значение цвета рамки с учетом погрешности сжатия
    top_green_mean = image[:border_size, :, 1].mean()
    bottom_magenta_red_mean = image[-border_size:, :, 0].mean()

    # Если рамка искажена или ее нет (напр. обычное видео) - сразу дропаем
    if top_green_mean < 150 or bottom_magenta_red_mean < 150:
        return None

    log_w = (width - 2 * border_size) // block_size
    log_h = (height - 2 * border_size) // block_size

    start_y = border_size + ((height - 2 * border_size) % block_size) // 2
    start_x = border_size + ((width - 2 * border_size) % block_size) // 2

    # Вырезаем область payload
    payload_area = image[start_y:start_y + log_h * block_size,
    start_x:start_x + log_w * block_size]

    # Сэмплируем пиксели СТРОГО из центра каждого блока!
    # Это спасает от размытых краев макроблоков H264
    offset = block_size // 2
    sampled = payload_area[offset::block_size, offset::block_size]

    # Thresholding: всё что ярче 127 считаем единицей
    bits = (sampled > 127).astype(np.uint8).flatten()

    # Собираем байты из битов
    all_bytes = np.packbits(bits)

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
