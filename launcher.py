from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Final

import ctypes

if getattr(sys, "frozen", False):
    ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    ROOT_DIR = Path(__file__).resolve().parent

APP_DIR = ROOT_DIR / "Incidencia"

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.chdir(APP_DIR)

from Incidencia.app import create_app, init_db  # noqa: E402

HOST: Final[str] = "127.0.0.1"
PORT: Final[int] = 8080
STARTUP_TIMEOUT_SECONDS: Final[float] = 15.0


def show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, "IncidenciaApp", 0x10)


def wait_for_server(host: str, port: int, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False

def run_app() -> None:
    app = create_app()
    init_db(app)
    app.run(debug=False, host=HOST, port=PORT)

if __name__ == "__main__":
    startup_error: list[BaseException] = []

    def app_worker() -> None:
        try:
            run_app()
        except BaseException as exc:
            startup_error.append(exc)

    thread = threading.Thread(target=app_worker, daemon=True)
    thread.start()

    if wait_for_server(HOST, PORT, STARTUP_TIMEOUT_SECONDS):
        webbrowser.open(f"http://{HOST}:{PORT}")
    else:
        detail = str(startup_error[0]) if startup_error else (
            f"La aplicacion no pudo iniciar en http://{HOST}:{PORT}.\n"
            "Verifica que el puerto 8080 no este en uso."
        )
        show_error(detail)

    thread.join()
