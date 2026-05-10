#!/usr/bin/env python3
"""
Tellico2HTML — Enricher
Busca datos faltantes de libros en Google Books, OpenLibrary y Hardcover.
Actualiza las fichas HTML y opcionalmente el archivo .tc de Tellico.
"""

import os
import gzip
import shutil
import hashlib
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from typing import Optional
import httpx

log = logging.getLogger("t2h.enricher")

# ─── Config ───────────────────────────────────────────────────────────────────
GOOGLE_BOOKS_API_KEY  = os.getenv("GOOGLE_BOOKS_API_KEY", "")
HARDCOVER_API_KEY     = os.getenv("HARDCOVER_API_KEY", "")
OPENLIBRARY_ENABLED   = os.getenv("OPENLIBRARY_ENABLED", "true").lower() == "true"
TELLICOPATH           = os.getenv("TELLICOPATH", "/libros")
TELLICODB             = os.getenv("TELLICODB", "libros 1.tc")
STATIC_DIR            = Path("/app/static")
IMAGES_DIR            = STATIC_DIR / "images"
NS                    = "http://periapsis.org/tellico/"

# ─── Resultado de enriquecimiento ─────────────────────────────────────────────

class EnrichResult:
    def __init__(self, book_id: str, title: str):
        self.book_id   = book_id
        self.title     = title
        self.source    = ""          # qué API proveyó los datos
        self.fields    : dict = {}   # campos nuevos o mejorados
        self.cover_url : str  = ""   # URL de portada a descargar
        self.author_photo_url: str = ""  # URL foto de autor (OpenLibrary)
        self.skipped   = False
        self.reason    = ""
        self.error     = ""

    def has_data(self) -> bool:
        return bool(self.fields or self.cover_url or self.author_photo_url)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _missing(entry: dict) -> list[str]:
    """Devuelve lista de campos que faltan o están vacíos."""
    missing = []
    if not entry.get("plot"):       missing.append("plot")
    if not entry.get("cover"):      missing.append("cover")
    if not entry.get("pub_year"):   missing.append("pub_year")
    if not entry.get("genres"):     missing.append("genres")
    if not entry.get("isbn"):       missing.append("isbn")
    return missing

def _download_cover(url: str, book_id: str) -> Optional[str]:
    """Descarga una imagen de portada y la guarda en IMAGES_DIR. Retorna el nombre de archivo."""
    try:
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        r = httpx.get(url, timeout=10, follow_redirects=True)
        r.raise_for_status()
        ct = r.headers.get("content-type", "image/jpeg")
        ext = "jpeg" if "jpeg" in ct or "jpg" in ct else "png" if "png" in ct else "jpeg"
        # Nombre basado en hash del URL para evitar colisiones
        h = hashlib.md5(url.encode()).hexdigest()
        fname = f"{h}.{ext}"
        fpath = IMAGES_DIR / fname
        if not fpath.exists():
            fpath.write_bytes(r.content)
            log.info(f"Portada descargada: {fname}")
        return fname
    except Exception as e:
        log.warning(f"Error descargando portada {url}: {e}")
        return None

def _download_author_photo(url: str, author_key: str) -> Optional[str]:
    """Descarga foto de autor. Retorna nombre de archivo."""
    try:
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        r = httpx.get(url, timeout=10, follow_redirects=True)
        r.raise_for_status()
        fname = f"author_{hashlib.md5(author_key.encode()).hexdigest()}.jpeg"
        fpath = IMAGES_DIR / fname
        if not fpath.exists():
            fpath.write_bytes(r.content)
            log.info(f"Foto de autor descargada: {fname}")
        return fname
    except Exception as e:
        log.warning(f"Error descargando foto de autor: {e}")
        return None

# ─── Google Books ──────────────────────────────────────────────────────────────

