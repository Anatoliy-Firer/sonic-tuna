import asyncio
import time
from typing import AsyncGenerator, Callable, Any

import numpy as np


async def chunk_from_queue(queue: asyncio.Queue[Any],
                           predicate: Callable[[int, int], bool],
                           max_wait: float) -> AsyncGenerator[list[Any], Any]:
    buffer = []
    current_size = 0
    deadline = None

    def flush():
        nonlocal buffer, current_size, deadline
        chunk = buffer
        buffer = []
        current_size = 0
        deadline = None
        return chunk

    while True:
        try:
            if deadline is None:
                item = await queue.get()
                deadline = time.perf_counter() + max_wait
            else:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    yield flush()
                    continue

                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    item = await asyncio.wait_for(queue.get(), timeout=remaining)

            item_len = len(item)
            if predicate(len(buffer) + 1, current_size + item_len):
                buffer.append(item)
                current_size += item_len
                continue

            if buffer:
                yield flush()

            buffer.append(item)
            current_size = item_len
            if deadline is None:
                deadline = time.perf_counter() + max_wait

        except asyncio.TimeoutError:
            if buffer:
                yield flush()
