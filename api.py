#!/usr/bin/env python3
"""
Tellico2HTML — API Server (FastAPI)
Endpoints: /health, /enrich (POST), /enrich/status (GET)
"""

import os
import sys
import uuid
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, UploadFile, File, Form
from typing import Optional as Opt
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import requests as req_lib

# Importar módulos del proyecto
sys.path.insert(0, "/app")
import processor as proc
import enricher as enr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("t2h.api")

# ─── Config ───────────────────────────────────────────────────────────────────
GOTIFY_URL   = os.getenv("GOTIFY_URL", "")
GOTIFY_TOKEN = os.getenv("GOTIFY_TOKEN", "")
SERVER_IP    = os.getenv("SERVER_IP", "192.168.1.10")
NGINX_PORT   = os.getenv("NGINX_PORT", "8079")
THEME        = os.getenv("THEME", "industrial")
BASE_URL     = f"http://{SERVER_IP}:{NGINX_PORT}/t2h"
STATIC_DIR   = Path("/app/static")

# ─── Estado global del job ────────────────────────────────────────────────────
# Simple in-memory state (solo un job a la vez)
_job: dict = {
    "id":       None,
    "running":  False,
    "current":  0,
    "total":    0,
    "message":  "",
    "started":  None,
    "finished": None,
    "results":  [],
}

# ─── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="Tellico2HTML API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "tellico2html"}


@app.post("/enrich")
def start_enrich(background_tasks: BackgroundTasks):
    """Inicia el proceso de enriquecimiento en background."""
    if _job["running"]:
        return JSONResponse(
            status_code=409,
            content={"error": "Ya hay un proceso de enriquecimiento en curso", "job_id": _job["id"]}
        )

    job_id = str(uuid.uuid4())[:8]
    _job.update({
        "id":       job_id,
        "running":  True,
        "current":  0,
        "total":    0,
        "message":  "Iniciando…",
        "started":  datetime.now().isoformat(),
        "finished": None,
        "results":  [],
    })

    background_tasks.add_task(_run_enrichment_job, job_id)
    return {"job_id": job_id, "status": "started"}


@app.get("/enrich/status")
def enrich_status():
    """Retorna el estado actual del job de enriquecimiento."""
    return {
        "job_id":   _job["id"],
        "running":  _job["running"],
        "current":  _job["current"],
        "total":    _job["total"],
        "message":  _job["message"],
        "started":  _job["started"],
        "finished": _job["finished"],
        "summary":  _build_summary(_job["results"]) if _job["results"] else None,
    }

# ─── Endpoint edición de ficha ────────────────────────────────────────────────

