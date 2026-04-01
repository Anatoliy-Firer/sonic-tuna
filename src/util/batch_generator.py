import asyncio
import time
from typing import AsyncGenerator, Callable, Any


async def chunk_from_queue(queue: asyncio.Queue,
                           predicate: Callable[[int, int], bool],
                           max_wait: float) -> AsyncGenerator[list[Any], Any]:
    buffer = []
    current_size = 0
    last_flush = time.perf_counter()

    while True:
        # Считаем, сколько времени осталось до принудительной отправки батча
        elapsed = time.perf_counter() - last_flush
        remaining = max_wait - elapsed

        try:
            # Ждем данные из очереди не дольше, чем осталось до таймаута
            # Если в очереди уже что-то есть, get() вернет это мгновенно
            item = await asyncio.wait_for(queue.get(), timeout=max(0, remaining))

            item_len = len(item)

            # Проверяем предикат ПЕРЕД добавлением
            if predicate(len(buffer) + 1, current_size + item_len):
                buffer.append(item)
                current_size += item_len
            else:
                # Лимит превышен: отдаем старый батч
                if buffer:
                    yield buffer

                # Текущий элемент идет в начало следующего батча
                buffer = [item]
                current_size = item_len
                last_flush = time.perf_counter()

        except asyncio.TimeoutError:
            # Время вышло, а новых данных нет — отдаем то, что накопили
            if buffer:
                yield buffer
                buffer = []
                current_size = 0
            # Обновляем отметку времени, чтобы начать отсчет для нового батча
            last_flush = time.perf_counter()
