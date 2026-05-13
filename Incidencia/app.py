from __future__ import annotations

import csv
import io
import os
import re
import secrets
import shutil
import warnings
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import urlsplit

from flask import Flask, Response, flash, g, redirect, render_template, request, send_from_directory, session, url_for
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DEFAULT_SQLITE_DATABASE_URL = f"sqlite:///{(BASE_DIR / 'incidencias.db').as_posix()}"

ESTADOS = ["Abierta", "En progreso", "Resuelta", "Cerrada"]
ESTADOS_CREAR = ["Abierta", "En progreso"]
PRIORIDADES = ["Baja", "Media", "Alta", "Critica"]
ROLES = ["Administrador", "Técnico", "Operador"]
TIPOS_INCIDENCIA = ["Hardware", "Software", "Red", "Seguridad", "Acceso", "Otro"]
TRANSICIONES_VALIDAS: dict[str, list[str]] = {
    "Abierta": ["En progreso"],
    "En progreso": ["Resuelta"],
    "Resuelta": ["Cerrada"],
    "Cerrada": [],
}

MIN_TITULO_LENGTH = 5
MAX_TITULO_LENGTH = 150
MIN_DESCRIPCION_LENGTH = 10
MAX_DESCRIPCION_LENGTH = 2000
MIN_RESPONSABLE_LENGTH = 3
MAX_RESPONSABLE_LENGTH = 120
MAX_NOMBRE_COMPLETO_LENGTH = 200
MAX_USERNAME_LENGTH = 50
MIN_USERNAME_LENGTH = 3
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MIN_SOLUCION_LENGTH = 20
HTML_ANGLE_RE = re.compile(r"[<>]")
CONSECUTIVE_SPACES_RE = re.compile(r" {2,}")
TITLE_TRAILING_PUNCT_RE = re.compile(r"[.,;]$")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
CONTROL_CHARS_RE = re.compile(r"[\t\n\r]")
URL_RE = re.compile(r"https?://")
SPECIAL_CHAR_RE = re.compile(r"[!@#$%^&*()\-_=+\[\]{}|;:,.<>?/`~]")
MIN_DESCRIPCION_CRITICA = 30
MIN_DESCRIPCION_EN_PROGRESO = 20
COMMON_PASSWORDS = frozenset({
    "12345678", "password", "password1", "qwerty123",
    "letmein1", "welcome1", "iloveyou1", "sunshine1",
})
TITULO_RESERVED_WORDS = frozenset(v.lower() for v in ESTADOS + PRIORIDADES)
REPEATED_CHAR_RE = re.compile(r"(.)\1{3,}")
REPEATED_PUNCT_RE = re.compile(r"([!?.])\1+$")
CONSECUTIVE_SPECIAL_RE = re.compile(r"[._-]{2,}")
MIN_DESCRIPCION_CERRADA_CRITICA = 40
MAX_INCIDENCIAS_POR_USUARIO = 50

db = SQLAlchemy()
ViewFunc = TypeVar("ViewFunc", bound=Callable[..., Any])


def build_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url
    postgres_password = os.getenv("POSTGRES_PASSWORD")
    if not postgres_password:
        return DEFAULT_SQLITE_DATABASE_URL
    postgres_user = os.getenv("POSTGRES_USER", "postgres")
    postgres_host = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port = os.getenv("POSTGRES_PORT", "5432")
    postgres_db = os.getenv("POSTGRES_DB", "incidencias")
    return (
        f"postgresql+psycopg://{postgres_user}:{postgres_password}"
        f"@{postgres_host}:{postgres_port}/{postgres_db}"
    )


# BUG-050: tabla independiente de roles
class Rol(db.Model):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(200), nullable=True)

    usuarios: Mapped[list["Usuario"]] = relationship("Usuario", back_populates="rol_obj", lazy="select")


# BUG-121: tabla de sucursales
class Sucursal(db.Model):
    __tablename__ = "sucursales"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    codigo: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    activa: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# BUG-109: auditoría de intentos de inicio de sesión
