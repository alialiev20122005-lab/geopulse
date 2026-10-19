import sys
import socket

# Обеспечиваем корректный вывод UTF-8 в консоли Windows
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import uvicorn

def get_local_ip():
    """Получение локального IP адреса в Wi-Fi сети для тестирования с телефона."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

if __name__ == "__main__":
    local_ip = get_local_ip()
    port = 8000

    print("=" * 65)
    print(">>> GeoPulse MVP - Сервер запущен!")
    print("=" * 65)
    print(f">> На компьютере откройте:        http://127.0.0.1:{port}")
    print(f">> С телефона в одной Wi-Fi сети: http://{local_ip}:{port}")
    print("=" * 65)
    print("Доступные демо-страницы:")
    print(f" - Панель управления:      http://127.0.0.1:{port}")
    print(f" - Кафе Daikon:            http://127.0.0.1:{port}/r/daikon")
    print(f" - Печать тейбл-тента:     http://127.0.0.1:{port}/print/daikon")
    print(f" - Парикмахерская Picasso: http://127.0.0.1:{port}/r/picasso")
    print(f" - Автосервис Katoff:      http://127.0.0.1:{port}/r/katoff")
    print("=" * 65)

    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
