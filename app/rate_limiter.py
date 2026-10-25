from collections import defaultdict
import time
from typing import Dict, List, Optional

class InMemoryRateLimiter:
    """
    Легковесный in-memory rate limiter без внешних зависимостей.
    Защищает от флуда и брутфорса, автоматически очищает устаревшие IP,
    предотвращая утечки памяти на сервере.
    """
    def __init__(self, requests_per_minute: int = 30, max_keys: int = 3000):
        self.rpm = requests_per_minute
        self.max_keys = max_keys
        self.history: Dict[str, List[float]] = defaultdict(list)

    def check(self, client_ip: str) -> bool:
        if not client_ip:
            return True
        now = time.time()
        window_start = now - 60
        timestamps = [t for t in self.history.get(client_ip, []) if t > window_start]
        if len(timestamps) >= self.rpm:
            self.history[client_ip] = timestamps
            return False
        timestamps.append(now)
        self.history[client_ip] = timestamps

        # Очистка устаревших ключей при превышении порога уникальных IP
        if len(self.history) > self.max_keys:
            expired_keys = [k for k, v in self.history.items() if not v or v[-1] <= window_start]
            for k in expired_keys:
                self.history.pop(k, None)
        return True

    def reset(self, client_ip: Optional[str] = None):
        if client_ip:
            self.history.pop(client_ip, None)
        else:
            self.history.clear()

# Глобальные инстансы для различных эндпоинтов
feedback_rate_limiter = InMemoryRateLimiter(requests_per_minute=6)
pilot_rate_limiter = InMemoryRateLimiter(requests_per_minute=10)
admin_login_rate_limiter = InMemoryRateLimiter(requests_per_minute=5)
