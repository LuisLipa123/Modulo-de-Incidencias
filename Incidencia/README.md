# Modulo de Incidencias

Mini proyecto en Python para gestionar incidencias en localhost.

## Funcionalidades

- Crear incidencias
- Listar incidencias
- Ver detalle de una incidencia
- Editar incidencias
- Eliminar incidencias
- Validacion basica de formulario
- Login basico por sesion
- Persistencia con PostgreSQL

## Tecnologias

- Python 3.14
- Flask
- Flask-SQLAlchemy
- PostgreSQL
- HTML y CSS
- Git para control de versiones

## Requisitos minimos

- Computadora con Windows, Linux o macOS
- Python 3 instalado
- PostgreSQL instalado y ejecutandose
- Navegador web
- Git instalado para versionado

## Ejecucion en localhost

1. Activar el entorno virtual si hace falta.
2. Instalar dependencias:

```bash
pip install -r requirements.txt
```

3. Crear manualmente una base de datos PostgreSQL llamada `incidencias`.

```sql
CREATE DATABASE incidencias;
```

Si vas a pasar este proyecto a otra laptop o a otro integrante del grupo, este paso tambien se tiene que hacer en esa maquina antes de ejecutar la aplicacion.

4. Configurar variables de entorno en PowerShell.
	Puedes tomar como base el archivo `.env.example`.

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres:TU_PASSWORD@localhost:5432/incidencias"
$env:DEFAULT_ADMIN_USERNAME = "admin"
$env:DEFAULT_ADMIN_PASSWORD = "admin123"
$env:DEFAULT_ADMIN_NAME = "Administrador General"
```

Tambien puedes usar variables separadas en lugar de `DATABASE_URL`:

```powershell
$env:POSTGRES_USER = "postgres"
$env:POSTGRES_PASSWORD = "TU_PASSWORD"
$env:POSTGRES_HOST = "localhost"
$env:POSTGRES_PORT = "5432"
$env:POSTGRES_DB = "incidencias"
```

5. Ejecutar la aplicacion:

```bash
python app.py
```

6. Abrir en el navegador:

```text
http://127.0.0.1:8080
```

## Acceso inicial

- Usuario: `admin`
- Contrasena: `admin123`

## Importante antes de ejecutar

Este proyecto no crea la base de datos PostgreSQL por si solo. Antes de levantar la aplicacion, primero debes crear `incidencias` en tu equipo y luego configurar `DATABASE_URL` o las variables `POSTGRES_*` con tu usuario y tu contrasena reales de PostgreSQL.

## Pruebas

```bash
pytest -q
```

## Estructura basica

- `app.py`: aplicacion principal Flask
- `templates/`: vistas HTML
- `static/`: estilos CSS
- `tests/`: pruebas automatizadas basicas
