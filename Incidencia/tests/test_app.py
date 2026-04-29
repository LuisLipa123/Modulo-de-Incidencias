import re

from sqlalchemy.exc import SQLAlchemyError

from app import Incidencia, build_database_url, create_app, db, init_db


def build_app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "DEFAULT_ADMIN_USERNAME": "admin",
            "DEFAULT_ADMIN_PASSWORD": "admin123",
            "DEFAULT_ADMIN_NAME": "Administrador Test",
        }
    )
    init_db(app)
    return app


def extract_csrf_token(response):
    html = response.data.decode("utf-8")
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def get_csrf_token(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return extract_csrf_token(response)


def login(client):
    csrf_token = get_csrf_token(client, "/login")
    return client.post(
        "/login",
        data={"username": "admin", "password": "admin123", "csrf_token": csrf_token},
        follow_redirects=True,
    )


def test_home_redirects_to_login(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_required_redirects_to_login(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()

    response = client.get("/incidencias")

    assert response.status_code == 302
    assert "/login?next=/incidencias" in response.headers["Location"]


def test_login_success_shows_listado(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()

    response = login(client)

    assert response.status_code == 200
    assert b"Bienvenido" in response.data
    assert b"Incidencias registradas" in response.data


def test_edit_page_loads_existing_incidencia(tmp_path):
    app = build_app(tmp_path)

    with app.app_context():
        db.session.add(
            Incidencia(
                titulo="Correo caido",
                descripcion="No entran mensajes nuevos.",
                responsable="Soporte",
                estado="Abierta",
                prioridad="Alta",
            )
        )
        db.session.commit()

    client = app.test_client()
    login(client)
    response = client.get("/incidencias/1/editar")

    assert response.status_code == 200
    assert b"Editar incidencia" in response.data


def test_create_incidencia_rejects_empty_and_invalid_fields(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "   ",
            "descripcion": "",
            "responsable": "   ",
            "estado": "Invalido",
            "prioridad": "Urgente",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo es obligatorio." in response.data
    assert b"La descripcion es obligatoria." in response.data
    assert b"El responsable es obligatorio." in response.data
    assert b"Seleccione un estado valido." in response.data
    assert b"Seleccione una prioridad valida." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_oversized_fields(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "T" * 151,
            "descripcion": "D" * 2001,
            "responsable": "R" * 121,
            "estado": "Abierta",
            "prioridad": "Alta",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo no puede superar 150 caracteres." in response.data
    assert b"La descripcion no puede superar 2000 caracteres." in response.data
    assert b"El responsable no puede superar 120 caracteres." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_login_rejects_oversized_username(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf_token = get_csrf_token(client, "/login")

    response = client.post(
        "/login",
        data={"username": "u" * 51, "password": "admin123", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El usuario no puede superar 50 caracteres." in response.data


def test_login_rejects_missing_csrf_token(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()

    response = client.post(
        "/login",
        data={"username": "admin", "password": "admin123"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La sesion del formulario expiro. Intenta nuevamente." in response.data


def test_crud_flow_creates_updates_and_deletes_incidencia(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)

    create_token = get_csrf_token(client, "/incidencias/nueva")
    create_response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": create_token,
            "titulo": "Servidor caido",
            "descripcion": "El servidor principal no responde.",
            "responsable": "Infraestructura",
            "estado": "Abierta",
            "prioridad": "Alta",
        },
        follow_redirects=True,
    )

    assert create_response.status_code == 200
    assert b"Incidencia creada correctamente." in create_response.data
    assert b"Servidor caido" in create_response.data

    detail_response = client.get("/incidencias/1")
    assert detail_response.status_code == 200
    assert b"Infraestructura" in detail_response.data

    edit_token = get_csrf_token(client, "/incidencias/1/editar")
    edit_response = client.post(
        "/incidencias/1/editar",
        data={
            "csrf_token": edit_token,
            "titulo": "Servidor recuperado",
            "descripcion": "Se restablecio el servicio.",
            "responsable": "Infraestructura",
            "estado": "Resuelta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert edit_response.status_code == 200
    assert b"Incidencia actualizada correctamente." in edit_response.data
    assert b"Servidor recuperado" in edit_response.data

    delete_page = client.get("/incidencias")
    delete_token = extract_csrf_token(delete_page)
    delete_response = client.post(
        "/incidencias/1/eliminar",
        data={"csrf_token": delete_token},
        follow_redirects=True,
    )

    assert delete_response.status_code == 200
    assert b"Incidencia eliminada correctamente." in delete_response.data
    assert b"No hay incidencias registradas" in delete_response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_handles_database_error(tmp_path, monkeypatch):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)

    create_token = get_csrf_token(client, "/incidencias/nueva")

    def fail_commit():
        raise SQLAlchemyError("db down")

    monkeypatch.setattr(db.session, "commit", fail_commit)

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": create_token,
            "titulo": "Servidor caido",
            "descripcion": "El servidor principal no responde.",
            "responsable": "Infraestructura",
            "estado": "Abierta",
            "prioridad": "Alta",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"No se pudo guardar la incidencia. Intenta nuevamente." in response.data


def test_create_incidencia_rejects_invalid_responsable_format(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Error de red",
            "descripcion": "Se pierde la conectividad en la oficina central.",
            "responsable": "Soporte@Nivel1",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        b"El responsable solo puede incluir letras, numeros, espacios, punto, guion y guion bajo."
        in response.data
    )


def test_create_incidencia_applies_state_description_rules(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)

    resuelta_token = get_csrf_token(client, "/incidencias/nueva")
    resuelta_response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": resuelta_token,
            "titulo": "Correo intermitente",
            "descripcion": "Solucion corta",
            "responsable": "Soporte",
            "estado": "Resuelta",
            "prioridad": "Alta",
        },
        follow_redirects=True,
    )

    assert resuelta_response.status_code == 200
    assert b"La descripcion debe explicar la resolucion con al menos 15 caracteres." in resuelta_response.data

    cerrada_token = get_csrf_token(client, "/incidencias/nueva")
    cerrada_response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": cerrada_token,
            "titulo": "VPN establecida",
            "descripcion": "Cierre breve final",
            "responsable": "Infraestructura",
            "estado": "Cerrada",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert cerrada_response.status_code == 200
    assert b"La descripcion debe documentar el cierre con al menos 20 caracteres." in cerrada_response.data


def test_form_pages_include_browser_validation_attributes(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()

    login_page = client.get("/login")
    assert b'name="csrf_token"' in login_page.data
    assert b'name="username" value="" placeholder="admin" required maxlength="50"' in login_page.data
    assert b'name="password" placeholder="Ingresa tu contrasena" required' in login_page.data

    login(client)
    form_page = client.get("/incidencias/nueva")
    assert b'name="csrf_token"' in form_page.data
    assert b'name="titulo"' in form_page.data and b'minlength="5" maxlength="150"' in form_page.data
    assert b'name="descripcion"' in form_page.data and b'minlength="10" maxlength="2000"' in form_page.data
    assert b'name="responsable"' in form_page.data and b'pattern="[A-Za-z0-9][A-Za-z0-9 ._-]*"' in form_page.data


def test_light_load_creates_multiple_incidencias(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)

    for index in range(8):
        csrf_token = get_csrf_token(client, "/incidencias/nueva")
        response = client.post(
            "/incidencias/nueva",
            data={
                "csrf_token": csrf_token,
                "titulo": f"Incidencia {index}",
                "descripcion": f"Descripcion valida de la incidencia numero {index}.",
                "responsable": "Mesa Central",
                "estado": "Abierta",
                "prioridad": "Media",
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert b"Incidencia creada correctamente." in response.data

    listado = client.get("/incidencias")
    assert listado.status_code == 200
    assert listado.data.count(b"button--danger") == 8
    assert listado.data.find(b"Incidencia 7") < listado.data.find(b"Incidencia 0")

    with app.app_context():
        assert Incidencia.query.count() == 8


def test_build_database_url_uses_database_url_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@localhost:5432/incidencias")
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

    assert build_database_url() == "postgresql+psycopg://user:secret@localhost:5432/incidencias"


def test_build_database_url_uses_postgres_parts(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "postgres")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secreto")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "incidencias")

    assert build_database_url() == "postgresql+psycopg://postgres:secreto@localhost:5432/incidencias"
