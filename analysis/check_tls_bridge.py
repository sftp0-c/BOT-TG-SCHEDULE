# -*- coding: utf-8 -*-
"""Разовая проба TLS-моста через TgWsProxy (127.0.0.1:1443).

Идея: если 1443 — «прозрачный» TCP-туннель до api.telegram.org, то
TLS-рукопожатие с SNI=api.telegram.org должно пройти и HTTPS-запрос вернётся.

Запуск: .venv\\Scripts\\python.exe -X utf8 analysis\\check_tls_bridge.py
"""
from __future__ import annotations

import socket
import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HOST, PORT = "127.0.0.1", 1443


def probe_tls(server_name: str) -> None:
    print(f"--- TLS через 127.0.0.1:1443, SNI={server_name} ---")
    try:
        context = ssl.create_default_context()
        with socket.create_connection((HOST, PORT), timeout=15) as raw:
            with context.wrap_socket(raw, server_hostname=server_name) as tls:
                cert = tls.getpeercert()
                subject = dict(x[0] for x in cert.get("subject", ()))
                print("TLS ок, сертификат:", subject.get("commonName"))
                tls.sendall(
                    f"GET / HTTP/1.1\r\nHost: {server_name}\r\n"
                    "Connection: close\r\n\r\n".encode())
                answer = tls.recv(400)
                print("ответ:", answer[:300])
    except Exception as error:  # noqa: BLE001 — разовый скрипт
        print("не получилось:", type(error).__name__, error)


def main() -> None:
    for name in ("api.telegram.org", "web.telegram.org"):
        probe_tls(name)


if __name__ == "__main__":
    main()
