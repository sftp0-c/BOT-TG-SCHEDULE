# -*- coding: utf-8 -*-
"""Разовая проверка локального TgWsProxy (127.0.0.1:1443): что это за протокол
и пускает ли он до api.telegram.org.

Запуск: .venv\\Scripts\\python.exe -X utf8 analysis\\check_tgproxy.py
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HOST, PORT = "127.0.0.1", 1443


def socks5_probe() -> bool:
    """Слушатель понимает SOCKS5-приветствие?"""
    try:
        with socket.create_connection((HOST, PORT), timeout=5) as sock:
            sock.sendall(b"\x05\x01\x00")
            data = sock.recv(2)
            ok = data == b"\x05\x00"
            print(f"SOCKS5-приветствие: {data.hex()!r} -> {'да' if ok else 'нет'}")
            return ok
    except OSError as error:
        print("SOCKS5-проба не удалась:", error)
        return False


def http_connect_probe() -> bool:
    """Слушатель понимает HTTP CONNECT?"""
    try:
        with socket.create_connection((HOST, PORT), timeout=8) as sock:
            sock.sendall(b"CONNECT api.telegram.org:443 HTTP/1.1\r\n"
                         b"Host: api.telegram.org:443\r\n\r\n")
            head = sock.recv(300).split(b"\r\n", 1)[0].decode("latin-1", "replace")
            print("HTTP CONNECT ответ:", head)
            return " 200" in head
    except OSError as error:
        print("HTTP CONNECT проба не удалась:", error)
        return False


def main() -> None:
    socks = socks5_probe()
    httpc = http_connect_probe()
    if not (socks or httpc):
        print("Это не SOCKS5 и не HTTP-прокси (возможно, сырой TCP-туннель).")
        return

    import httpx
    scheme = "socks5://" if socks else "http://"
    proxy = f"{scheme}{HOST}:{PORT}"
    token = [line for line in Path(".env").read_text(encoding="utf-8").splitlines()
             if line.startswith("TG_BOT_TOKEN=")][0].split("=", 1)[1].strip()
    try:
        response = httpx.get(f"https://api.telegram.org/bot{token}/getMe",
                             proxy=proxy, timeout=20)
        print(f"getMe через {proxy}: {response.status_code} {response.text[:200]}")
    except Exception as error:  # noqa: BLE001 — разовый скрипт
        print(f"getMe через {proxy}: ОШИБКА {type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