def _query_google_books(entry: dict) -> Optional[dict]:
    """Busca en Google Books. Retorna dict con datos o None."""
    if not GOOGLE_BOOKS_API_KEY:
        log.debug("Google Books: sin API key, saltando")
        return None

    # Construir query
    if entry.get("isbn"):
        q = f"isbn:{entry['isbn']}"
    else:
        title   = entry.get("title", "")
        authors = entry.get("authors", [])
        q = f'intitle:"{title}"'
        if authors:
            q += f' inauthor:"{authors[0]}"'

    try:
        params = {"q": q, "key": GOOGLE_BOOKS_API_KEY, "maxResults": 1, "langRestrict": ""}
        r = httpx.get("https://www.googleapis.com/books/v1/volumes", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        if not data.get("items"):
            return None

        vol  = data["items"][0]["volumeInfo"]
        result = {}

        if vol.get("description"):
            result["plot"] = vol["description"]
        if vol.get("publishedDate"):
            result["pub_year"] = vol["publishedDate"][:4]
        if vol.get("categories"):
            result["genres"] = vol["categories"]
        if vol.get("industryIdentifiers"):
            for id_obj in vol["industryIdentifiers"]:
                if id_obj["type"] in ("ISBN_13", "ISBN_10"):
                    result["isbn"] = id_obj["identifier"]
                    break
        if vol.get("imageLinks", {}).get("thumbnail"):
            # Pedir imagen de mayor resolución
            cover_url = vol["imageLinks"]["thumbnail"].replace("zoom=1", "zoom=3").replace("http://", "https://")
            result["_cover_url"] = cover_url

        return result if result else None

    except Exception as e:
        log.warning(f"Google Books error: {e}")
        return None

# ─── OpenLibrary ──────────────────────────────────────────────────────────────

def _query_openlibrary(entry: dict) -> Optional[dict]:
    """Busca en OpenLibrary. Retorna dict con datos o None."""
    if not OPENLIBRARY_ENABLED:
        return None

    try:
        result = {}

        # Buscar el libro
        if entry.get("isbn"):
            url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{entry['isbn']}&format=json&jscmd=data"
            r = httpx.get(url, timeout=10)
            r.raise_for_status()
            data = r.json()
            book_data = data.get(f"ISBN:{entry['isbn']}")
        else:
            title   = entry.get("title", "")
            authors = entry.get("authors", [])
            params  = {"title": title, "limit": 1}
            if authors:
                params["author"] = authors[0]
            r = httpx.get("https://openlibrary.org/search.json", params=params, timeout=10)
            r.raise_for_status()
            hits = r.json().get("docs", [])
            if not hits:
                return None
            # Buscar datos completos del primer resultado
            work_key = hits[0].get("key", "")
            if not work_key:
                return None
            r2 = httpx.get(f"https://openlibrary.org{work_key}.json", timeout=10)
            r2.raise_for_status()
            book_data = r2.json()

        if not book_data:
            return None

        # Extraer descripción
        desc = book_data.get("description") or book_data.get("excerpts", [{}])[0].get("excerpt", "")
        if isinstance(desc, dict):
            desc = desc.get("value", "")
        if desc:
            result["plot"] = desc

        # Extraer año
        pub_date = book_data.get("publish_date") or ""
        if pub_date:
            import re
            m = re.search(r'\d{4}', str(pub_date))
            if m:
                result["pub_year"] = m.group()

        # Extraer géneros/subjects
        subjects = book_data.get("subjects", [])
        if subjects:
            genre_list = [s if isinstance(s, str) else s.get("name", "") for s in subjects[:4]]
            genre_list = [g for g in genre_list if g]
            if genre_list:
                result["genres"] = genre_list

        # Portada
        covers = book_data.get("cover", {})
        if isinstance(covers, dict) and covers.get("large"):
            result["_cover_url"] = covers["large"]
        elif isinstance(covers, list) and covers:
            result["_cover_url"] = f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg"

        # Foto del autor (buscar en la API de autores de OL)
        authors_ol = book_data.get("authors", [])
        if authors_ol:
            author_key = None
            if isinstance(authors_ol[0], dict):
                author_key = authors_ol[0].get("key") or (authors_ol[0].get("url", "").replace("https://openlibrary.org", ""))
            if author_key:
                try:
                    ra = httpx.get(f"https://openlibrary.org{author_key}.json", timeout=8)
                    ra.raise_for_status()
                    adata = ra.json()
                    photos = adata.get("photos", [])
                    if photos and photos[0] > 0:
                        result["_author_photo_url"] = f"https://covers.openlibrary.org/a/id/{photos[0]}-M.jpg"
                        result["_author_key"] = author_key
                except Exception:
                    pass

        return result if result else None

    except Exception as e:
        log.warning(f"OpenLibrary error: {e}")
        return None

# ─── Foto de autor (OpenLibrary, búsqueda directa por nombre) ────────────────

def _fetch_author_photo(author_name: str, cache: dict = None) -> tuple:
    """
    Busca un autor por nombre en OpenLibrary y retorna (url_foto, olid).
    Prueba los primeros 5 resultados hasta encontrar uno con foto.
    Retorna ("", "") si no encuentra foto.
    """
    if cache is not None and author_name in cache:
        return cache[author_name]
    if not author_name or not OPENLIBRARY_ENABLED:
        return "", ""
    try:
        r = httpx.get(
            "https://openlibrary.org/search/authors.json",
            params={"q": author_name, "limit": 5},
            timeout=8,
        )
        r.raise_for_status()
        docs = r.json().get("docs", [])
        if not docs:
            return "", ""

        # Probar cada resultado hasta encontrar uno con foto
        for author in docs:
            olid = author.get("key", "")
            if not olid:
                continue

            # Verificar que tiene foto (default=false retorna 404 si no hay)
            photo_url = f"https://covers.openlibrary.org/a/olid/{olid}-M.jpg?default=false"
            try:
                check = httpx.get(photo_url, timeout=6, follow_redirects=True)
                if check.status_code == 200 and len(check.content) > 1000:
                    log.debug(f"Foto encontrada para '{author_name}': {olid}")
                    result = (f"https://covers.openlibrary.org/a/olid/{olid}-M.jpg", olid)
                    if cache is not None: cache[author_name] = result
                    return result
            except Exception:
                continue

        if cache is not None: cache[author_name] = ("", "")
        return "", ""
    except Exception as e:
        log.debug(f"Error buscando foto de autor '{author_name}': {e}")
        if cache is not None: cache[author_name] = ("", "")
        return "", ""

# ─── Hardcover ────────────────────────────────────────────────────────────────

HARDCOVER_GQL_URL = "https://api.hardcover.app/v1/graphql"

def _query_hardcover(entry: dict) -> Optional[dict]:
    """Busca en Hardcover via GraphQL."""
    if not HARDCOVER_API_KEY:
        log.debug("Hardcover: sin API key, saltando")
        return None

    try:
        # Construir query GraphQL
        if entry.get("isbn"):
            gql = """
            query($isbn: String!) {
              books(where: {editions: {isbn_13: {_eq: $isbn}}}, limit: 1) {
                title
                description
                release_year
                contributions { author { name } }
                genres { genre { name } }
                editions(where: {isbn_13: {_eq: $isbn}}, limit: 1) {
                  isbn_13
                  image { url }
                }
              }
            }"""
            variables = {"isbn": entry["isbn"]}
        else:
            title = entry.get("title", "")
            gql = """
            query($title: String!) {
              books(where: {title: {_ilike: $title}}, limit: 1) {
                title
                description
                release_year
                contributions { author { name } }
                genres { genre { name } }
                editions(limit: 1) {
                  isbn_13
                  image { url }
                }
              }
            }"""
            variables = {"title": f"%{title}%"}

        headers = {
            "Authorization": f"Bearer {HARDCOVER_API_KEY}",
            "Content-Type": "application/json",
        }
        r = httpx.post(
            HARDCOVER_GQL_URL,
            json={"query": gql, "variables": variables},
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()

        books = data.get("data", {}).get("books", [])
        if not books:
            return None

        book    = books[0]
        result  = {}

        if book.get("description"):
            result["plot"] = book["description"]
        if book.get("release_year"):
            result["pub_year"] = str(book["release_year"])
        if book.get("genres"):
            result["genres"] = [g["genre"]["name"] for g in book["genres"] if g.get("genre")]

        editions = book.get("editions", [])
        if editions:
            ed = editions[0]
            if ed.get("isbn_13"):
                result["isbn"] = ed["isbn_13"]
            if ed.get("image", {}) and ed["image"].get("url"):
                result["_cover_url"] = ed["image"]["url"]

        return result if result else None

    except Exception as e:
        log.warning(f"Hardcover error: {e}")
        return None

# ─── Lógica principal de enriquecimiento ──────────────────────────────────────

def enrich_entry(entry: dict, _author_photo_cache: dict = None) -> EnrichResult:
    """
    Enriquece una entrada consultando APIs externas.
    Busca foto de autor para todos los libros.
    """
    if _author_photo_cache is None:
        _author_photo_cache = {}
    result = EnrichResult(entry["id"], entry["title"])
    missing = _missing(entry)

    if not missing:
        authors = entry.get("authors", [])
        if authors:
            photo_url, olid = _fetch_author_photo(authors[0], _author_photo_cache)
            if photo_url:
                result.author_photo_url = photo_url
                result.fields["_author_key"] = olid
                log.info(f"[{entry['id']}] Foto de autor (skipped): {authors[0]} ({olid})")
                result.skipped = False
                result.reason  = "Solo foto de autor"
            else:
                result.skipped = True
                result.reason  = "Todos los campos presentes"
        else:
            result.skipped = True
            result.reason  = "Todos los campos presentes"
        return result

    log.info(f"[{entry['id']}] '{entry['title']}' — faltan: {', '.join(missing)}")

    # Intentar APIs en orden
    api_data = None
    for api_name, api_fn in [
        ("Google Books", _query_google_books),
        ("OpenLibrary",  _query_openlibrary),
        ("Hardcover",    _query_hardcover),
    ]:
        data = api_fn(entry)
        if data:
            api_data = data
            result.source = api_name
            log.info(f"[{entry['id']}] Datos encontrados en {api_name}")
            break

    if not api_data:
        result.reason = "No se encontraron datos en ninguna API"
        return result

    # Procesar campos encontrados — solo los que faltan en Tellico
    # (o sobreescribir si la API trae algo y el campo era vacío/deficiente)
    fields_map = {
        "plot":     "plot",
        "pub_year": "pub_year",
        "genres":   "genres",
        "isbn":     "isbn",
    }

    for api_key, entry_key in fields_map.items():
        if api_key in api_data:
            current = entry.get(entry_key)
            new_val = api_data[api_key]
            # Sobreescribir si: campo vacío, o lista vacía, o string vacío
            should_update = (
                not current or
                (isinstance(current, list) and len(current) == 0) or
                (isinstance(current, str) and current.strip() == "")
            )
            if should_update:
                result.fields[entry_key] = new_val

    # Portada
    if "cover" in missing and api_data.get("_cover_url"):
        result.cover_url = api_data["_cover_url"]

    # Foto del autor: primero lo que vino de la API,
    # si no, buscar directamente por nombre en OpenLibrary
    if api_data.get("_author_photo_url"):
        result.author_photo_url = api_data["_author_photo_url"]
        result.fields["_author_key"] = api_data.get("_author_key", "")
    else:
        authors = entry.get("authors", [])
        if authors:
            photo_url, olid = _fetch_author_photo(authors[0])
            if photo_url:
                result.author_photo_url = photo_url
                result.fields["_author_key"] = olid
                log.info(f"[{entry['id']}] Foto de autor: {authors[0]} ({olid})")

    return result

# ─── Actualizar .tc ───────────────────────────────────────────────────────────

def _tag(name: str) -> str:
    return f"{{{NS}}}{name}"

def update_tellico_entry(entry_id: str, fields: dict, cover_filename: str = ""):
    """
    Actualiza los campos de una entrada en el archivo .tc de Tellico.
    Solo modifica los campos especificados.
    """
    tc_path = Path(TELLICOPATH) / TELLICODB
    if not tc_path.exists():
        log.error(f"No se encontró el .tc: {tc_path}")
        return False

    try:
        raw = tc_path.read_bytes()
        compressed = raw[:2] == b'\x1f\x8b'
        if compressed:
            xml_bytes = gzip.decompress(raw)
        else:
            xml_bytes = raw

        # Registrar namespace para no perderlo al serializar
        ET.register_namespace("", NS)
        root = ET.fromstring(xml_bytes)
        collection = root.find(_tag("collection"))
        if collection is None:
            return False

        # Encontrar la entrada
        target = None
        for entry in collection.findall(_tag("entry")):
            eid_el = entry.find(_tag("id"))
            if eid_el is not None and eid_el.text and eid_el.text.strip() == str(entry_id):
                target = entry
                break

        if target is None:
            log.warning(f"Entrada {entry_id} no encontrada en el .tc")
            return False

        # Actualizar campos simples
        simple_fields = ["plot", "pub_year", "isbn", "title"]
        for field_key in simple_fields:
            if field_key not in fields:
                continue
            val = fields[field_key]
            el = target.find(_tag(field_key))
            if el is None:
                el = ET.SubElement(target, _tag(field_key))
            el.text = str(val)

        # Autores (lista)
        if "authors" in fields:
            authors_val = fields["authors"]
            authors_el = target.find(_tag("authors"))
            if authors_el is not None:
                target.remove(authors_el)
            authors_el = ET.SubElement(target, _tag("authors"))
            for a in (authors_val if isinstance(authors_val, list) else [authors_val]):
                ae = ET.SubElement(authors_el, _tag("author"))
                ae.text = a

        # Géneros (lista)
        if "genres" in fields:
            genres_val = fields["genres"]
            genres_el = target.find(_tag("genres"))
            if genres_el is not None:
                target.remove(genres_el)
            genres_el = ET.SubElement(target, _tag("genres"))
            for g in (genres_val if isinstance(genres_val, list) else [genres_val]):
                ge = ET.SubElement(genres_el, _tag("genre"))
                ge.text = g

        # Portada
        if cover_filename:
            cover_el = target.find(_tag("cover"))
            if cover_el is None:
                cover_el = ET.SubElement(target, _tag("cover"))
            cover_el.text = cover_filename

        # Actualizar mdate
        mdate_el = target.find(_tag("mdate"))
        if mdate_el is None:
            mdate_el = ET.SubElement(target, _tag("mdate"))
            mdate_el.set("calendar", "gregorian")
        now = datetime.now()
        for sub, val in [("year", now.year), ("month", now.month), ("day", now.day)]:
            sub_el = mdate_el.find(_tag(sub))
            if sub_el is None:
                sub_el = ET.SubElement(mdate_el, _tag(sub))
            sub_el.text = f"{val:02d}"

        # Hacer backup del .tc original
        backup = tc_path.with_suffix(".tc.bak")
        shutil.copy2(tc_path, backup)

        # Serializar
        xml_str = ET.tostring(root, encoding="unicode", xml_declaration=False)
        xml_out = f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_str}'.encode("utf-8")

        if compressed:
            tc_path.write_bytes(gzip.compress(xml_out))
        else:
            tc_path.write_bytes(xml_out)

        log.info(f"Entrada {entry_id} actualizada en el .tc")
        return True

    except Exception as e:
        log.error(f"Error actualizando .tc: {e}")
        return False

# ─── Función principal llamada por la API ─────────────────────────────────────

def run_enrichment(entries: list[dict], progress_callback=None) -> list[dict]:
    """
    Procesa todas las entradas con campos faltantes.
    progress_callback(current, total, message) para reportar progreso.
    Retorna lista de resultados para el log.
    """
    log_entries = []
    total = len(entries)

    author_photo_cache: dict = {}
    for i, entry in enumerate(entries):
        if progress_callback:
            progress_callback(i, total, f"Procesando: {entry['title'][:50]}")

        result = enrich_entry(entry, author_photo_cache)

        if result.skipped:
            log.debug(f"[{entry['id']}] Saltado: {result.reason}")
            log_entries.append({
                "id": entry["id"], "title": entry["title"],
                "status": "skipped", "reason": result.reason, "source": "",
            })
            continue

        if result.error:
            log_entries.append({
                "id": entry["id"], "title": entry["title"],
                "status": "error", "reason": result.error, "source": "",
            })
            continue

        if not result.has_data():
            log_entries.append({
                "id": entry["id"], "title": entry["title"],
                "status": "not_found", "reason": result.reason, "source": "",
            })
            continue

        # Descargar portada si aplica
        cover_filename = ""
        if result.cover_url:
            cover_filename = _download_cover(result.cover_url, entry["id"]) or ""
            if cover_filename:
                result.fields["cover"] = cover_filename

        # Descargar foto de autor si aplica
        author_photo = ""
        if result.author_photo_url:
            author_key = result.fields.pop("_author_key", entry["id"])
            author_photo = _download_author_photo(result.author_photo_url, author_key) or ""
            if author_photo:
                result.fields["author_photo"] = author_photo

        # Actualizar .tc
        tc_fields = {k: v for k, v in result.fields.items()
                     if k not in ("cover", "author_photo", "_author_key")}
        if cover_filename:
            tc_fields["cover"] = cover_filename
        # author_photo no va al .tc (no es un campo estándar de Tellico)

        if tc_fields or cover_filename:
            update_tellico_entry(entry["id"], tc_fields, cover_filename)

        fields_updated = list(result.fields.keys())
        log_entries.append({
            "id":      entry["id"],
            "title":   entry["title"],
            "status":  "enriched",
            "source":  result.source,
            "fields":  fields_updated,
            "reason":  "",
            "author_photo": author_photo,
        })

    if progress_callback:
        progress_callback(total, total, "Completado")

    return log_entries
