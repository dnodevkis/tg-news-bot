#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Utils module
===========
Provides reusable decorators and helper functions like retry, rate limiting, etc.
"""

import time
import logging
from functools import wraps

logger = logging.getLogger(__name__)

def retry(max_attempts=3, delay=2, backoff=2):
    """
    Декоратор для повторных попыток выполнения функции при возникновении исключений.
    
    :param max_attempts: Максимальное число попыток
    :param delay: Начальная задержка между попытками
    :param backoff: Множитель увеличения задержки
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            current_delay = delay
            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    attempt += 1
                    logger.warning("Ошибка: %s. Попытка %d из %d", e, attempt, max_attempts)
                    if attempt < max_attempts:
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        raise
        return wrapper
    return decorator

def rate_limited(max_per_second: float):
    """
    Декоратор для ограничения количества вызовов функции (rate limiting).
    :param max_per_second: максимальное кол-во вызовов в секунду
    """
    min_interval = 1.0 / float(max_per_second)

    def decorator(func):
        last_call = [0.0]

        @wraps(func)
        def wrapper(*args, **kwargs):
            elapsed = time.time() - last_call[0]
            wait = min_interval - elapsed
            if wait > 0:
                logger.debug("Rate limit: waiting %s seconds", wait)
                time.sleep(wait)
            ret = func(*args, **kwargs)
            last_call[0] = time.time()
            return ret

        return wrapper
    return decorator
