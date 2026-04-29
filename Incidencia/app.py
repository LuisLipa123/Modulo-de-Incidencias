from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

from flask import Flask, flash, g, redirect, render_template, request, send_from_directory, session, url_for
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DEFAULT_SQLITE_DATABASE_URL = f"sqlite:///{(BASE_DIR / 'incidencias.db').as_posix()}"

ESTADOS = ["Abierta", "En progreso", "Resuelta", "Cerrada"]
PRIORIDADES = ["Baja", "Media", "Alta", "Critica"]
MIN_TITULO_LENGTH = 5
MAX_TITULO_LENGTH = 150
MIN_DESCRIPCION_LENGTH = 10
MAX_DESCRIPCION_LENGTH = 2000
MIN_RESPONSABLE_LENGTH = 3
MAX_RESPONSABLE_LENGTH = 120
MAX_USERNAME_LENGTH = 50
RESPONSABLE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
MIN_USERNAME_LENGTH = 3
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
HTML_ANGLE_RE = re.compile(r"[<>]")
CONSECUTIVE_SPACES_RE = re.compile(r" {2,}")
TITLE_TRAILING_PUNCT_RE = re.compile(r"[.,;]$")

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


class Usuario(db.Model):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nombre_completo: Mapped[str] = mapped_column(String(120), nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class Incidencia(db.Model):
    __tablename__ = "incidencias"

    id: Mapped[int] = mapped_column(primary_key=True)
    titulo: Mapped[str] = mapped_column(String(150), nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    responsable: Mapped[str] = mapped_column(String(120), nullable=False)
    estado: Mapped[str] = mapped_column(String(30), nullable=False)
    prioridad: Mapped[str] = mapped_column(String(30), nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "desarrollo-incidencias"),
        SQLALCHEMY_DATABASE_URI=build_database_url(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        DEFAULT_ADMIN_USERNAME=os.getenv("DEFAULT_ADMIN_USERNAME", "admin"),
        DEFAULT_ADMIN_PASSWORD=os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123"),
        DEFAULT_ADMIN_NAME=os.getenv("DEFAULT_ADMIN_NAME", "Administrador General"),
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

    return wrapped_view


def ensure_default_user(app: Flask) -> None:
    admin_username = app.config["DEFAULT_ADMIN_USERNAME"]
    existing_user = Usuario.query.filter_by(username=admin_username).first()
    if existing_user is not None:
        return

    user = Usuario(
        username=admin_username,
        nombre_completo=app.config["DEFAULT_ADMIN_NAME"],
        password_hash=generate_password_hash(app.config["DEFAULT_ADMIN_PASSWORD"]),
    )
    db.session.add(user)
    db.session.commit()


def init_db(app: Flask) -> None:
    with app.app_context():
        db.create_all()
        ensure_default_user(app)


def validate_max_length(value: str, max_length: int, field_label: str) -> str | None:
    if len(value) > max_length:
        return f"{field_label} no puede superar {max_length} caracteres."
    return None


def validate_min_length(value: str, min_length: int, field_label: str) -> str | None:
    if value and len(value) < min_length:
        return f"{field_label} debe tener al menos {min_length} caracteres."
    return None


def validate_incidencia(form_data: dict[str, str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    titulo = form_data["titulo"].strip()
    descripcion = form_data["descripcion"].strip()
    responsable = form_data["responsable"].strip()

    if not titulo:
        errors["titulo"] = "El titulo es obligatorio."
    if not descripcion:
        errors["descripcion"] = "La descripcion es obligatoria."
    if not responsable:
        errors["responsable"] = "El responsable es obligatorio."

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

    responsable_min_length_error = validate_min_length(
        responsable, MIN_RESPONSABLE_LENGTH, "El responsable"
    )
    if responsable_min_length_error is not None:
        errors["responsable"] = responsable_min_length_error

    responsable_length_error = validate_max_length(
        responsable, MAX_RESPONSABLE_LENGTH, "El responsable"
    )
    if responsable_length_error is not None:
        errors["responsable"] = responsable_length_error

    if responsable and not RESPONSABLE_PATTERN.fullmatch(responsable):
        errors["responsable"] = (
            "El responsable solo puede incluir letras, numeros, espacios, punto, guion y guion bajo."
        )

    if form_data["estado"] not in ESTADOS:
        errors["estado"] = "Seleccione un estado valido."
    if form_data["prioridad"] not in PRIORIDADES:
        errors["prioridad"] = "Seleccione una prioridad valida."

    if form_data["estado"] == "Resuelta" and len(descripcion) < 15:
        errors["descripcion"] = "La descripcion debe explicar la resolucion con al menos 15 caracteres."

    if form_data["estado"] == "Cerrada" and len(descripcion) < 20:
        errors["descripcion"] = "La descripcion debe documentar el cierre con al menos 20 caracteres."

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

    if responsable and "responsable" not in errors and CONSECUTIVE_SPACES_RE.search(responsable):
        errors["responsable"] = "El responsable no puede contener espacios consecutivos."

    return errors


def extract_form_data(source: Any | None = None) -> dict[str, str]:
    if isinstance(source, Incidencia):
        return {
            "titulo": source.titulo,
            "descripcion": source.descripcion,
            "responsable": source.responsable,
            "estado": source.estado,
            "prioridad": source.prioridad,
        }

    source = source or {}
    getter = source.get if hasattr(source, "get") else lambda key, default="": getattr(source, key, default)

    return {
        "titulo": getter("titulo", "").strip(),
        "descripcion": getter("descripcion", "").strip(),
        "responsable": getter("responsable", "").strip(),
        "estado": getter("estado", ESTADOS[0]),
        "prioridad": getter("prioridad", PRIORIDADES[1]),
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

    if not password:
        errors["password"] = "La contrasena es obligatoria."
    else:
        pass_min_err = validate_min_length(password, MIN_PASSWORD_LENGTH, "La contrasena")
        if pass_min_err:
            errors["password"] = pass_min_err
        pass_max_err = validate_max_length(password, MAX_PASSWORD_LENGTH, "La contrasena")
        if pass_max_err:
            errors["password"] = pass_max_err

    return errors


def resolve_next_url() -> str:
    next_url = request.args.get("next") or request.form.get("next")
    if next_url and next_url.startswith("/"):
        return next_url
    return url_for("listar_incidencias")


def register_routes(app: Flask) -> None:
    @app.before_request
    def protect_post_requests() -> Any | None:
        if request.method == "POST" and not is_csrf_valid():
            flash("La sesion del formulario expiro. Intenta nuevamente.", "error")
            return redirect(request.referrer or request.path or url_for("login"))

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
                user = Usuario.query.filter_by(username=username).first()
                if user is None or not check_password_hash(user.password_hash, password):
                    errors["general"] = "Credenciales invalidas."
                else:
                    session.clear()
                    session["user_id"] = user.id
                    session["csrf_token"] = secrets.token_urlsafe(32)
                    flash(f"Bienvenido, {user.nombre_completo}.", "success")
                    return redirect(resolve_next_url())

        return render_template(
            "login.html",
            username=username,
            errors=errors,
            next_url=request.args.get("next", ""),
        )

    @app.post("/logout")
    def logout() -> Any:
        session.clear()
        flash("Sesion cerrada correctamente.", "success")
        return redirect(url_for("login"))

    @app.route("/incidencias")
    @login_required
    def listar_incidencias() -> str:
        incidencias = Incidencia.query.order_by(
            Incidencia.fecha_creacion.desc(), Incidencia.id.desc()
        ).all()
        return render_template("index.html", incidencias=incidencias)

    @app.route("/incidencias/nueva", methods=["GET", "POST"])
    @login_required
    def crear_incidencia() -> str | Any:
        form_data = extract_form_data(request.form)
        errors: dict[str, str] = {}

        if request.method == "POST":
            errors = validate_incidencia(form_data)
            if not errors:
                incidencia = Incidencia(**form_data)
                db.session.add(incidencia)
                if commit_with_handling(
                    "Incidencia creada correctamente.",
                    "No se pudo guardar la incidencia. Intenta nuevamente.",
                ):
                    return redirect(url_for("listar_incidencias"))

        return render_template(
            "form.html",
            title="Nueva incidencia",
            form_action=url_for("crear_incidencia"),
            incidencia=form_data,
            errors=errors,
            estados=ESTADOS,
            prioridades=PRIORIDADES,
            submit_label="Guardar",
        )

    @app.route("/incidencias/<int:incidencia_id>/editar", methods=["GET", "POST"])
    @login_required
    def editar_incidencia(incidencia_id: int) -> str | Any:
        incidencia = db.session.get(Incidencia, incidencia_id)
        if incidencia is None:
            flash("La incidencia solicitada no existe.", "error")
            return redirect(url_for("listar_incidencias"))

        form_data = extract_form_data(incidencia if request.method == "GET" else request.form)
        errors: dict[str, str] = {}

        if request.method == "POST":
            errors = validate_incidencia(form_data)
            if not errors:
                incidencia.titulo = form_data["titulo"]
                incidencia.descripcion = form_data["descripcion"]
                incidencia.responsable = form_data["responsable"]
                incidencia.estado = form_data["estado"]
                incidencia.prioridad = form_data["prioridad"]
                if commit_with_handling(
                    "Incidencia actualizada correctamente.",
                    "No se pudo actualizar la incidencia. Intenta nuevamente.",
                ):
                    return redirect(url_for("listar_incidencias"))

        return render_template(
            "form.html",
            title="Editar incidencia",
            form_action=url_for("editar_incidencia", incidencia_id=incidencia_id),
            incidencia=form_data,
            errors=errors,
            estados=ESTADOS,
            prioridades=PRIORIDADES,
            submit_label="Actualizar",
        )

    @app.post("/incidencias/<int:incidencia_id>/eliminar")
    @login_required
    def eliminar_incidencia(incidencia_id: int) -> Any:
        incidencia = db.session.get(Incidencia, incidencia_id)
        if incidencia is None:
            flash("La incidencia solicitada no existe.", "error")
            return redirect(url_for("listar_incidencias"))

        db.session.delete(incidencia)
        commit_with_handling(
            "Incidencia eliminada correctamente.",
            "No se pudo eliminar la incidencia. Intenta nuevamente.",
        )
        return redirect(url_for("listar_incidencias"))

    @app.get("/incidencias/<int:incidencia_id>")
    @login_required
    def ver_incidencia(incidencia_id: int) -> str | Any:
        incidencia = db.session.get(Incidencia, incidencia_id)
        if incidencia is None:
            flash("La incidencia solicitada no existe.", "error")
            return redirect(url_for("listar_incidencias"))
        return render_template("detail.html", incidencia=incidencia)


app = create_app()


if __name__ == "__main__":
    init_db(app)
    app.run(debug=True, port=8080)