class AuditoriaLogin(db.Model):
    __tablename__ = "auditoria_logins"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    exitoso: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    accion: Mapped[str | None] = mapped_column(String(20), nullable=True, default="login")
    fecha: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class Usuario(db.Model):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nombre_completo: Mapped[str] = mapped_column(String(200), nullable=False)
    # BUG-050: FK a tabla roles
    rol_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    # BUG-032: campo activo para habilitar/deshabilitar usuarios
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # BUG-121: FK a sucursal
    sucursal_id: Mapped[int | None] = mapped_column(ForeignKey("sucursales.id"), nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    rol_obj: Mapped["Rol"] = relationship("Rol", back_populates="usuarios", lazy="select")
    sucursal: Mapped["Sucursal | None"] = relationship("Sucursal", foreign_keys=[sucursal_id], lazy="select")

    @property
    def rol(self) -> str:
        return self.rol_obj.nombre if self.rol_obj else ""


class Incidencia(db.Model):
    __tablename__ = "incidencias"
    # BUG-046 / BUG-117: restricciones CHECK en base de datos
    __table_args__ = (
        CheckConstraint(
            "estado IN ('Abierta', 'En progreso', 'Resuelta', 'Cerrada')",
            name="ck_incidencias_estado",
        ),
        CheckConstraint(
            "prioridad IN ('Baja', 'Media', 'Alta', 'Critica')",
            name="ck_incidencias_prioridad",
        ),
        CheckConstraint(
            "tipo IN ('Hardware', 'Software', 'Red', 'Seguridad', 'Acceso', 'Otro')",
            name="ck_incidencias_tipo",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    titulo: Mapped[str] = mapped_column(String(150), nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(String(50), nullable=False)
    responsable_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    estado: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    prioridad: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # BUG-091: campo solución requerido para Resuelta/Cerrada
    solucion: Mapped[str | None] = mapped_column(Text, nullable=True)
    usuario_reportante_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), nullable=False)
    # BUG-121: FK a sucursal
    sucursal_id: Mapped[int | None] = mapped_column(ForeignKey("sucursales.id"), nullable=True)
    eliminado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
    # BUG-033: fecha de última actualización
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    # BUG-034: fecha de cierre (solo cuando estado = Cerrada)
    fecha_cierre: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    responsable_user: Mapped["Usuario | None"] = relationship(
        "Usuario", foreign_keys=[responsable_id], lazy="select"
    )
    usuario_reportante: Mapped["Usuario"] = relationship(
        "Usuario", foreign_keys=[usuario_reportante_id], lazy="select"
    )
    sucursal_obj: Mapped["Sucursal | None"] = relationship(
        "Sucursal", foreign_keys=[sucursal_id], lazy="select"
    )
    historial: Mapped[list["HistorialCambio"]] = relationship(
        "HistorialCambio", back_populates="incidencia", lazy="select",
        order_by="HistorialCambio.fecha",
    )


class HistorialCambio(db.Model):
    __tablename__ = "historial_cambios"

    id: Mapped[int] = mapped_column(primary_key=True)
    incidencia_id: Mapped[int] = mapped_column(ForeignKey("incidencias.id"), nullable=False)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), nullable=False)
    campo: Mapped[str] = mapped_column(String(50), nullable=False)
    valor_anterior: Mapped[str | None] = mapped_column(Text, nullable=True)
    valor_nuevo: Mapped[str | None] = mapped_column(Text, nullable=True)
    fecha: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    incidencia: Mapped["Incidencia"] = relationship("Incidencia", back_populates="historial")
    usuario: Mapped["Usuario"] = relationship("Usuario", foreign_keys=[usuario_id])


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)

    # BUG-037: secret key sin valor hardcodeado
    _secret = os.getenv("SECRET_KEY")
    if not _secret:
        _secret = secrets.token_hex(32)
        warnings.warn(
            "SECRET_KEY no definida en el entorno. Se usa una clave aleatoria — "
            "las sesiones no persisten entre reinicios. Define SECRET_KEY en .env.",
            UserWarning,
            stacklevel=1,
        )

    app.config.update(
        SECRET_KEY=_secret,
        SQLALCHEMY_DATABASE_URI=build_database_url(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        DEFAULT_ADMIN_USERNAME=os.getenv("DEFAULT_ADMIN_USERNAME", "admin"),
        DEFAULT_ADMIN_PASSWORD=os.getenv("DEFAULT_ADMIN_PASSWORD", "Admin@2025!"),
        DEFAULT_ADMIN_NAME=os.getenv("DEFAULT_ADMIN_NAME", "Administrador General"),
        # BUG-189: sesiones con tiempo de vida definido
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        # BUG-190: cookies seguras
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",
    )

    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    register_template_context(app)
    register_routes(app)
    return app


def register_template_context(app: Flask) -> None:
    @app.context_processor
    def inject_template_context() -> dict[str, Any]:
        return {"current_user": get_current_user(), "csrf_token": get_csrf_token()}


def get_csrf_token() -> str:
    token = session.get("csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def is_csrf_valid() -> bool:
    submitted_token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    session_token = session.get("csrf_token")
    return bool(session_token and submitted_token and secrets.compare_digest(submitted_token, session_token))


def commit_with_handling(success_message: str, error_message: str) -> bool:
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash(error_message, "error")
        return False
    flash(success_message, "success")
    return True


def get_current_user() -> Usuario | None:
    if "current_user" not in g:
        user_id = session.get("user_id")
        g.current_user = db.session.get(Usuario, user_id) if user_id else None
    return g.current_user


def login_required(view: ViewFunc) -> ViewFunc:
    @wraps(view)
    def wrapped_view(*args: Any, **kwargs: Any) -> Any:
        if get_current_user() is None:
            flash("Inicia sesion para continuar.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped_view  # type: ignore[return-value]


def admin_required(view: ViewFunc) -> ViewFunc:
    @wraps(view)
    def wrapped_view(*args: Any, **kwargs: Any) -> Any:
        user = get_current_user()
        if user is None:
            flash("Inicia sesion para continuar.", "error")
            return redirect(url_for("login", next=request.path))
        if user.rol != "Administrador":
            flash("No tienes permisos para realizar esta accion.", "error")
            return redirect(url_for("listar_incidencias"))
        return view(*args, **kwargs)
    return wrapped_view  # type: ignore[return-value]


def create_user(
    username: str,
    password: str,
    nombre_completo: str,
    rol: str = "Operador",
    activo: bool = True,
    sucursal_id: int | None = None,
) -> Usuario:
    rol_obj = Rol.query.filter_by(nombre=rol).first()
    user = Usuario(
        username=username,
        nombre_completo=nombre_completo,
        password_hash=generate_password_hash(password),
        rol_id=rol_obj.id if rol_obj else 1,
        activo=activo,
        sucursal_id=sucursal_id,
    )
    db.session.add(user)
    return user


_DUMMY_HASH = generate_password_hash("dummy-timing-protection-hash")


def authenticate_user(username: str, password: str) -> Usuario | None:
    user = Usuario.query.filter_by(username=username).first()
    # BUG-187: comparar siempre un hash para evitar timing attacks
    candidate_hash = user.password_hash if user else _DUMMY_HASH
    if not check_password_hash(candidate_hash, password) or user is None:
        return None
    return user


def seed_roles() -> None:
    roles_data = [
        ("Administrador", "Acceso completo al sistema"),
        ("Técnico", "Puede gestionar incidencias asignadas"),
        ("Operador", "Puede registrar y consultar incidencias"),
    ]
    for nombre, desc in roles_data:
        if not Rol.query.filter_by(nombre=nombre).first():
            db.session.add(Rol(nombre=nombre, descripcion=desc))
    db.session.commit()


def seed_sucursales() -> None:
    if Sucursal.query.count() == 0:
        db.session.add(Sucursal(nombre="Sede Principal", codigo="SEDE-01"))
        db.session.commit()


def ensure_default_user(app: Flask) -> None:
    admin_username = app.config["DEFAULT_ADMIN_USERNAME"]
    existing_user = Usuario.query.filter_by(username=admin_username).first()
    if existing_user is not None:
        return
    create_user(
        admin_username,
        app.config["DEFAULT_ADMIN_PASSWORD"],
        app.config["DEFAULT_ADMIN_NAME"],
        rol="Administrador",
        activo=True,
    )
    db.session.commit()


def init_db(app: Flask) -> None:
    with app.app_context():
        db.create_all()
        seed_roles()
        seed_sucursales()
        ensure_default_user(app)


def generate_codigo() -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"INC-{today}-"
    count = Incidencia.query.filter(Incidencia.codigo.like(f"{prefix}%")).count()
    return f"{prefix}{count + 1:05d}"


def get_tecnicos() -> list[Usuario]:
    return (
        Usuario.query
        .join(Rol)
        .filter(Rol.nombre.in_(["Administrador", "Técnico"]), Usuario.activo == True)
        .order_by(Usuario.nombre_completo)
        .all()
    )


def get_sucursales() -> list[Sucursal]:
    return Sucursal.query.filter_by(activa=True).order_by(Sucursal.nombre).all()


def registrar_cambios(
    incidencia_id: int,
    old_data: dict[str, str],
    new_data: dict[str, str],
    usuario_id: int,
) -> None:
    campos = ["titulo", "descripcion", "tipo", "responsable_id", "estado", "prioridad", "solucion", "sucursal_id"]
    for campo in campos:
        old_val = str(old_data.get(campo, "") or "")
        new_val = str(new_data.get(campo, "") or "")
        if old_val != new_val:
            db.session.add(HistorialCambio(
                incidencia_id=incidencia_id,
                usuario_id=usuario_id,
                campo=campo,
                valor_anterior=old_val,
                valor_nuevo=new_val,
            ))


def _register_login_audit(username: str, exitoso: bool, accion: str = "login") -> None:
    ip = request.remote_addr or "desconocido"
    db.session.add(AuditoriaLogin(username=username, exitoso=exitoso, ip_address=ip, accion=accion))
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()


def validate_max_length(value: str, max_length: int, field_label: str) -> str | None:
    if len(value) > max_length:
        return f"{field_label} no puede superar {max_length} caracteres."
    return None


def validate_min_length(value: str, min_length: int, field_label: str) -> str | None:
    if value and len(value) < min_length:
        return f"{field_label} debe tener al menos {min_length} caracteres."
    return None


# BUG-104: política de contraseña robusta (para registro y reseteo)
def validate_password_strength(password: str) -> str | None:
    if not re.search(r"[A-Z]", password):
        return "La contrasena debe contener al menos una letra mayuscula."
    if not re.search(r"[a-z]", password):
        return "La contrasena debe contener al menos una letra minuscula."
    if not re.search(r"\d", password):
        return "La contrasena debe contener al menos un digito."
    if not SPECIAL_CHAR_RE.search(password):
        return "La contrasena debe contener al menos un caracter especial (!@#$%^&*...)."
    return None


def validate_incidencia(form_data: dict[str, str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    titulo = form_data["titulo"].strip()
    descripcion = form_data["descripcion"].strip()
    tipo = form_data.get("tipo", "")
    solucion = form_data.get("solucion", "").strip()

    if not titulo:
        errors["titulo"] = "El titulo es obligatorio."
    if not descripcion:
        errors["descripcion"] = "La descripcion es obligatoria."
    if tipo not in TIPOS_INCIDENCIA:
        errors["tipo"] = "Seleccione un tipo de incidencia valido."

    titulo_min_length_error = validate_min_length(titulo, MIN_TITULO_LENGTH, "El titulo")
    if titulo_min_length_error is not None:
        errors["titulo"] = titulo_min_length_error

    titulo_length_error = validate_max_length(titulo, MAX_TITULO_LENGTH, "El titulo")
    if titulo_length_error is not None:
        errors["titulo"] = titulo_length_error

    descripcion_min_length_error = validate_min_length(
        descripcion, MIN_DESCRIPCION_LENGTH, "La descripcion"
    )
    if descripcion_min_length_error is not None:
        errors["descripcion"] = descripcion_min_length_error

    descripcion_length_error = validate_max_length(
        descripcion, MAX_DESCRIPCION_LENGTH, "La descripcion"
    )
    if descripcion_length_error is not None:
        errors["descripcion"] = descripcion_length_error

    if form_data["estado"] not in ESTADOS:
        errors["estado"] = "Seleccione un estado valido."
    if form_data["prioridad"] not in PRIORIDADES:
        errors["prioridad"] = "Seleccione una prioridad valida."

    if form_data["estado"] == "Resuelta" and "descripcion" not in errors and len(descripcion) < 15:
        errors["descripcion"] = "La descripcion debe explicar la resolucion con al menos 15 caracteres."

    if form_data["estado"] == "Cerrada" and "descripcion" not in errors and len(descripcion) < 20:
        errors["descripcion"] = "La descripcion debe documentar el cierre con al menos 20 caracteres."

    # BUG-091: solución requerida en estados Resuelta y Cerrada
    if form_data["estado"] in ("Resuelta", "Cerrada") and "solucion" not in errors:
        if not solucion or len(solucion) < MIN_SOLUCION_LENGTH:
            errors["solucion"] = (
                f"Debe describir la solucion aplicada con al menos {MIN_SOLUCION_LENGTH} caracteres."
            )

    if titulo and "titulo" not in errors and not re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", titulo):
        errors["titulo"] = "El titulo debe contener al menos una letra."

    if titulo and "titulo" not in errors and HTML_ANGLE_RE.search(titulo):
        errors["titulo"] = "El titulo no puede contener los caracteres '<' ni '>'."

    if titulo and "titulo" not in errors and TITLE_TRAILING_PUNCT_RE.search(titulo):
        errors["titulo"] = "El titulo no debe terminar con punto, coma ni punto y coma."

    if descripcion and "descripcion" not in errors and HTML_ANGLE_RE.search(descripcion):
        errors["descripcion"] = "La descripcion no puede contener los caracteres '<' ni '>'."

    if titulo and descripcion and "descripcion" not in errors and descripcion.lower() == titulo.lower():
        errors["descripcion"] = "La descripcion no puede ser identica al titulo."

    if titulo and "titulo" not in errors and CONTROL_CHARS_RE.search(titulo):
        errors["titulo"] = "El titulo no puede contener tabulaciones ni saltos de linea."

    if titulo and "titulo" not in errors and URL_RE.search(titulo):
        errors["titulo"] = "El titulo no puede contener URLs."

    if descripcion and "descripcion" not in errors and CONTROL_CHARS_RE.search(descripcion):
        errors["descripcion"] = "La descripcion no puede contener tabulaciones ni saltos de linea."

    if descripcion and "descripcion" not in errors and len(descripcion.split()) < 3:
        errors["descripcion"] = "La descripcion debe contener al menos 3 palabras."

    if form_data["prioridad"] == "Critica" and "descripcion" not in errors and len(descripcion) < MIN_DESCRIPCION_CRITICA:
        errors["descripcion"] = (
            f"Las incidencias criticas requieren una descripcion de al menos {MIN_DESCRIPCION_CRITICA} caracteres."
        )

    if form_data["estado"] == "En progreso" and "descripcion" not in errors:
        if len(descripcion) < MIN_DESCRIPCION_EN_PROGRESO:
            errors["descripcion"] = "Las incidencias en progreso requieren una descripcion de al menos 20 caracteres."

    if titulo and "titulo" not in errors and len(titulo.split()) < 2:
        errors["titulo"] = "El titulo debe contener al menos dos palabras."

    if titulo and "titulo" not in errors and len(titulo.split()) > 10:
        errors["titulo"] = "El titulo no puede superar 10 palabras."

    if titulo and "titulo" not in errors and any(c.isalpha() for c in titulo) and all(c.isupper() or not c.isalpha() for c in titulo):
        errors["titulo"] = "El titulo no puede estar escrito enteramente en mayusculas."

    if descripcion and "descripcion" not in errors:
        _words = descripcion.lower().split()
        if len(_words) > 1 and len(set(_words)) == 1:
            errors["descripcion"] = "La descripcion no puede consistir en la misma palabra repetida."

    if descripcion and "descripcion" not in errors and any(len(w) > 50 for w in descripcion.split()):
        errors["descripcion"] = "La descripcion no puede contener palabras de mas de 50 caracteres."

    if descripcion and "descripcion" not in errors and not (descripcion[0].isalpha() or descripcion[0].isdigit()):
        errors["descripcion"] = "La descripcion debe comenzar con una letra o numero."

    if titulo and "titulo" not in errors and titulo[0].isalpha() and not titulo[0].isupper():
        errors["titulo"] = "El titulo debe comenzar con una letra mayuscula."

    if titulo and "titulo" not in errors and REPEATED_CHAR_RE.search(titulo):
        errors["titulo"] = "El titulo no puede contener el mismo caracter repetido 4 o mas veces seguidas."

    if titulo and "titulo" not in errors and re.search(r"\d{5,}", titulo):
        errors["titulo"] = "El titulo no puede contener 5 o mas digitos consecutivos."

    if titulo and "titulo" not in errors and titulo.lower() in TITULO_RESERVED_WORDS:
        errors["titulo"] = "El titulo no puede coincidir con el nombre de un estado o prioridad."

    if descripcion and "descripcion" not in errors and descripcion[0].isalpha() and not descripcion[0].isupper():
        errors["descripcion"] = "La descripcion debe comenzar con una letra mayuscula."

    if descripcion and "descripcion" not in errors:
        _dwords = descripcion.lower().split()
        if len(_dwords) >= 4 and len(set(_dwords)) / len(_dwords) < 0.5:
            errors["descripcion"] = "La descripcion contiene demasiadas palabras repetidas."

    if descripcion and "descripcion" not in errors and URL_RE.search(descripcion):
        errors["descripcion"] = "La descripcion no puede contener URLs."

    if descripcion and "descripcion" not in errors and REPEATED_PUNCT_RE.search(descripcion):
        errors["descripcion"] = "La descripcion no puede terminar con signos de puntuacion repetidos."

    if (
        form_data["estado"] == "Cerrada"
        and form_data["prioridad"] == "Critica"
        and "descripcion" not in errors
        and len(descripcion) < MIN_DESCRIPCION_CERRADA_CRITICA
    ):
        errors["descripcion"] = (
            f"Las incidencias criticas cerradas requieren una descripcion de al menos "
            f"{MIN_DESCRIPCION_CERRADA_CRITICA} caracteres."
        )

    return errors


def extract_form_data(source: Any | None = None) -> dict[str, str]:
    if isinstance(source, Incidencia):
        return {
            "titulo": source.titulo,
            "descripcion": source.descripcion,
            "tipo": source.tipo,
            "responsable_id": str(source.responsable_id) if source.responsable_id else "",
            "estado": source.estado,
            "prioridad": source.prioridad,
            "solucion": source.solucion or "",
            "sucursal_id": str(source.sucursal_id) if source.sucursal_id else "",
        }

    source = source or {}
    getter = source.get if hasattr(source, "get") else lambda key, default="": getattr(source, key, default)

    return {
        "titulo": getter("titulo", "").strip(),
        "descripcion": getter("descripcion", "").strip(),
        "tipo": getter("tipo", TIPOS_INCIDENCIA[0]),
        "responsable_id": getter("responsable_id", ""),
        "estado": getter("estado", ESTADOS[0]),
        "prioridad": getter("prioridad", PRIORIDADES[1]),
        "solucion": getter("solucion", "").strip(),
        "sucursal_id": getter("sucursal_id", ""),
    }


def validate_login(username: str, password: str) -> dict[str, str]:
    errors: dict[str, str] = {}
    username_stripped = username.strip()

    if not username_stripped:
        errors["username"] = "El usuario es obligatorio."
    else:
        min_err = validate_min_length(username_stripped, MIN_USERNAME_LENGTH, "El usuario")
        if min_err:
            errors["username"] = min_err
        max_err = validate_max_length(username_stripped, MAX_USERNAME_LENGTH, "El usuario")
        if max_err:
            errors["username"] = max_err
        if not errors.get("username") and re.search(r"\s", username_stripped):
            errors["username"] = "El usuario no puede contener espacios."
        if not errors.get("username") and not USERNAME_PATTERN.fullmatch(username_stripped):
            errors["username"] = "El usuario solo puede contener letras, numeros, punto, guion y guion bajo."
        if not errors.get("username") and username_stripped.isdigit():
            errors["username"] = "El usuario no puede estar compuesto solo de numeros."
        if not errors.get("username") and (username_stripped[0] in "._-" or username_stripped[-1] in "._-"):
            errors["username"] = "El usuario no puede comenzar ni terminar con punto, guion ni guion bajo."
        if not errors.get("username") and CONSECUTIVE_SPECIAL_RE.search(username_stripped):
            errors["username"] = "El usuario no puede tener punto, guion ni guion bajo consecutivos."

    if not password:
        errors["password"] = "La contrasena es obligatoria."
    else:
        pass_min_err = validate_min_length(password, MIN_PASSWORD_LENGTH, "La contrasena")
        if pass_min_err:
            errors["password"] = pass_min_err
        pass_max_err = validate_max_length(password, MAX_PASSWORD_LENGTH, "La contrasena")
        if pass_max_err:
            errors["password"] = pass_max_err
        if (
            not errors.get("password")
            and not errors.get("username")
            and password.lower() == username_stripped.lower()
        ):
            errors["password"] = "La contrasena no puede ser igual al nombre de usuario."
        if not errors.get("password") and password.lower() in COMMON_PASSWORDS:
            errors["password"] = "La contrasena es demasiado comun. Elige una mas segura."
        if not errors.get("password") and password.isalpha():
            errors["password"] = "La contrasena debe contener al menos un caracter no alfabetico."

    return errors


def validate_nombre_completo(nombre: str) -> str | None:
    nombre_stripped = nombre.strip()
    if not nombre_stripped:
        return "El nombre completo es obligatorio."

    min_err = validate_min_length(nombre_stripped, MIN_RESPONSABLE_LENGTH, "El nombre completo")
    if min_err:
        return min_err

    # BUG-052: límite ampliado para nombres largos reales
    max_err = validate_max_length(nombre_stripped, MAX_NOMBRE_COMPLETO_LENGTH, "El nombre completo")
    if max_err:
        return max_err

    if CONTROL_CHARS_RE.search(nombre_stripped):
        return "El nombre completo no puede contener tabulaciones ni saltos de linea."

    if HTML_ANGLE_RE.search(nombre_stripped):
        return "El nombre completo no puede contener los caracteres '<' ni '>'."

    if CONSECUTIVE_SPACES_RE.search(nombre_stripped):
        return "El nombre completo no puede contener espacios consecutivos."

    return None


def normalize_local_url(target: str | None) -> str | None:
    if not target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        if parts.scheme and parts.netloc and parts.netloc == request.host:
            path = parts.path or "/"
            if path.startswith("//") or not path.startswith("/"):
                return None
            if parts.query:
                return f"{path}?{parts.query}"
            return path
        return None
    if target.startswith("//") or not target.startswith("/"):
        return None
    return target


def resolve_next_url() -> str:
    next_url = normalize_local_url(request.args.get("next") or request.form.get("next"))
    if next_url:
        return next_url
    return url_for("listar_incidencias")


def _validate_responsable_id(responsable_id_str: str) -> str | None:
    if not responsable_id_str:
        return "El responsable es obligatorio."
    try:
        responsable_id = int(responsable_id_str)
        responsable_user = db.session.get(Usuario, responsable_id)
        if responsable_user is None or responsable_user.rol not in ["Administrador", "Técnico"]:
            return "El responsable debe ser un tecnico o administrador registrado."
        if not responsable_user.activo:
            return "El responsable seleccionado no tiene una cuenta activa."
    except (ValueError, TypeError):
        return "El responsable seleccionado no es valido."
    return None


def _validate_sucursal_id(sucursal_id_str: str) -> str | None:
    # BUG-179: validación backend de sucursal independiente del frontend
    if not sucursal_id_str:
        return None
    try:
        suc = db.session.get(Sucursal, int(sucursal_id_str))
        if suc is None or not suc.activa:
            return "La sucursal seleccionada no existe o no esta activa."
    except (ValueError, TypeError):
        return "La sucursal seleccionada no es valida."
    return None


def _can_edit_incidencia(incidencia: Incidencia, current_user: Usuario) -> bool:
    """BUG-100/101: solo el creador, el responsable asignado o un admin pueden editar."""
    if current_user.rol == "Administrador":
        return True
    if incidencia.usuario_reportante_id == current_user.id:
        return True
    if incidencia.responsable_id == current_user.id:
        return True
    return False


def backup_db() -> str | None:
    # BUG-238/240: backup de base de datos SQLite con validacion de tamaño
    db_url = str(db.engine.url)
    if not db_url.startswith("sqlite"):
        return None
    db_path = BASE_DIR / "incidencias.db"
    if not db_path.exists():
        return None
    size_mb = db_path.stat().st_size / (1024 * 1024)
    if size_mb > 100:
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = BASE_DIR / f"incidencias_backup_{timestamp}.db"
    shutil.copy2(db_path, backup_path)
    return str(backup_path)


def register_routes(app: Flask) -> None:
    @app.before_request
    def check_maintenance_mode() -> Any | None:
        # BUG-285: modo mantenimiento controlado por variable de entorno
        if os.getenv("MAINTENANCE_MODE", "0") == "1":
            exempt = {"static", "favicon", "login", "mantenimiento"}
            if request.endpoint not in exempt:
                return render_template("mantenimiento.html"), 503
        return None

    @app.before_request
    def protect_post_requests() -> Any | None:
        if request.method == "POST" and not is_csrf_valid():
            flash("La sesion del formulario expiro. Intenta nuevamente.", "error")
            safe_referrer = normalize_local_url(request.referrer)
            if safe_referrer:
                return redirect(safe_referrer)
            fallback = url_for("listar_incidencias") if get_current_user() else url_for("login")
            return redirect(fallback)

    @app.get("/favicon.ico")
    def favicon() -> Any:
        return send_from_directory(app.static_folder, "favicon.svg", mimetype="image/svg+xml")

    @app.route("/")
    def home() -> Any:
        if get_current_user() is None:
            return redirect(url_for("login"))
        return redirect(url_for("listar_incidencias"))

    @app.route("/login", methods=["GET", "POST"])
    def login() -> str | Any:
        if get_current_user() is not None:
            return redirect(url_for("listar_incidencias"))

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        errors: dict[str, str] = {}

        if request.method == "POST":
            errors = validate_login(username, password)
            if not errors:
                user = authenticate_user(username, password)
                if user is None:
                    errors["general"] = "Credenciales invalidas."
                    _register_login_audit(username, exitoso=False)
                elif not user.activo:
                    # BUG-032 / BUG-102: cuenta pendiente de aprobación
                    errors["general"] = "Tu cuenta esta pendiente de aprobacion por un administrador."
                    _register_login_audit(username, exitoso=False)
                else:
                    session.clear()
                    session.permanent = True  # BUG-189: sesion con TTL definido
                    session["user_id"] = user.id
                    session["csrf_token"] = secrets.token_urlsafe(32)
                    _register_login_audit(username, exitoso=True)
                    flash(f"Bienvenido, {user.nombre_completo}.", "success")
                    return redirect(resolve_next_url())

        return render_template(
            "login.html",
            username=username,
            errors=errors,
            next_url=request.args.get("next", ""),
        )

    @app.route("/registro", methods=["GET", "POST"])
    def registro() -> str | Any:
        if get_current_user() is not None:
            return redirect(url_for("listar_incidencias"))

        form_data = {"username": "", "nombre_completo": ""}
        errors: dict[str, str] = {}

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            confirmar = request.form.get("confirmar_contrasena", "")
            nombre_completo = request.form.get("nombre_completo", "").strip()
            form_data = {"username": username, "nombre_completo": nombre_completo}

            errors = validate_login(username, password)

            # BUG-104: política de contraseña fuerte al registrarse
            if not errors.get("password"):
                strength_err = validate_password_strength(password)
                if strength_err:
                    errors["password"] = strength_err

            # BUG-105: confirmar contraseña
            if not errors.get("password") and password != confirmar:
                errors["confirmar_contrasena"] = "Las contrasenas no coinciden."

            nombre_error = validate_nombre_completo(nombre_completo)
            if nombre_error:
                errors["nombre_completo"] = nombre_error

            if not errors:
                existing = Usuario.query.filter_by(username=username).first()
                if existing is not None:
                    errors["username"] = "El usuario ya existe."

            if not errors:
                # BUG-102: cuentas nuevas inactivas hasta aprobación del admin
                create_user(username, password, nombre_completo, rol="Operador", activo=False)
                if commit_with_handling(
                    "Cuenta creada. Un administrador debe aprobarla antes de que puedas iniciar sesion.",
                    "No se pudo crear el usuario. Intenta nuevamente.",
                ):
                    return redirect(url_for("login"))

        return render_template("register.html", form_data=form_data, errors=errors)

    @app.post("/logout")
    def logout() -> Any:
        # BUG-184: registrar evento de cierre de sesion
        user = get_current_user()
        if user:
            _register_login_audit(user.username, exitoso=True, accion="logout")
        session.clear()
        flash("Sesion cerrada correctamente.", "success")
        return redirect(url_for("login"))

    @app.get("/recuperar-contrasena")
    def recuperar_contrasena() -> str:
        return render_template("recuperar_contrasena.html")

    @app.route("/incidencias")
    @login_required
    def listar_incidencias() -> str:
        # BUG-122 / BUG-123: paginación para evitar queries masivas
        page = request.args.get("page", 1, type=int)
        per_page = 20
        paginacion = (
            Incidencia.query
            .filter_by(eliminado=False)
            .order_by(Incidencia.fecha_creacion.desc(), Incidencia.id.desc())
            .paginate(page=page, per_page=per_page, error_out=False)
        )
        return render_template("index.html", incidencias=paginacion.items, paginacion=paginacion)

    @app.route("/incidencias/nueva", methods=["GET", "POST"])
    @login_required
    def crear_incidencia() -> str | Any:
        form_data = extract_form_data(request.form)
        errors: dict[str, str] = {}
        tecnicos = get_tecnicos()
        sucursales = get_sucursales()

        if request.method == "POST":
            errors = validate_incidencia(form_data)

            if not errors.get("estado") and form_data["estado"] not in ESTADOS_CREAR:
                errors["estado"] = (
                    f"Solo se puede crear incidencias en estado: {', '.join(ESTADOS_CREAR)}."
                )

            resp_err = _validate_responsable_id(form_data.get("responsable_id", ""))
            if resp_err:
                errors["responsable_id"] = resp_err

            suc_err = _validate_sucursal_id(form_data.get("sucursal_id", ""))
            if suc_err:
                errors["sucursal_id"] = suc_err

            # BUG-095 / BUG-096: sin duplicados en estados activos
            if not errors:
                existing = Incidencia.query.filter(
                    Incidencia.titulo == form_data["titulo"],
                    Incidencia.eliminado == False,
                    Incidencia.estado.in_(["Abierta", "En progreso", "Resuelta"]),
                ).first()
                if existing:
                    errors["titulo"] = (
                        "Ya existe una incidencia activa con el mismo titulo "
                        f"(estado: {existing.estado})."
                    )

            # BUG-230: limite de incidencias activas por usuario
            if not errors:
                _cur = get_current_user()
                activas = Incidencia.query.filter(
                    Incidencia.usuario_reportante_id == _cur.id,
                    Incidencia.eliminado == False,
                    Incidencia.estado != "Cerrada",
                ).count()
                if activas >= MAX_INCIDENCIAS_POR_USUARIO:
                    errors["titulo"] = (
                        f"Has alcanzado el limite de {MAX_INCIDENCIAS_POR_USUARIO} incidencias activas."
                    )

            if not errors:
                current_user = get_current_user()
                codigo = generate_codigo()
                suc_id = int(form_data["sucursal_id"]) if form_data.get("sucursal_id") else None
                incidencia = Incidencia(
                    codigo=codigo,
                    titulo=form_data["titulo"],
                    descripcion=form_data["descripcion"],
                    tipo=form_data["tipo"],
                    responsable_id=int(form_data["responsable_id"]),
                    estado=form_data["estado"],
                    prioridad=form_data["prioridad"],
                    solucion=form_data["solucion"] or None,
                    usuario_reportante_id=current_user.id,
                    sucursal_id=suc_id,
                )
                db.session.add(incidencia)
                try:
                    db.session.flush()
                    db.session.add(HistorialCambio(
                        incidencia_id=incidencia.id,
                        usuario_id=current_user.id,
                        campo="creacion",
                        valor_anterior=None,
                        valor_nuevo=f"Incidencia creada en estado '{form_data['estado']}'",
                    ))
                    db.session.commit()
                    flash("Incidencia creada correctamente.", "success")
                    return redirect(url_for("listar_incidencias"))
                except SQLAlchemyError:
                    db.session.rollback()
                    flash("No se pudo guardar la incidencia. Intenta nuevamente.", "error")

        return render_template(
            "form.html",
            title="Nueva incidencia",
            form_action=url_for("crear_incidencia"),
            incidencia=form_data,
            errors=errors,
            estados=ESTADOS_CREAR,
            prioridades=PRIORIDADES,
            tipos=TIPOS_INCIDENCIA,
            tecnicos=tecnicos,
            sucursales=sucursales,
            submit_label="Guardar",
        )

    @app.route("/incidencias/<int:incidencia_id>/editar", methods=["GET", "POST"])
    @login_required
    def editar_incidencia(incidencia_id: int) -> str | Any:
        incidencia = db.session.get(Incidencia, incidencia_id)
        if incidencia is None or incidencia.eliminado:
            flash("La incidencia solicitada no existe.", "error")
            return redirect(url_for("listar_incidencias"))

        if incidencia.estado == "Cerrada":
            flash("Las incidencias cerradas no pueden ser modificadas.", "error")
            return redirect(url_for("listar_incidencias"))

        # BUG-100 / BUG-101: solo creador, responsable o admin pueden editar
        current_user = get_current_user()
        if not _can_edit_incidencia(incidencia, current_user):
            flash("No tienes permisos para editar esta incidencia.", "error")
            return redirect(url_for("listar_incidencias"))

        form_data = extract_form_data(incidencia if request.method == "GET" else request.form)
        errors: dict[str, str] = {}
        tecnicos = get_tecnicos()
        sucursales = get_sucursales()

        if request.method == "POST":
            errors = validate_incidencia(form_data)

            if not errors.get("estado") and form_data["estado"] != incidencia.estado:
                transiciones = TRANSICIONES_VALIDAS.get(incidencia.estado, [])
                if form_data["estado"] not in transiciones:
                    permitidos = ", ".join(transiciones) if transiciones else "ninguno"
                    errors["estado"] = (
                        f"Transicion no permitida desde '{incidencia.estado}'. "
                        f"Estado(s) valido(s): {permitidos}."
                    )

            resp_err = _validate_responsable_id(form_data.get("responsable_id", ""))
            if resp_err:
                errors["responsable_id"] = resp_err

            suc_err = _validate_sucursal_id(form_data.get("sucursal_id", ""))
            if suc_err:
                errors["sucursal_id"] = suc_err

            if not errors:
                old_data = extract_form_data(incidencia)
                incidencia.titulo = form_data["titulo"]
                incidencia.descripcion = form_data["descripcion"]
                incidencia.tipo = form_data["tipo"]
                incidencia.responsable_id = int(form_data["responsable_id"])
                incidencia.prioridad = form_data["prioridad"]
                incidencia.solucion = form_data["solucion"] or None
                incidencia.sucursal_id = int(form_data["sucursal_id"]) if form_data.get("sucursal_id") else None
                incidencia.estado = form_data["estado"]
                # BUG-033: actualizar fecha_actualizacion
                incidencia.fecha_actualizacion = datetime.now(timezone.utc)
                # BUG-034 / BUG-092: registrar fecha_cierre al cerrar
                if form_data["estado"] == "Cerrada" and old_data["estado"] != "Cerrada":
                    incidencia.fecha_cierre = datetime.now(timezone.utc)
                registrar_cambios(incidencia.id, old_data, form_data, current_user.id)
                if commit_with_handling(
                    "Incidencia actualizada correctamente.",
                    "No se pudo actualizar la incidencia. Intenta nuevamente.",
                ):
                    return redirect(url_for("listar_incidencias"))

        estados_disponibles = [incidencia.estado] + TRANSICIONES_VALIDAS.get(incidencia.estado, [])

        return render_template(
            "form.html",
            title="Editar incidencia",
            form_action=url_for("editar_incidencia", incidencia_id=incidencia_id),
            incidencia=form_data,
            errors=errors,
            estados=estados_disponibles,
            prioridades=PRIORIDADES,
            tipos=TIPOS_INCIDENCIA,
            tecnicos=tecnicos,
            sucursales=sucursales,
            submit_label="Actualizar",
        )

    @app.post("/incidencias/<int:incidencia_id>/eliminar")
    @admin_required
    def eliminar_incidencia(incidencia_id: int) -> Any:
        incidencia = db.session.get(Incidencia, incidencia_id)
        if incidencia is None or incidencia.eliminado:
            flash("La incidencia solicitada no existe.", "error")
            return redirect(url_for("listar_incidencias"))

        if incidencia.estado == "Abierta" and incidencia.prioridad == "Critica":
            flash("No se puede eliminar una incidencia critica que sigue abierta.", "error")
            return redirect(url_for("listar_incidencias"))

        # BUG-093: no se pueden borrar incidencias ya cerradas
        if incidencia.estado == "Cerrada":
            flash("No se puede eliminar una incidencia en estado Cerrada.", "error")
            return redirect(url_for("listar_incidencias"))

        current_user = get_current_user()
        incidencia.eliminado = True
        incidencia.fecha_actualizacion = datetime.now(timezone.utc)
        db.session.add(HistorialCambio(
            incidencia_id=incidencia.id,
            usuario_id=current_user.id,
            campo="eliminado",
            valor_anterior="False",
            valor_nuevo="True",
        ))
        commit_with_handling(
            "Incidencia eliminada correctamente.",
            "No se pudo eliminar la incidencia. Intenta nuevamente.",
        )
        return redirect(url_for("listar_incidencias"))

    @app.get("/incidencias/<int:incidencia_id>")
    @login_required
    def ver_incidencia(incidencia_id: int) -> str | Any:
        incidencia = db.session.get(Incidencia, incidencia_id)
        if incidencia is None or incidencia.eliminado:
            flash("La incidencia solicitada no existe.", "error")
            return redirect(url_for("listar_incidencias"))
        return render_template("detail.html", incidencia=incidencia)

    @app.route("/usuarios")
    @admin_required
    def listar_usuarios() -> str:
        usuarios = Usuario.query.order_by(Usuario.nombre_completo).all()
        sucursales = get_sucursales()
        return render_template("usuarios.html", usuarios=usuarios, roles=ROLES, sucursales=sucursales)

    @app.post("/usuarios/<int:usuario_id>/resetear-contrasena")
    @admin_required
    def resetear_contrasena(usuario_id: int) -> Any:
        usuario = db.session.get(Usuario, usuario_id)
        if usuario is None:
            flash("El usuario solicitado no existe.", "error")
            return redirect(url_for("listar_usuarios"))
        nueva = request.form.get("nueva_contrasena", "")
        pass_errors = validate_login(usuario.username, nueva)
        if pass_errors.get("password"):
            flash(f"Contrasena invalida: {pass_errors['password']}", "error")
            return redirect(url_for("listar_usuarios"))
        # BUG-104: validar fortaleza también al resetear
        strength_err = validate_password_strength(nueva)
        if strength_err:
            flash(f"Contrasena debil: {strength_err}", "error")
            return redirect(url_for("listar_usuarios"))
        usuario.password_hash = generate_password_hash(nueva)
        commit_with_handling(
            f"Contrasena de '{usuario.username}' restablecida correctamente.",
            "No se pudo restablecer la contrasena. Intenta nuevamente.",
        )
        return redirect(url_for("listar_usuarios"))

    @app.post("/usuarios/<int:usuario_id>/cambiar-rol")
    @admin_required
    def cambiar_rol(usuario_id: int) -> Any:
        usuario = db.session.get(Usuario, usuario_id)
        if usuario is None:
            flash("El usuario solicitado no existe.", "error")
            return redirect(url_for("listar_usuarios"))
        nuevo_rol = request.form.get("rol", "")
        if nuevo_rol not in ROLES:
            flash("Rol no valido.", "error")
            return redirect(url_for("listar_usuarios"))
        if usuario.rol == "Administrador" and nuevo_rol != "Administrador":
            admin_count = sum(
                1 for u in Usuario.query.all()
                if u.rol == "Administrador" and u.id != usuario.id
            )
            if admin_count == 0:
                flash("No se puede cambiar el rol del unico administrador del sistema.", "error")
                return redirect(url_for("listar_usuarios"))
        rol_obj = Rol.query.filter_by(nombre=nuevo_rol).first()
        if rol_obj is None:
            flash("Rol no encontrado en la base de datos.", "error")
            return redirect(url_for("listar_usuarios"))
        usuario.rol_id = rol_obj.id
        commit_with_handling(
            f"Rol de '{usuario.username}' actualizado a '{nuevo_rol}'.",
            "No se pudo actualizar el rol. Intenta nuevamente.",
        )
        return redirect(url_for("listar_usuarios"))

    # BUG-032 / BUG-102: activar/desactivar usuarios
    @app.post("/usuarios/<int:usuario_id>/cambiar-activo")
    @admin_required
    def cambiar_activo(usuario_id: int) -> Any:
        usuario = db.session.get(Usuario, usuario_id)
        if usuario is None:
            flash("El usuario solicitado no existe.", "error")
            return redirect(url_for("listar_usuarios"))
        if usuario.activo and usuario.rol == "Administrador":
            admins_activos = sum(
                1 for u in Usuario.query.all()
                if u.rol == "Administrador" and u.activo and u.id != usuario.id
            )
            if admins_activos == 0:
                flash("No se puede desactivar el unico administrador activo.", "error")
                return redirect(url_for("listar_usuarios"))
        usuario.activo = not usuario.activo
        estado_txt = "activado" if usuario.activo else "desactivado"
        commit_with_handling(
            f"Usuario '{usuario.username}' {estado_txt} correctamente.",
            "No se pudo cambiar el estado del usuario.",
        )
        return redirect(url_for("listar_usuarios"))

    # BUG-121: gestión de sucursales
    @app.route("/sucursales")
    @admin_required
    def listar_sucursales() -> str:
        sucursales = Sucursal.query.order_by(Sucursal.nombre).all()
        return render_template("sucursales.html", sucursales=sucursales)

    @app.post("/sucursales/nueva")
    @admin_required
    def crear_sucursal() -> Any:
        nombre = request.form.get("nombre", "").strip()
        codigo = request.form.get("codigo", "").strip().upper()
        if not nombre or not codigo:
            flash("Nombre y codigo son obligatorios.", "error")
            return redirect(url_for("listar_sucursales"))
        if Sucursal.query.filter_by(codigo=codigo).first():
            flash("Ya existe una sucursal con ese codigo.", "error")
            return redirect(url_for("listar_sucursales"))
        db.session.add(Sucursal(nombre=nombre, codigo=codigo))
        commit_with_handling(
            "Sucursal creada correctamente.",
            "No se pudo crear la sucursal. Intenta nuevamente.",
        )
        return redirect(url_for("listar_sucursales"))

    # BUG-244: dashboard con estadisticas para administradores
    @app.get("/dashboard")
    @admin_required
    def dashboard() -> str:
        total = Incidencia.query.filter_by(eliminado=False).count()
        por_estado = {e: Incidencia.query.filter_by(eliminado=False, estado=e).count() for e in ESTADOS}
        por_prioridad = {p: Incidencia.query.filter_by(eliminado=False, prioridad=p).count() for p in PRIORIDADES}
        criticas_abiertas = Incidencia.query.filter_by(eliminado=False, estado="Abierta", prioridad="Critica").count()
        total_usuarios = Usuario.query.count()
        return render_template(
            "dashboard.html",
            total=total,
            por_estado=por_estado,
            por_prioridad=por_prioridad,
            criticas_abiertas=criticas_abiertas,
            total_usuarios=total_usuarios,
        )

    # BUG-261: exportacion de incidencias a CSV
    @app.get("/incidencias/exportar.csv")
    @admin_required
    def exportar_incidencias_csv() -> Response:
        incidencias = (
            Incidencia.query
            .filter_by(eliminado=False)
            .order_by(Incidencia.fecha_creacion.desc())
            .all()
        )
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "codigo", "titulo", "tipo", "estado", "prioridad",
            "responsable", "reportante", "sucursal",
            "fecha_creacion", "fecha_actualizacion", "fecha_cierre",
        ])
        for inc in incidencias:
            writer.writerow([
                inc.codigo,
                inc.titulo,
                inc.tipo,
                inc.estado,
                inc.prioridad,
                inc.responsable_user.nombre_completo if inc.responsable_user else "",
                inc.usuario_reportante.nombre_completo,
                inc.sucursal_obj.nombre if inc.sucursal_obj else "",
                inc.fecha_creacion.strftime("%Y-%m-%d %H:%M"),
                inc.fecha_actualizacion.strftime("%Y-%m-%d %H:%M"),
                inc.fecha_cierre.strftime("%Y-%m-%d %H:%M") if inc.fecha_cierre else "",
            ])
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=incidencias.csv"},
        )

    # BUG-238/240: ruta para generar backup de la base de datos
    @app.post("/admin/backup")
    @admin_required
    def hacer_backup() -> Any:
        result = backup_db()
        if result is None:
            flash("Backup no disponible. Solo SQLite con tamano inferior a 100 MB.", "error")
        else:
            flash(f"Backup creado: {Path(result).name}", "success")
        return redirect(url_for("listar_usuarios"))

    # BUG-285: pagina de mantenimiento
    @app.get("/mantenimiento")
    def mantenimiento() -> Any:
        return render_template("mantenimiento.html"), 503


if __name__ == "__main__":
    app = create_app()
    init_db(app)
    # BUG-038: modo debug controlado por variable de entorno
    debug_mode = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_mode, port=int(os.getenv("PORT", "8080")))
