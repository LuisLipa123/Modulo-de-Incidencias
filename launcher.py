from __future__ import annotations

import ctypes
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Final

if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys.executable).resolve().parent
else:
    ROOT_DIR = Path(__file__).resolve().parent

LOG_FILE = ROOT_DIR / "app.log"

HOST: Final[str] = "127.0.0.1"
PORT: Final[int] = 8080
STARTUP_TIMEOUT_SECONDS: Final[float] = 30.0
APP_URL: Final[str] = f"http://{HOST}:{PORT}"


def show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "IncidenciaApp", 0x10)
    else:
        print(message, file=sys.stderr)


def resolve_app_dir(root: Path) -> Path:
    candidates = (root / "Incidencia", root.parent / "Incidencia")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        f"No se encontro la carpeta de la aplicacion en {root} ni en {root.parent}."
    )


def resolve_python_commands() -> list[list[str]]:
    candidates: list[list[str]] = []
    if not getattr(sys, "frozen", False):
        candidates.append([sys.executable])
    override = os.getenv("PYTHON")
    if override:
        candidates.append(shlex.split(override, posix=False))
    candidates.extend([["python3"], ["python"], ["py", "-3"]])
    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for cmd in candidates:
        key = tuple(cmd)
        if key in seen:
            continue
        seen.add(key)
        unique.append(cmd)
    return unique


def command_available(cmd: list[str]) -> bool:
    if Path(cmd[0]).is_file():
        return True
    return shutil.which(cmd[0]) is not None


def ensure_venv(python_cmds: list[list[str]]) -> Path:
    for name in ("venv", ".venv"):
        candidate = ROOT_DIR / name
        if candidate.exists():
            python_path = venv_python_path(candidate)
            if python_path.exists():
                return candidate
            shutil.rmtree(candidate, ignore_errors=True)
    venv_path = ROOT_DIR / ".venv"
    last_error: BaseException | None = None
    for cmd in python_cmds:
        if not command_available(cmd):
            continue
        try:
            subprocess.run(cmd + ["-m", "venv", str(venv_path)], check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            last_error = exc
            continue
        python_path = venv_python_path(venv_path)
        if python_path.exists():
            return venv_path
        shutil.rmtree(venv_path, ignore_errors=True)
    if last_error:
        raise FileNotFoundError(
            "No se pudo crear el entorno virtual. Verifica que Python 3 este instalado y en PATH."
        ) from last_error
    raise FileNotFoundError("Python 3 no esta disponible en el PATH.")


def venv_python_path(venv_path: Path) -> Path:
    if os.name == "nt":
        python_path = venv_path / "Scripts" / "python.exe"
    else:
        python_path = venv_path / "bin" / "python"
    return python_path


def install_dependencies(python_path: Path, requirements_file: Path) -> None:
    subprocess.run(
        [str(python_path), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
        check=True,
    )
    subprocess.run(
        [str(python_path), "-m", "pip", "install", "--quiet", "-r", str(requirements_file)],
        check=True,
    )


def start_server(python_path: Path, app_dir: Path):
    script = (
        "from app import create_app, init_db\n"
        "app = create_app()\n"
        "init_db(app)\n"
        "app.run(debug=False, port=8080)\n"
    )
    log_handle = LOG_FILE.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [str(python_path), "-c", script],
        cwd=str(app_dir),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    return process, log_handle


def wait_for_server(host: str, port: int, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def main() -> None:
    app_dir = resolve_app_dir(ROOT_DIR)
    requirements_file = app_dir / "requirements.txt"
    python_cmds = resolve_python_commands()
    venv_path = ensure_venv(python_cmds)
    venv_python = venv_python_path(venv_path)
    if not venv_python.exists():
        raise FileNotFoundError(f"No se encontro el Python del entorno virtual en {venv_python}.")
    install_dependencies(venv_python, requirements_file)
    process, log_handle = start_server(venv_python, app_dir)
    try:
        wait_for_server(HOST, PORT, STARTUP_TIMEOUT_SECONDS)
        webbrowser.open(APP_URL)
        process.wait()
    finally:
        log_handle.close()


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        show_error(str(exc))
        raise
    except subprocess.CalledProcessError as exc:
        show_error(f"No se pudieron instalar dependencias o iniciar la app.\n{exc}")
        raise
