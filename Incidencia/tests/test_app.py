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


# --- Batch 1: 10 nuevas validaciones ---


def test_login_rejects_short_username(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf_token = get_csrf_token(client, "/login")

    response = client.post(
        "/login",
        data={"username": "ab", "password": "admin123", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El usuario debe tener al menos 3 caracteres." in response.data


def test_login_rejects_username_with_spaces(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf_token = get_csrf_token(client, "/login")

    response = client.post(
        "/login",
        data={"username": "ad min", "password": "admin123", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El usuario no puede contener espacios." in response.data


def test_login_rejects_short_password(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf_token = get_csrf_token(client, "/login")

    response = client.post(
        "/login",
        data={"username": "admin", "password": "passwor", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La contrasena debe tener al menos 8 caracteres." in response.data


def test_login_rejects_oversized_password(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf_token = get_csrf_token(client, "/login")

    response = client.post(
        "/login",
        data={"username": "admin", "password": "p" * 129, "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La contrasena no puede superar 128 caracteres." in response.data


def test_create_incidencia_rejects_numeric_only_titulo(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "12345",
            "descripcion": "El servidor principal no responde a las solicitudes.",
            "responsable": "Infraestructura",
            "estado": "Abierta",
            "prioridad": "Alta",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo debe contener al menos una letra." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_html_in_titulo(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "<script>alert(1)</script>",
            "descripcion": "Intento de inyeccion de codigo en el sistema de incidencias.",
            "responsable": "Seguridad",
            "estado": "Abierta",
            "prioridad": "Alta",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo no puede contener los caracteres" in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_html_in_descripcion(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Intento de XSS detectado",
            "descripcion": "<img src=x onerror=alert(1)> detectado en el sistema de incidencias.",
            "responsable": "Seguridad",
            "estado": "Abierta",
            "prioridad": "Alta",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La descripcion no puede contener los caracteres" in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_consecutive_spaces_in_responsable(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Servidor caido en produccion",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Juan  Perez",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El responsable no puede contener espacios consecutivos." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_trailing_punct_in_titulo(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Error de red.",
            "descripcion": "Se pierde la conectividad en la oficina central al intentar conectarse.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Baja",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo no debe terminar con punto" in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_descripcion_identical_to_titulo(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Error de conexion VPN",
            "descripcion": "Error de conexion VPN",
            "responsable": "Infraestructura",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La descripcion no puede ser identica al titulo." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


# --- Batch 2: 10 nuevas validaciones ---


def test_login_rejects_username_with_special_chars(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf_token = get_csrf_token(client, "/login")

    response = client.post(
        "/login",
        data={"username": "admin@site", "password": "admin123", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El usuario solo puede contener letras, numeros, punto, guion y guion bajo." in response.data


def test_login_rejects_password_equal_to_username(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf_token = get_csrf_token(client, "/login")

    response = client.post(
        "/login",
        data={"username": "testuser", "password": "testuser", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La contrasena no puede ser igual al nombre de usuario." in response.data


def test_create_incidencia_rejects_responsable_starting_with_digit(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Servidor caido en produccion",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "1Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El responsable debe comenzar con una letra." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_responsable_with_too_few_letters(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla de red en oficina central",
            "descripcion": "Se pierde la conexion a internet de forma intermitente.",
            "responsable": "A12",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El responsable debe contener al menos dos letras." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_control_chars_in_titulo(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Error\ten el servidor",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Alta",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo no puede contener tabulaciones ni saltos de linea." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_url_in_titulo(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Ver https://example.com para detalles",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo no puede contener URLs." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_control_chars_in_descripcion(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla en servidor de correo",
            "descripcion": "El servidor no responde.\tVerificar logs.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La descripcion no puede contener tabulaciones ni saltos de linea." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_descripcion_with_fewer_than_3_words(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla de red",
            "descripcion": "Error sistema",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La descripcion debe contener al menos 3 palabras." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_short_descripcion_for_critica(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Caida total del sistema",
            "descripcion": "Descripcion critica breve",
            "responsable": "Infraestructura",
            "estado": "Abierta",
            "prioridad": "Critica",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Las incidencias criticas requieren una descripcion de al menos 30 caracteres." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_short_descripcion_for_en_progreso(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Migracion de base de datos",
            "descripcion": "Trabajando en esto ahora",
            "responsable": "Infraestructura",
            "estado": "En progreso",
            "prioridad": "Alta",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Las incidencias en progreso requieren una descripcion de al menos 20 caracteres." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


# --- Batch 3: 10 nuevas validaciones ---


def test_create_incidencia_rejects_single_word_titulo(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Servidor",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo debe contener al menos dos palabras." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_titulo_with_too_many_words(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Error en el servidor de base de datos del cliente final extendido",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo no puede superar 10 palabras." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_all_caps_titulo(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "SERVIDOR CAIDO",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo no puede estar escrito enteramente en mayusculas." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_descripcion_with_repeated_word(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla de red",
            "descripcion": "error error error error error",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La descripcion no puede consistir en la misma palabra repetida." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_descripcion_with_long_word(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    long_word = "a" * 51
    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla de red",
            "descripcion": f"El sistema presenta {long_word} errores consecutivos.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La descripcion no puede contener palabras de mas de 50 caracteres." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_descripcion_starting_with_special_char(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla de red",
            "descripcion": "!!! El servidor no responde a las solicitudes.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La descripcion debe comenzar con una letra o numero." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_reserved_responsable(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla de red",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "admin",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El responsable no puede ser un nombre de sistema reservado." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_responsable_ending_with_dot(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla de red",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Soporte.",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El responsable no puede comenzar ni terminar con un punto." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_login_rejects_numeric_only_username(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf_token = get_csrf_token(client, "/login")

    response = client.post(
        "/login",
        data={"username": "123456", "password": "admin123", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El usuario no puede estar compuesto solo de numeros." in response.data


def test_edit_rejects_estado_change_from_cerrada(tmp_path):
    app = build_app(tmp_path)

    with app.app_context():
        db.session.add(
            Incidencia(
                titulo="Servidor resuelto correctamente",
                descripcion="El servidor fue reparado y verificado por el equipo.",
                responsable="Soporte",
                estado="Cerrada",
                prioridad="Media",
            )
        )
        db.session.commit()

    client = app.test_client()
    login(client)

    edit_token = get_csrf_token(client, "/incidencias/1/editar")
    response = client.post(
        "/incidencias/1/editar",
        data={
            "csrf_token": edit_token,
            "titulo": "Servidor resuelto correctamente",
            "descripcion": "El servidor fue reparado y verificado por el equipo.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Una incidencia cerrada no puede cambiar de estado." in response.data

    with app.app_context():
        inc = Incidencia.query.first()
        assert inc.estado == "Cerrada"


# --- Batch 4: 20 nuevas validaciones ---


def test_create_incidencia_rejects_titulo_starting_with_lowercase(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "servidor caido",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo debe comenzar con una letra mayuscula." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_titulo_with_repeated_char(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Errrror de servidor",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo no puede contener el mismo caracter repetido 4 o mas veces seguidas." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_titulo_with_long_digit_sequence(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Error 123456 en servidor",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo no puede contener 5 o mas digitos consecutivos." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_titulo_equal_to_estado(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "En progreso",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El titulo no puede coincidir con el nombre de un estado o prioridad." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_descripcion_starting_with_lowercase(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla de red",
            "descripcion": "el servidor no responde a las solicitudes de red.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La descripcion debe comenzar con una letra mayuscula." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_descripcion_with_high_word_repetition(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla de red",
            "descripcion": "Error sistema error sistema error",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La descripcion contiene demasiadas palabras repetidas." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_url_in_descripcion(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla de red",
            "descripcion": "Ver reporte en https://example.com para mas detalles del fallo.",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La descripcion no puede contener URLs." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_descripcion_with_repeated_trailing_punct(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla de red",
            "descripcion": "El servidor principal no responde a las solicitudes!!",
            "responsable": "Soporte",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La descripcion no puede terminar con signos de puntuacion repetidos." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_responsable_with_too_many_words(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla de red",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Juan Carlos De La Riva",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El responsable no puede tener mas de 4 palabras." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_responsable_with_too_many_digits(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Falla de red",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Juan123",
            "estado": "Abierta",
            "prioridad": "Media",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El responsable no puede contener mas de 2 digitos." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_incidencia_rejects_cerrada_critica_combo_short_desc(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Caida total del sistema",
            "descripcion": "El servidor fue reparado y cerrado.",
            "responsable": "Infraestructura",
            "estado": "Cerrada",
            "prioridad": "Critica",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Las incidencias criticas cerradas requieren una descripcion de al menos 40 caracteres." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_login_rejects_username_starting_with_special_char(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf_token = get_csrf_token(client, "/login")

    response = client.post(
        "/login",
        data={"username": ".admin", "password": "admin123", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El usuario no puede comenzar ni terminar con punto, guion ni guion bajo." in response.data


def test_login_rejects_username_with_consecutive_special_chars(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf_token = get_csrf_token(client, "/login")

    response = client.post(
        "/login",
        data={"username": "admin..user", "password": "admin123", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"El usuario no puede tener punto, guion ni guion bajo consecutivos." in response.data


def test_login_rejects_common_password(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf_token = get_csrf_token(client, "/login")

    response = client.post(
        "/login",
        data={"username": "admin", "password": "password1", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La contrasena es demasiado comun. Elige una mas segura." in response.data


def test_login_rejects_alphabetic_only_password(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf_token = get_csrf_token(client, "/login")

    response = client.post(
        "/login",
        data={"username": "admin", "password": "passwordonly", "csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"La contrasena debe contener al menos un caracter no alfabetico." in response.data


def test_eliminar_blocks_critica_abierta(tmp_path):
    app = build_app(tmp_path)

    with app.app_context():
        db.session.add(
            Incidencia(
                titulo="Falla critica de red",
                descripcion="El sistema de red ha fallado completamente.",
                responsable="Infraestructura",
                estado="Abierta",
                prioridad="Critica",
            )
        )
        db.session.commit()

    client = app.test_client()
    login(client)

    listado = client.get("/incidencias")
    csrf_token = extract_csrf_token(listado)
    response = client.post(
        "/incidencias/1/eliminar",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"No se puede eliminar una incidencia critica que sigue abierta." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 1


def test_create_rejects_critica_cerrada_combo(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)
    csrf_token = get_csrf_token(client, "/incidencias/nueva")

    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": csrf_token,
            "titulo": "Sistema critico cerrado",
            "descripcion": "Sistema critico cerrado por el equipo de soporte tecnico especializado.",
            "responsable": "Infraestructura",
            "estado": "Cerrada",
            "prioridad": "Critica",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"No se puede crear una incidencia critica directamente como Cerrada." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 0


def test_create_rejects_duplicate_titulo_abierta(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)

    for _ in range(2):
        csrf_token = get_csrf_token(client, "/incidencias/nueva")
        client.post(
            "/incidencias/nueva",
            data={
                "csrf_token": csrf_token,
                "titulo": "Servidor caido",
                "descripcion": "El servidor principal no responde a las solicitudes de red.",
                "responsable": "Infraestructura",
                "estado": "Abierta",
                "prioridad": "Alta",
            },
            follow_redirects=True,
        )

    second_token = get_csrf_token(client, "/incidencias/nueva")
    response = client.post(
        "/incidencias/nueva",
        data={
            "csrf_token": second_token,
            "titulo": "Servidor caido",
            "descripcion": "El servidor principal no responde a las solicitudes de red.",
            "responsable": "Infraestructura",
            "estado": "Abierta",
            "prioridad": "Alta",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Ya existe una incidencia abierta con el mismo titulo." in response.data

    with app.app_context():
        assert Incidencia.query.count() == 1


def test_edit_nonexistent_incidencia_redirects_with_error(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)

    response = client.get("/incidencias/999/editar", follow_redirects=True)

    assert response.status_code == 200
    assert b"La incidencia solicitada no existe." in response.data


def test_ver_nonexistent_incidencia_redirects_with_error(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    login(client)

    response = client.get("/incidencias/999", follow_redirects=True)

    assert response.status_code == 200
    assert b"La incidencia solicitada no existe." in response.data
