# Tellico2HTML

Convierte una colección de libros de [Tellico](https://tellico-project.org/) en una biblioteca web estática autoalojada, con enriquecimiento automático de metadatos y edición de fichas desde el browser.

---

## Características

- 📚 **Biblioteca web** generada a partir de una base de datos `.tc` de Tellico
- ⚡ **Enriquecimiento automático** de metadatos vía Google Books, OpenLibrary y Hardcover
- 📷 **Fotos de autor** descargadas automáticamente desde OpenLibrary
- ✎ **Edición de fichas** desde el browser (título, autor, género, sinopsis, portada, foto de autor)
- 🎨 **Tres temas visuales**: Industrial, Futurista y Antiguo
- 🔔 **Notificaciones Gotify** al agregar libros y al completar enriquecimientos
- 🔍 **Filtro y ordenamiento** en tiempo real en el índice
- 📝 **Log de enriquecimiento** en HTML con resumen de resultados
- 🐳 **Docker** — un solo contenedor, sin dependencias externas

---

## Requisitos

- Docker y Docker Compose
- Nginx (para servir los estáticos, ver configuración más abajo)
- Una base de datos Tellico (`.tc`) con una colección de libros
- API keys opcionales: Google Books, Hardcover

---

## Instalación

### 1. Clonar el repositorio

```bash
git clone https://github.com/osdaeg/tellico2html.git
cd tellico2html
```

### 2. Configurar el entorno

```bash
cp .env.example .env
nano .env
```

Variables disponibles:

| Variable | Descripción | Default |
|---|---|---|
| `SERVER_IP` | IP del servidor | `192.168.1.10` |
| `NGINX_PORT` | Puerto de Nginx | `8079` |
| `TELLICO2HTML_EXTERNAL_PORT` | Puerto externo de la API | `7995` |
| `TELLICO2HTML_INTERNAL_PORT` | Puerto interno del contenedor | `8000` |
| `THEME` | Tema por defecto: `industrial`, `futurista`, `antiguo` | `industrial` |
| `TELLICOPATH` | Ruta al directorio con la base de datos | — |
| `TELLICODB` | Nombre del archivo `.tc` | — |
| `FORCE_REGEN` | `true` para regenerar todas las fichas al iniciar | `false` |
| `GOTIFY_URL` | URL de la instancia de Gotify | — |
| `GOTIFY_TOKEN` | Token de la app en Gotify | — |
| `GOOGLE_BOOKS_API_KEY` | API key de Google Books (opcional) | — |
| `HARDCOVER_API_KEY` | Bearer token de Hardcover (opcional) | — |
| `OPENLIBRARY_ENABLED` | Habilitar OpenLibrary | `true` |

### 3. Crear directorios de salida

```bash
mkdir -p /ruta/a/sitio/t2h/images
mkdir -p /ruta/a/sitio/t2h/books
```

### 4. Levantar el contenedor

```bash
docker-compose up -d --build
```

### 5. Verificar

```bash
curl http://localhost:7995/health
```

---

## Configuración de Nginx

```nginx
location /t2h {
    alias /var/www/html/t2h;
    index index.html;
    try_files $uri $uri/ =404;
}
```

El volumen compartido entre Nginx y el contenedor es la clave: Tellico2HTML escribe
los archivos estáticos en `/app/static`, que está mapeado al directorio que Nginx sirve.

---

## Uso

### Agregar un libro

1. Agregarlo en Tellico y guardar el `.tc`
2. `docker-compose restart` — el contenedor detecta la nueva entrada y genera su ficha
3. Llega una notificación Gotify con el título y link directo

### Enriquecer la colección

Desde el browser, hacer click en **⚡ Enriquecer** en el índice. Se muestra una barra de progreso en tiempo real. Al terminar se genera un log en `http://servidor/t2h/log.html`.

También se puede lanzar manualmente:

```bash
curl -X POST http://localhost:7995/enrich
```

El enriquecimiento:
- Detecta campos vacíos: sinopsis, portada, año, géneros, ISBN
- Busca en Google Books → OpenLibrary → Hardcover (en ese orden)
- Para **todos** los libros busca foto del autor en OpenLibrary
- Usa caché en memoria para no repetir búsquedas del mismo autor
- Actualiza el `.tc` directamente (hace backup como `.tc.bak`)
- Regenera solo las fichas afectadas

### Editar una ficha

Desde cualquier ficha individual, hacer click en **✎ Editar**. Se despliega un formulario inline con los datos actuales. Se puede editar título, autor(es), año, género(s), ISBN, sinopsis, y subir imágenes de portada y foto del autor desde el dispositivo.

Los cambios se guardan en el `.tc` y la ficha se regenera automáticamente.

### Regenerar todas las fichas

Útil después de cambios en las plantillas o el tema:

```bash
# En .env: FORCE_REGEN=true
docker-compose restart
# Volver a FORCE_REGEN=false
docker-compose restart
```

---

## Estructura de archivos

```
tellico2html/
├── processor.py        # Parseo del .tc y generación de HTML
├── enricher.py         # Enriquecimiento de metadatos y edición del .tc
├── api.py              # Servidor FastAPI
├── entrypoint.sh       # Script de arranque
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

Archivos generados en el volumen de salida:

```
/app/static/
├── index.html
├── log.html
├── images/             ← portadas y fotos de autor
└── books/
    ├── 1.html
    ├── 2.html
    └── ...
```

---

## APIs de enriquecimiento

| API | Autenticación | Datos que aporta |
|---|---|---|
| [Google Books](https://developers.google.com/books) | API key | Sinopsis, géneros, portada, año, ISBN |
| [OpenLibrary](https://openlibrary.org/developers) | Sin autenticación | Sinopsis, géneros, portada, foto de autor |
| [Hardcover](https://hardcover.app) | Bearer token (JWT, ~1 año) | Sinopsis, géneros |

El token de Hardcover se puede obtener desde DevTools al usar el sitio web de Hardcover (header `Authorization` en cualquier request a su API).

---

## Tecnologías

- **Python 3.12** — FastAPI, httpx, ElementTree
- **Docker** — imagen base `python:3.12-slim`
- **HTML/CSS/JS** vanilla — sin frameworks frontend

---

## Licencia

AGPL