@app.post("/book/{book_id}")
async def edit_book(
    book_id:      str,
    title:        Opt[str] = Form(None),
    authors:      Opt[str] = Form(None),
    pub_year:     Opt[str] = Form(None),
    genres:       Opt[str] = Form(None),
    isbn:         Opt[str] = Form(None),
    plot:         Opt[str] = Form(None),
    cover:        Opt[UploadFile] = File(None),
    author_photo: Opt[UploadFile] = File(None),
):
    """Actualiza los datos de un libro y regenera su ficha HTML."""
    import shutil

    fields = {}
    if title:    fields["title"]    = title.strip()
    if authors:  fields["authors"]  = [a.strip() for a in authors.split(",") if a.strip()]
    if pub_year: fields["pub_year"] = pub_year.strip()
    if genres:   fields["genres"]   = [g.strip() for g in genres.split(",") if g.strip()]
    if isbn:     fields["isbn"]     = isbn.strip()
    if plot:     fields["plot"]     = plot.strip()

    import hashlib, time

    # Procesar carátula si se subió
    cover_filename = ""
    if cover and cover.filename:
        ext = Path(cover.filename).suffix.lower() or ".jpg"
        fname = hashlib.md5(f"{book_id}{time.time()}".encode()).hexdigest() + ext
        fpath = STATIC_DIR / "images" / fname
        fpath.parent.mkdir(parents=True, exist_ok=True)
        with fpath.open("wb") as f:
            shutil.copyfileobj(cover.file, f)
        cover_filename = fname
        fields["cover"] = fname
        log.info(f"Carátula subida para {book_id}: {fname}")

    # Procesar foto de autor si se subió
    author_photo_filename = ""
    if author_photo and author_photo.filename:
        ext = Path(author_photo.filename).suffix.lower() or ".jpg"
        fname = "author_" + hashlib.md5(f"{book_id}_author{time.time()}".encode()).hexdigest() + ext
        fpath = STATIC_DIR / "images" / fname
        fpath.parent.mkdir(parents=True, exist_ok=True)
        with fpath.open("wb") as f:
            shutil.copyfileobj(author_photo.file, f)
        author_photo_filename = fname
        log.info(f"Foto de autor subida para {book_id}: {fname}")

    if not fields and not cover_filename and not author_photo_filename:
        raise HTTPException(status_code=400, detail="No se enviaron datos para actualizar")

    # Actualizar .tc
    tc_fields = {k: v for k, v in fields.items() if k != "cover"}
    enr.update_tellico_entry(book_id, tc_fields, cover_filename)

    # Releer entry del .tc actualizado y regenerar HTML
    try:
        tc_path = Path(proc.TELLICOPATH) / proc.TELLICODB
        root    = proc.parse_tellico(tc_path)
        entries = proc.parse_entries(root)
        entry   = next((e for e in entries if e["id"] == book_id), None)
        if entry:
            if author_photo_filename:
                entry["author_photo"] = author_photo_filename
            page = proc.build_book_page(entry, THEME)
            (STATIC_DIR / "books" / f"{book_id}.html").write_text(page, encoding="utf-8")
            log.info(f"Ficha {book_id}.html regenerada tras edición")
    except Exception as e:
        log.error(f"Error regenerando ficha {book_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "ok", "book_id": book_id, "fields_updated": list(fields.keys()), "author_photo": author_photo_filename}

# ─── Background job ───────────────────────────────────────────────────────────

def _progress(current: int, total: int, message: str):
    _job["current"] = current
    _job["total"]   = total
    _job["message"] = message

def _build_summary(results: list) -> dict:
    enriched  = [r for r in results if r["status"] == "enriched"]
    not_found = [r for r in results if r["status"] == "not_found"]
    skipped   = [r for r in results if r["status"] == "skipped"]
    errors    = [r for r in results if r["status"] == "error"]
    return {
        "total":     len(results),
        "enriched":  len(enriched),
        "not_found": len(not_found),
        "skipped":   len(skipped),
        "errors":    len(errors),
    }

def _run_enrichment_job(job_id: str):
    try:
        log.info(f"[job:{job_id}] Iniciando enriquecimiento")

        # Parsear la base de datos
        tc_path = Path(proc.TELLICOPATH) / proc.TELLICODB
        root    = proc.parse_tellico(tc_path)
        entries = proc.parse_entries(root)

        _job["total"]   = len(entries)
        _job["message"] = f"Analizando {len(entries)} libros…"

        # Ejecutar enriquecimiento
        results = enr.run_enrichment(entries, progress_callback=_progress)
        _job["results"] = results

        # Regenerar fichas HTML con datos actualizados
        _job["message"] = "Regenerando fichas HTML…"
        _regenerate_enriched_books(results, entries)

        # Regenerar index
        root2   = proc.parse_tellico(tc_path)
        entries2 = proc.parse_entries(root2)
        index_html = proc.build_index(entries2, THEME)
        (STATIC_DIR / "index.html").write_text(index_html, encoding="utf-8")

        # Generar log HTML
        _job["message"] = "Generando log…"
        _generate_log_html(results, job_id)

        # Notificar Gotify
        summary = _build_summary(results)
        _notify_gotify_enrich(job_id, summary)

        _job["finished"] = datetime.now().isoformat()
        _job["running"]  = False
        _job["message"]  = "Completado"
        log.info(f"[job:{job_id}] Enriquecimiento completado: {summary}")

    except Exception as e:
        log.error(f"[job:{job_id}] Error: {e}", exc_info=True)
        _job["running"]  = False
        _job["finished"] = datetime.now().isoformat()
        _job["message"]  = f"Error: {e}"

def _regenerate_enriched_books(results: list, original_entries: list):
    """Regenera las fichas HTML de los libros que fueron enriquecidos."""
    # Construir mapa id → entry original
    entry_map = {e["id"]: e for e in original_entries}

    # Releer el .tc actualizado para tener los datos frescos
    try:
        tc_path = Path(proc.TELLICOPATH) / proc.TELLICODB
        root    = proc.parse_tellico(tc_path)
        updated = proc.parse_entries(root)
        entry_map = {e["id"]: e for e in updated}
    except Exception as e:
        log.warning(f"No se pudo releer el .tc: {e}, usando entradas originales")

    books_dir = STATIC_DIR / "books"
    books_dir.mkdir(parents=True, exist_ok=True)

    for r in results:
        if r["status"] != "enriched":
            continue
        entry = entry_map.get(r["id"])
        if not entry:
            continue

        # Agregar author_photo al entry si se descargó
        if r.get("author_photo"):
            entry["author_photo"] = r["author_photo"]

        page = proc.build_book_page(entry, THEME)
        (books_dir / f"{r['id']}.html").write_text(page, encoding="utf-8")
        log.info(f"Ficha regenerada tras enriquecimiento: {r['id']}.html")

def _generate_log_html(results: list, job_id: str):
    """Genera el archivo log.html con el resultado del enriquecimiento."""
    summary = _build_summary(results)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Construir filas
    rows = ""
    status_labels = {
        "enriched":  ("✓", "log-ok"),
        "not_found": ("?", "log-miss"),
        "skipped":   ("–", "log-skip"),
        "error":     ("✗", "log-err"),
    }
    for r in results:
        if r["status"] == "skipped":
            continue  # No mostrar los que ya estaban completos
        icon, css_class = status_labels.get(r["status"], ("?", ""))
        fields_str = ", ".join(r.get("fields", [])) or r.get("reason", "")
        source_str = r.get("source", "")
        rows += f"""
    <tr class="{css_class}">
      <td class="log-id">{r['id']}</td>
      <td><a href="books/{r['id']}.html">{r['title']}</a></td>
      <td class="log-icon">{icon}</td>
      <td>{source_str}</td>
      <td class="log-fields">{fields_str}</td>
    </tr>"""

    theme = THEME
    html  = proc.header_html(theme, "Log de enriquecimiento", back_link="index.html")
    html += f"""
<style>
  .log-summary {{
    display: flex; gap: 1.5rem; flex-wrap: wrap;
    margin-bottom: 1.2rem; font-size: 0.85rem;
  }}
  .log-stat {{ display: flex; flex-direction: column; align-items: center; gap: 0.2rem; }}
  .log-stat .num {{
    font-size: 1.8rem; font-weight: bold; color: var(--accent);
    font-family: var(--font-title);
  }}
  .log-stat .lbl {{ opacity: 0.6; font-size: 0.75rem; text-transform: uppercase; }}
  .log-date {{ font-size: 0.78rem; opacity: 0.5; margin-bottom: 1rem; }}
  .log-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  .log-table th {{
    background: var(--table-head-bg); color: var(--accent);
    padding: 0.5rem 0.7rem; text-align: left; font-size: 0.75rem;
    text-transform: uppercase; letter-spacing: 0.06em;
    border-bottom: 2px solid var(--accent);
  }}
  .log-table td {{ padding: 0.45rem 0.7rem; border-bottom: 1px solid var(--border-color); }}
  .log-ok   td {{ color: var(--body-color); }}
  .log-miss td {{ opacity: 0.6; }}
  .log-err  td {{ color: #c0392b; }}
  .log-icon {{ text-align: center; font-weight: bold; width: 2rem; }}
  .log-id   {{ width: 3rem; opacity: 0.5; font-size: 0.75rem; }}
  .log-fields {{ opacity: 0.7; font-size: 0.8rem; }}
  .log-ok .log-icon   {{ color: #27ae60; }}
  .log-miss .log-icon {{ color: #e67e22; }}
  .log-err .log-icon  {{ color: #c0392b; }}
</style>

<div class="log-date">Ejecución: {now_str} — Job: {job_id}</div>
<div class="log-summary">
  <div class="log-stat"><span class="num">{summary['enriched']}</span><span class="lbl">Enriquecidos</span></div>
  <div class="log-stat"><span class="num">{summary['not_found']}</span><span class="lbl">No encontrados</span></div>
  <div class="log-stat"><span class="num">{summary['skipped']}</span><span class="lbl">Ya completos</span></div>
  <div class="log-stat"><span class="num">{summary['errors']}</span><span class="lbl">Errores</span></div>
</div>

<table class="log-table">
  <thead>
    <tr>
      <th>ID</th><th>Título</th><th></th><th>Fuente</th><th>Campos</th>
    </tr>
  </thead>
  <tbody>{rows}
  </tbody>
</table>
"""
    html += proc.FOOTER_HTML
    (STATIC_DIR / "log.html").write_text(html, encoding="utf-8")
    log.info("log.html generado")

def _notify_gotify_enrich(job_id: str, summary: dict):
    if not GOTIFY_URL or not GOTIFY_TOKEN:
        logging.warning(f"Gotify: URL o token vacíos — URL={repr(GOTIFY_URL)} TOKEN={repr(GOTIFY_TOKEN[:4] if GOTIFY_TOKEN else '')}")
        return
    log_link = f"{BASE_URL}/log.html"
    msg = (
        f"Enriquecidos: {summary['enriched']} | "
        f"No encontrados: {summary['not_found']} | "
        f"Errores: {summary['errors']}\n"
        f"Log: {log_link}"
    )
    logging.info(f"Gotify: enviando notificación a {GOTIFY_URL}")
    try:
        r = req_lib.post(
            f"{GOTIFY_URL}/message",
            json={"title": "Tellico2HTML — Enriquecimiento completado", "message": msg, "priority": 5},
            headers={"X-Gotify-Key": GOTIFY_TOKEN},
            timeout=5,
        )
        logging.info(f"Gotify: respuesta {r.status_code} — {r.text[:100]}")
        r.raise_for_status()
    except Exception as e:
        logging.warning(f"Gotify error: {e}")
