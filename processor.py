#!/usr/bin/env python3
"""
Tellico2HTML — Procesador principal
Lee una base de datos Tellico (.tc) y genera páginas HTML estáticas.
"""

import os
import sys
import gzip
import shutil
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from html import unescape
import requests

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("tellico2html")

# ─── Config desde entorno ──────────────────────────────────────────────────────
GOTIFY_URL        = os.getenv("GOTIFY_URL", "")
GOTIFY_TOKEN      = os.getenv("GOTIFY_TOKEN", "")
SERVER_IP         = os.getenv("SERVER_IP", "192.168.1.10")
NGINX_PORT        = os.getenv("NGINX_PORT", "8079")
THEME             = os.getenv("THEME", "industrial")
TELLICOPATH       = os.getenv("TELLICOPATH", "/libros")
TELLICODB         = os.getenv("TELLICODB", "libros 1.tc")

STATIC_DIR        = Path("/app/static")
BOOKS_DIR         = STATIC_DIR / "books"
IMAGES_DIR        = STATIC_DIR / "images"

BASE_URL          = f"http://{SERVER_IP}:{NGINX_PORT}/t2h"

NS = "http://periapsis.org/tellico/"

# ─── Utilidades XML ───────────────────────────────────────────────────────────

def parse_tellico(tc_path: Path) -> ET.Element:
    """Parsea un archivo .tc (que puede ser gzip). Retorna el elemento raíz."""
    raw = tc_path.read_bytes()
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)

def tag(name: str) -> str:
    return f"{{{NS}}}{name}"

def get_text(entry: ET.Element, field: str, default: str = "") -> str:
    el = entry.find(tag(field))
    if el is not None and el.text:
        return el.text.strip()
    return default

def get_list(entry: ET.Element, container: str, item: str) -> list[str]:
    """Extrae una lista como <authors><author>X</author></authors>"""
    container_el = entry.find(tag(container))
    if container_el is None:
        return []
    return [el.text.strip() for el in container_el.findall(tag(item)) if el.text]

def get_cdate(entry: ET.Element) -> datetime:
    cdate = entry.find(tag("cdate"))
    if cdate is None:
        return datetime.min
    try:
        y = int(cdate.find(tag("year")).text)
        m = int(cdate.find(tag("month")).text)
        d = int(cdate.find(tag("day")).text)
        return datetime(y, m, d)
    except Exception:
        return datetime.min

# ─── Parseo de entradas ───────────────────────────────────────────────────────

def parse_entries(root: ET.Element) -> list[dict]:
    collection = root.find(tag("collection"))
    if collection is None:
        log.error("No se encontró <collection> en el XML.")
        sys.exit(1)

    entries = []
    for entry in collection.findall(tag("entry")):
        eid     = get_text(entry, "id")
        title   = get_text(entry, "title")
        subtitle= get_text(entry, "subtitle")
        authors = get_list(entry, "authors", "author")
        pub_year= get_text(entry, "pub_year")
        pages   = get_text(entry, "pages")
        isbn    = get_text(entry, "isbn")
        cover   = get_text(entry, "cover")
        # plot puede venir en <plot> o en <comments>
        plot    = get_text(entry, "plot") or get_text(entry, "comments")
        genres  = get_list(entry, "genres", "genre")
        cdate   = get_cdate(entry)

        entries.append({
            "id":       eid,
            "title":    title,
            "subtitle": subtitle,
            "authors":  authors,
            "pub_year": pub_year,
            "pages":    pages,
            "isbn":     isbn,
            "cover":    cover,
            "plot":     plot,
            "genres":   genres,
            "cdate":    cdate,
        })

    entries.sort(key=lambda e: e["cdate"], reverse=True)
    return entries

# ─── Copiar imágenes ──────────────────────────────────────────────────────────

def copy_cover(cover_filename: str, source_dir: Path) -> bool:
    if not cover_filename:
        return False
    src = source_dir / cover_filename
    dst = IMAGES_DIR / cover_filename
    if dst.exists():
        return True
    if src.exists():
        shutil.copy2(src, dst)
        return True
    log.warning(f"Imagen no encontrada: {src}")
    return False

# ─── Gotify ───────────────────────────────────────────────────────────────────

def notify_gotify(title: str, book_id: str):
    if not GOTIFY_URL or not GOTIFY_TOKEN:
        return
    url   = f"{GOTIFY_URL}/message"
    link  = f"{BASE_URL}/books/{book_id}.html"
    payload = {
        "title":    f"Tellico2HTML — Nueva ficha: {title}",
        "message":  f"Libro: {title}\nFicha: {link}",
        "priority": 5,
    }
    try:
        r = requests.post(url, json=payload,
                          headers={"X-Gotify-Key": GOTIFY_TOKEN}, timeout=5)
        r.raise_for_status()
        log.info(f"Gotify notificado: {title}")
    except Exception as e:
        log.warning(f"Error Gotify: {e}")

# ─── Templates HTML ───────────────────────────────────────────────────────────

THEMES = {
    "industrial": {
        "body_bg":      "#1a1a1a",
        "body_color":   "#c8c8c8",
        "header_bg":    "#111",
        "accent":       "#b34700",
        "accent2":      "#8a3500",
        "link_color":   "#e05a00",
        "font_main":    "'Courier New', Courier, monospace",
        "font_title":   "'Courier New', Courier, monospace",
        "card_bg":      "#222",
        "border_color": "#444",
        "table_head_bg":"#2a2a2a",
        "btn_bg":       "#b34700",
        "btn_color":    "#fff",
        "scanline":     "none",
        "texture":      "repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.05) 2px, rgba(0,0,0,0.05) 4px)",
        "ornament":     "⚙",
    },
    "futurista": {
        "body_bg":      "#000",
        "body_color":   "#e0ffe0",
        "header_bg":    "#050505",
        "accent":       "#00ffe0",
        "accent2":      "#007a70",
        "link_color":   "#00ffe0",
        "font_main":    "'Trebuchet MS', sans-serif",
        "font_title":   "'Trebuchet MS', sans-serif",
        "card_bg":      "#0a0a0a",
        "border_color": "#00ffe040",
        "table_head_bg":"#001a18",
        "btn_bg":       "#00ffe0",
        "btn_color":    "#000",
        "scanline":     "repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(0,255,200,0.03) 3px, rgba(0,255,200,0.03) 4px)",
        "texture":      "none",
        "ornament":     "◈",
    },
    "antiguo": {
        "body_bg":      "#f4e8c1",
        "body_color":   "#2c1a0e",
        "header_bg":    "#2c1a0e",
        "accent":       "#8b4513",
        "accent2":      "#5c2e0a",
        "link_color":   "#7a3410",
        "font_main":    "Palatino, 'Palatino Linotype', serif",
        "font_title":   "Palatino, 'Palatino Linotype', serif",
        "card_bg":      "#fdf6e3",
        "border_color": "#c8a96e",
        "table_head_bg":"#ede0b8",
        "btn_bg":       "#8b4513",
        "btn_color":    "#f4e8c1",
        "scanline":     "none",
        "texture":      "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='4' height='4'%3E%3Crect width='4' height='4' fill='%23f4e8c1'/%3E%3Ccircle cx='1' cy='1' r='0.5' fill='%23c8a96e22'/%3E%3C/svg%3E\")",
        "ornament":     "✦",
    },
}

def get_theme(name: str) -> dict:
    return THEMES.get(name, THEMES["industrial"])

def _theme_css_block(selector: str, t: dict) -> str:
    """Genera un bloque CSS con variables para el selector dado."""
    vars_str = "\n".join([f"        --{k.replace('_','-')}: {v};" for k, v in t.items()])
    return f"    {selector} {{\n{vars_str}\n    }}"

def all_themes_css() -> str:
    """Emite los tres temas como bloques CSS con selectores data-theme.
    El tema por defecto (industrial) va en :root para que funcione sin JS."""
    blocks = []
    # :root = industrial (fallback sin JS)
    blocks.append(_theme_css_block(":root", THEMES["industrial"]))
    for name, t in THEMES.items():
        blocks.append(_theme_css_block(f'html[data-theme="{name}"]', t))
    return "\n".join(blocks)

def base_css(_unused=None) -> str:
    return f"""
{all_themes_css()}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
        background-color: var(--body-bg);
        color: var(--body-color);
        font-family: var(--font-main);
        font-size: 15px;
        line-height: 1.6;
        min-height: 100vh;
        background-image: var(--texture);
        transition: background-color 0.25s, color 0.25s;
    }}
    body::before {{
        content: '';
        position: fixed;
        inset: 0;
        background-image: var(--scanline);
        pointer-events: none;
        z-index: 9999;
    }}
    a {{ color: var(--link-color); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}

    header {{
        background: var(--header-bg);
        border-bottom: 2px solid var(--accent);
        padding: 0.8rem 1.5rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 0.5rem;
    }}
    header h1 {{
        font-family: var(--font-title);
        font-size: 1.4rem;
        color: var(--accent);
        letter-spacing: 0.05em;
    }}
    header .ornament {{ color: var(--accent); font-size: 1.2rem; }}

    .theme-switcher {{
        display: flex;
        gap: 0.4rem;
    }}
    .theme-btn {{
        background: transparent;
        border: 1px solid var(--accent);
        color: var(--link-color);
        padding: 0.2rem 0.6rem;
        font-family: var(--font-main);
        font-size: 0.75rem;
        cursor: pointer;
        transition: background 0.2s;
    }}
    .theme-btn:hover, .theme-btn.active {{
        background: var(--accent);
        color: var(--btn-color);
    }}

    main {{ padding: 1.5rem; max-width: 1200px; margin: 0 auto; }}

    .stats {{
        font-size: 0.82rem;
        opacity: 0.6;
        margin-bottom: 1rem;
    }}

    /* ── Tabla de libros ── */
    .book-table {{
        width: 100%;
        border-collapse: collapse;
    }}
    .book-table th {{
        background: var(--table-head-bg);
        color: var(--accent);
        font-family: var(--font-title);
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        padding: 0.6rem 0.8rem;
        text-align: left;
        border-bottom: 2px solid var(--accent);
        white-space: nowrap;
    }}
    .book-table td {{
        padding: 0.55rem 0.8rem;
        border-bottom: 1px solid var(--border-color);
        vertical-align: middle;
        font-size: 0.88rem;
    }}
    .book-table tr:hover td {{
        background: rgba(128,128,128,0.08);
    }}
    .col-id    {{ width: 3.5rem; text-align: center; opacity: 0.5; font-size: 0.75rem; }}
    .col-title {{ font-weight: bold; color: var(--body-color); }}
    .col-author{{ opacity: 0.85; }}
    .col-year  {{ width: 4rem; text-align: center; }}
    .col-pages {{ width: 4rem; text-align: center; }}
    .col-link  {{ width: 3rem; text-align: center; }}

    .arrow-link {{
        display: inline-block;
        font-size: 1.1rem;
        color: var(--accent);
        transition: transform 0.15s;
    }}
    .arrow-link:hover {{ transform: translateX(4px); text-decoration: none; }}

    /* ── Ficha de libro ── */
    .book-card {{
        background: var(--card-bg);
        border: 1px solid var(--border-color);
        border-radius: 4px;
        padding: 2rem;
        display: flex;
        gap: 2rem;
        flex-wrap: wrap;
        margin-bottom: 1.5rem;
    }}
    .book-cover img {{
        max-width: 180px;
        max-height: 280px;
        object-fit: cover;
        border: 2px solid var(--border-color);
        display: block;
    }}
    .book-cover .no-cover {{
        width: 140px;
        height: 200px;
        background: var(--table-head-bg);
        border: 2px dashed var(--border-color);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 2rem;
        color: var(--accent);
    }}
    .book-info {{ flex: 1; min-width: 240px; }}
    .book-info h2 {{
        font-family: var(--font-title);
        font-size: 1.4rem;
        color: var(--accent);
        margin-bottom: 0.3rem;
    }}
    .book-info .subtitle {{
        font-size: 0.95rem;
        opacity: 0.7;
        margin-bottom: 0.8rem;
        font-style: italic;
    }}
    .meta-table {{ border-collapse: collapse; width: 100%; margin-bottom: 1rem; }}
    .meta-table td {{ padding: 0.3rem 0; font-size: 0.88rem; }}
    .meta-table td:first-child {{
        color: var(--accent);
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        padding-right: 1rem;
        white-space: nowrap;
        width: 7rem;
    }}
    .plot-section {{ margin-top: 1.5rem; }}
    .plot-section h3 {{
        color: var(--accent);
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 0.5rem;
        border-bottom: 1px solid var(--border-color);
        padding-bottom: 0.3rem;
    }}
    .plot-section .plot-text {{ font-size: 0.9rem; line-height: 1.7; }}

    .btn-back {{
        display: inline-block;
        background: var(--btn-bg);
        color: var(--btn-color);
        padding: 0.5rem 1.2rem;
        font-family: var(--font-main);
        font-size: 0.85rem;
        border: none;
        cursor: pointer;
        text-decoration: none;
        transition: opacity 0.2s;
        margin-bottom: 1.5rem;
    }}
    .btn-back:hover {{ opacity: 0.85; text-decoration: none; }}
"""

THEME_SWITCHER_JS = """
<script>
(function() {
    var VALID = ['industrial', 'futurista', 'antiguo'];

    function applyTheme(name) {
        if (VALID.indexOf(name) === -1) name = 'industrial';
        document.documentElement.setAttribute('data-theme', name);
        localStorage.setItem('t2h_theme', name);
        document.querySelectorAll('.theme-btn').forEach(function(b) {
            b.classList.toggle('active', b.dataset.theme === name);
        });
    }

    // Aplicar tema guardado ANTES de que el navegador pinte (evita flash)
    var saved = localStorage.getItem('t2h_theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);

    document.addEventListener('DOMContentLoaded', function() {
        var current = localStorage.getItem('t2h_theme') || 'industrial';
        applyTheme(current);
        document.querySelectorAll('.theme-btn').forEach(function(b) {
            b.addEventListener('click', function() { applyTheme(b.dataset.theme); });
        });
    });
})();
</script>
"""

def header_html(active_theme: str, title: str = "Tellico2HTML", back_link: str = "") -> str:
    # active_theme es el tema por defecto del servidor (el del .env).
    # El JS lo sobreescribirá con el guardado en localStorage si existe.
    back = f'<a href="{back_link}" class="btn-back">← Volver</a>' if back_link else ""
    return f"""<!DOCTYPE html>
<html lang="es" data-theme="{active_theme}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<!-- El script de tema va PRIMERO para evitar flash de tema incorrecto -->
<script>
(function(){{
    var t = localStorage.getItem('t2h_theme');
    if (t) document.documentElement.setAttribute('data-theme', t);
}})();
</script>
<style>{base_css()}</style>
</head>
<body>
<header>
  <h1><span class="ornament">◆</span> Tellico2HTML</h1>
  <div class="theme-switcher">
    <button class="theme-btn" data-theme="industrial">Industrial</button>
    <button class="theme-btn" data-theme="futurista">Futurista</button>
    <button class="theme-btn" data-theme="antiguo">Antiguo</button>
  </div>
</header>
<main>
{back}
"""

FOOTER_HTML = f"""
</main>
{THEME_SWITCHER_JS}
</body>
</html>"""

# ─── Generador de index.html ──────────────────────────────────────────────────

def build_index(entries: list[dict], theme: str) -> str:
    rows = ""
    for e in entries:
        authors_str = ", ".join(e["authors"]) if e["authors"] else "—"
        cdate_iso   = e["cdate"].strftime("%Y%m%d") if e["cdate"].year > 1 else "00000000"
        rows += f"""
    <tr>
      <td class="col-id"   data-val="{e['id']}">{e['id']}</td>
      <td class="col-title" data-val="{e['title']}">{e['title']}</td>
      <td class="col-author" data-val="{authors_str}">{authors_str}</td>
      <td class="col-year"  data-val="{e['pub_year'] or '0'}">{e['pub_year'] or '—'}</td>
      <td class="col-pages" data-val="{e['pages'] or '0'}">{e['pages'] or '—'}</td>
      <td class="col-link"  data-val="{cdate_iso}" data-cdate="{cdate_iso}"><a class="arrow-link" href="books/{e['id']}.html" title="Ver ficha">→</a></td>
    </tr>"""

    count = len(entries)
    sort_js = """
<script>
(function() {{
  var API_URL = 'http://{server_ip}:{api_port}';
  function startEnrich() {{
    var btn = document.getElementById('enrichBtn');
    var panel = document.getElementById('enrichPanel');
    btn.disabled = true;
    panel.style.display = 'block';
    document.getElementById('enrichStatus').textContent = 'Iniciando…';

    fetch(API_URL + '/enrich', {{method: 'POST'}})
      .then(function(r) {{ return r.json(); }})
      .then(function(data) {{
        if (data.error) {{
          document.getElementById('enrichStatus').textContent = '⚠ ' + data.error;
          btn.disabled = false;
          return;
        }}
        pollEnrich();
      }})
      .catch(function() {{
        document.getElementById('enrichStatus').textContent = '✗ Error al conectar con el servidor';
        btn.disabled = false;
      }});
  }}

  function pollEnrich() {{
    fetch(API_URL + '/enrich/status')
      .then(function(r) {{ return r.json(); }})
      .then(function(s) {{
        var pct = s.total > 0 ? Math.round((s.current / s.total) * 100) : 0;
        document.getElementById('enrichProgress').value = pct;
        document.getElementById('enrichMsg').textContent = s.message || '';

        if (s.running) {{
          var txt = s.total > 0
            ? ('Procesando ' + s.current + ' / ' + s.total + ' (' + pct + '%)')
            : 'Iniciando…';
          document.getElementById('enrichStatus').textContent = txt;
          setTimeout(pollEnrich, 1500);
        }} else {{
          var sum = s.summary || {{}};
          document.getElementById('enrichStatus').innerHTML =
            '✓ Completado — <strong>' + (sum.enriched||0) + '</strong> enriquecidos, ' +
            (sum.not_found||0) + ' no encontrados, ' +
            (sum.skipped||0) + ' ya completos' +
            ' — <a href=\"log.html\">Ver log</a>';
          document.getElementById('enrichProgress').value = 100;
          document.getElementById('enrichBtn').disabled = false;
          setTimeout(function() {{ window.location.reload(); }}, 3000);
        }}
      }})
      .catch(function() {{ setTimeout(pollEnrich, 2000); }});
  }}

  window.startEnrich = startEnrich;
})();
</script>

<script>
(function() {
    // col index → tipo de ordenamiento
    var COL_TYPE = {0: 'num', 1: 'str', 2: 'str', 3: 'num', 4: 'num'};
    // Estado actual: columna 5 (cdate, oculta en flecha) descendente
    var sortCol = 5, sortAsc = false;

    function cellVal(row, col) {
        var td = row.querySelectorAll('td')[col];
        return td ? (td.getAttribute('data-val') || td.textContent.trim()) : '';
    }

    function sortTable(col, asc) {
        var tbody = document.querySelector('.book-table tbody');
        var rows  = Array.from(tbody.querySelectorAll('tr'));
        var type  = COL_TYPE[col] || 'str';

        rows.sort(function(a, b) {
            var av = cellVal(a, col), bv = cellVal(b, col);
            var cmp;
            if (type === 'num') {
                cmp = (parseFloat(av) || 0) - (parseFloat(bv) || 0);
            } else {
                cmp = av.localeCompare(bv, 'es', {sensitivity: 'base'});
            }
            return asc ? cmp : -cmp;
        });

        rows.forEach(function(r) { tbody.appendChild(r); });

        // Actualizar indicadores en encabezados
        document.querySelectorAll('.book-table th[data-col]').forEach(function(th) {
            var c = parseInt(th.getAttribute('data-col'));
            th.classList.toggle('sort-asc',  c === col &&  asc);
            th.classList.toggle('sort-desc', c === col && !asc);
            th.classList.toggle('sort-none', c !== col);
        });

        sortCol = col; sortAsc = asc;
    }

    // ── Filtro ──────────────────────────────────────────────────────────────
    function applyFilter(q) {
        var rows  = document.querySelectorAll('.book-table tbody tr');
        var terms = q.toLowerCase().trim().split(' ').filter(Boolean);
        var shown = 0;

        rows.forEach(function(row) {
            // Buscar en título (col 1), autor (col 2) y año (col 3)
            var text = [1, 2, 3].map(function(i) {
                var td = row.querySelectorAll('td')[i];
                return td ? td.textContent.toLowerCase() : '';
            }).join(' ');

            var match = terms.length === 0 || terms.every(function(t) {
                return text.indexOf(t) !== -1;
            });

            row.style.display = match ? '' : 'none';
            if (match) shown++;
        });

        var countEl = document.getElementById('filterCount');
        if (countEl) {
            countEl.textContent = terms.length ? (shown + ' resultado' + (shown !== 1 ? 's' : '')) : '';
        }
        var clearBtn = document.getElementById('filterClear');
        if (clearBtn) clearBtn.classList.toggle('visible', q.length > 0);
    }

    document.addEventListener('DOMContentLoaded', function() {
        // Aplicar orden inicial (cdate desc)
        sortTable(5, false);

        document.querySelectorAll('.book-table th[data-col]').forEach(function(th) {
            th.style.cursor = 'pointer';
            th.addEventListener('click', function() {
                var col = parseInt(th.getAttribute('data-col'));
                var asc = (col === sortCol) ? !sortAsc : true;
                sortTable(col, asc);
            });
        });

        // Filtro
        var input    = document.getElementById('filterInput');
        var clearBtn = document.getElementById('filterClear');
        if (input) {
            input.addEventListener('input', function() { applyFilter(input.value); });
        }
        if (clearBtn) {
            clearBtn.addEventListener('click', function() {
                input.value = '';
                applyFilter('');
                input.focus();
            });
        }
    });
})();
</script>
"""

    api_port = os.getenv("TELLICO2HTML_EXTERNAL_PORT", "7995")
    # Separar el JS del enriquecimiento (tiene placeholders) del JS de ordenamiento
    _sep = "  window.startEnrich = startEnrich;\n})();\n</script>\n\n<script>\n(function() {"
    _parts = sort_js.split(_sep, 1)
    enrich_js = _parts[0].replace('{server_ip}', SERVER_IP).replace('{api_port}', api_port)
    enrich_js = enrich_js.replace('{{', '{').replace('}}', '}')
    enrich_js += "  window.startEnrich = startEnrich;\n})();\n</script>\n\n<script>\n(function() {"
    sort_js = enrich_js + _parts[1]
    html = header_html(theme, "Colección de Libros")
    html += f"""
<style>
  .book-table th[data-col]::after {{ content: ' ↕'; opacity: 0.35; font-size: 0.7em; }}
  .book-table th.sort-asc::after  {{ content: ' ↑'; opacity: 1; }}
  .book-table th.sort-desc::after {{ content: ' ↓'; opacity: 1; }}
  .book-table th.sort-none::after {{ content: ' ↕'; opacity: 0.35; }}
  .book-table th[data-col]:hover  {{ opacity: 0.8; }}

  .enrich-btn {{
    background: var(--accent);
    color: var(--btn-color);
    border: none;
    font-family: var(--font-main);
    font-size: 0.8rem;
    padding: 0.3rem 0.8rem;
    cursor: pointer;
    margin-left: auto;
    transition: opacity 0.2s;
  }}
  .enrich-btn:hover {{ opacity: 0.8; }}
  .enrich-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
  .filter-bar {{
    display: flex;
    align-items: center;
    gap: 0.6rem;
    margin-bottom: 0.8rem;
  }}
  .filter-input {{
    background: var(--card-bg);
    border: 1px solid var(--border-color);
    color: var(--body-color);
    font-family: var(--font-main);
    font-size: 0.88rem;
    padding: 0.35rem 0.7rem;
    width: 260px;
    outline: none;
    transition: border-color 0.2s;
  }}
  .filter-input:focus {{ border-color: var(--accent); }}
  .filter-input::placeholder {{ opacity: 0.4; }}
  .filter-clear {{
    background: transparent;
    border: 1px solid var(--border-color);
    color: var(--link-color);
    font-family: var(--font-main);
    font-size: 0.8rem;
    padding: 0.3rem 0.6rem;
    cursor: pointer;
    display: none;
  }}
  .filter-clear.visible {{ display: inline-block; }}
  .filter-count {{ font-size: 0.8rem; opacity: 0.55; }}
</style>
<div class="filter-bar">
  <input class="filter-input" id="filterInput" type="text" placeholder="Filtrar por título, autor, año…" autocomplete="off">
  <button class="filter-clear" id="filterClear">✕ Limpiar</button>
  <span class="filter-count" id="filterCount"></span>
  <button class="enrich-btn" id="enrichBtn" onclick="startEnrich()">⚡ Enriquecer</button>
</div>
<div id="enrichPanel" style="display:none; margin-bottom:1rem; padding:0.8rem; border:1px solid var(--border-color); background:var(--card-bg); font-size:0.85rem;">
  <div id="enrichStatus">Iniciando…</div>
  <progress id="enrichProgress" value="0" max="100" style="width:100%; height:6px; margin-top:0.4rem; display:block;"></progress>
  <div id="enrichMsg" style="opacity:0.6; font-size:0.78rem; margin-top:0.3rem;"></div>
</div>
<p class="stats">{count} libro{'s' if count != 1 else ''} en la colección</p>
<table class="book-table">
  <thead>
    <tr>
      <th class="col-id sort-none"    data-col="0">ID</th>
      <th class="col-title sort-none" data-col="1">Título</th>
      <th class="col-author sort-none" data-col="2">Autor</th>
      <th class="col-year sort-none"  data-col="3">Año</th>
      <th class="col-pages sort-none" data-col="4">Págs.</th>
      <th class="col-link sort-desc"  data-col="5"></th>
    </tr>
  </thead>
  <tbody>{rows}
  </tbody>
</table>
{sort_js}"""
    html += FOOTER_HTML
    return html

# ─── Generador de ficha id.html ──────────────────────────────────────────────

def build_book_page(entry: dict, theme: str) -> str:
    cover_src = ""
    if entry["cover"]:
        cover_src = f"../images/{entry['cover']}"

    cover_html = ""
    if cover_src:
        cover_html = f'<div class="book-cover"><img src="{cover_src}" alt="Portada"></div>'
    else:
        t = get_theme(theme)
        cover_html = f'<div class="book-cover"><div class="no-cover">{t["ornament"]}</div></div>'

    authors_str = ", ".join(entry["authors"]) if entry["authors"] else "—"
    subtitle_html = f'<div class="subtitle">{entry["subtitle"]}</div>' if entry["subtitle"] else ""

    genres_str = ", ".join(entry["genres"]) if entry.get("genres") else ""
    meta_rows = ""
    for label, value in [
        ("Autor", authors_str),
        ("Género", genres_str or "—"),
        ("ISBN", entry["isbn"] or "—"),
        ("Publicación", entry["pub_year"] or "—"),
        ("Páginas", entry["pages"] or "—"),
    ]:
        meta_rows += f"<tr><td>{label}</td><td>{value}</td></tr>"

    plot_html = ""
    if entry["plot"]:
        plot_html = f"""
<div class="plot-section">
  <h3>Sinopsis</h3>
  <div class="plot-text">{entry['plot']}</div>
</div>"""

    # Foto del autor (solo si está disponible, provista por el enricher)
    author_photo_html = ""
    if entry.get("author_photo"):
        author_photo_html = f'''
<div class="author-photo-section">
  <h3>Autor</h3>
  <img src="../images/{entry["author_photo"]}" alt="Foto del autor" class="author-photo">
</div>'''

    book_id  = entry["id"]
    api_port = os.getenv("TELLICO2HTML_EXTERNAL_PORT", "7995")
    api_url  = f"http://{SERVER_IP}:{api_port}"

    authors_val  = ", ".join(entry["authors"]) if entry["authors"] else ""
    genres_val   = ", ".join(entry.get("genres", [])) if entry.get("genres") else ""

    html  = header_html(theme, entry["title"], back_link="../index.html")
    html += f"""
<style>
  .author-photo-section {{ margin-top: 1.2rem; }}
  .author-photo-section h3 {{
    color: var(--accent); font-size: 0.8rem; text-transform: uppercase;
    letter-spacing: 0.08em; margin-bottom: 0.5rem;
    border-bottom: 1px solid var(--border-color); padding-bottom: 0.3rem;
  }}
  .author-photo {{ max-width: 120px; border-radius: 50%; border: 2px solid var(--border-color); display: block; }}
  .edit-btn {{
    float: right; background: transparent; border: 1px solid var(--accent);
    color: var(--link-color); font-family: var(--font-main); font-size: 0.78rem;
    padding: 0.2rem 0.6rem; cursor: pointer; margin-top: 0.3rem;
  }}
  .edit-btn:hover {{ background: var(--accent); color: var(--btn-color); }}
  .edit-form {{
    display: none; margin-top: 1.5rem; padding: 1.2rem;
    border: 1px solid var(--border-color); background: var(--card-bg);
  }}
  .edit-form.visible {{ display: block; }}
  .edit-form h3 {{
    color: var(--accent); font-size: 0.8rem; text-transform: uppercase;
    letter-spacing: 0.08em; margin-bottom: 1rem;
  }}
  .edit-field {{ margin-bottom: 0.8rem; }}
  .edit-field label {{
    display: block; font-size: 0.75rem; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--accent); margin-bottom: 0.25rem;
  }}
  .edit-field input, .edit-field textarea {{
    width: 100%; background: var(--body-bg); border: 1px solid var(--border-color);
    color: var(--body-color); font-family: var(--font-main); font-size: 0.88rem;
    padding: 0.35rem 0.6rem; outline: none;
  }}
  .edit-field input:focus, .edit-field textarea:focus {{ border-color: var(--accent); }}
  .edit-field textarea {{ min-height: 100px; resize: vertical; }}
  .edit-actions {{ display: flex; gap: 0.6rem; margin-top: 1rem; }}
  .save-btn {{
    background: var(--btn-bg); color: var(--btn-color); border: none;
    font-family: var(--font-main); font-size: 0.85rem;
    padding: 0.45rem 1.2rem; cursor: pointer;
  }}
  .save-btn:hover {{ opacity: 0.85; }}
  .save-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
  .cancel-btn {{
    background: transparent; border: 1px solid var(--border-color);
    color: var(--body-color); font-family: var(--font-main); font-size: 0.85rem;
    padding: 0.45rem 1rem; cursor: pointer;
  }}
  .edit-status {{ font-size: 0.82rem; margin-top: 0.5rem; }}
  .cover-preview {{ max-width: 100px; margin-top: 0.4rem; display: block; }}
</style>

<div class="book-card">
  {cover_html}
  <div class="book-info">
    <h2>{entry['title']} <button class="edit-btn" onclick="toggleEdit()">✎ Editar</button></h2>
    {subtitle_html}
    <table class="meta-table">
      {meta_rows}
    </table>
    {author_photo_html}
    {plot_html}
  </div>
</div>

<div class="edit-form" id="editForm">
  <h3>Editar ficha</h3>
  <div class="edit-field">
    <label>Título</label>
    <input type="text" id="ef-title" value="{entry['title']}">
  </div>
  <div class="edit-field">
    <label>Autor(es) — separados por coma</label>
    <input type="text" id="ef-authors" value="{authors_val}">
  </div>
  <div class="edit-field">
    <label>Año de publicación</label>
    <input type="text" id="ef-year" value="{entry.get('pub_year', '')}">
  </div>
  <div class="edit-field">
    <label>Género(s) — separados por coma</label>
    <input type="text" id="ef-genres" value="{genres_val}">
  </div>
  <div class="edit-field">
    <label>ISBN</label>
    <input type="text" id="ef-isbn" value="{entry.get('isbn', '')}">
  </div>
  <div class="edit-field">
    <label>Sinopsis</label>
    <textarea id="ef-plot">{entry.get('plot', '')}</textarea>
  </div>
  <div class="edit-field">
    <label>Carátula (imagen desde tu dispositivo)</label>
    <input type="file" id="ef-cover" accept="image/*" onchange="previewCover(this, 'ef-cover-preview')">
    <img id="ef-cover-preview" class="cover-preview" style="display:none">
  </div>
  <div class="edit-field">
    <label>Foto del autor (imagen desde tu dispositivo)</label>
    <input type="file" id="ef-author-photo" accept="image/*" onchange="previewCover(this, 'ef-author-preview')">
    <img id="ef-author-preview" class="cover-preview" style="display:none">
  </div>
  <div class="edit-actions">
    <button class="save-btn" id="saveBtn" onclick="saveBook()">Guardar</button>
    <button class="cancel-btn" onclick="toggleEdit()">Cancelar</button>
  </div>
  <div class="edit-status" id="editStatus"></div>
</div>

<script>
(function() {{
  var API_URL = '{api_url}';
  var BOOK_ID = '{book_id}';

  window.toggleEdit = function() {{
    var form = document.getElementById('editForm');
    form.classList.toggle('visible');
  }};

  window.previewCover = function(input, previewId) {{
    var preview = document.getElementById(previewId);
    if (input.files && input.files[0]) {{
      var reader = new FileReader();
      reader.onload = function(e) {{
        preview.src = e.target.result;
        preview.style.display = 'block';
      }};
      reader.readAsDataURL(input.files[0]);
    }}
  }};

  window.saveBook = function() {{
    var btn    = document.getElementById('saveBtn');
    var status = document.getElementById('editStatus');
    btn.disabled = true;
    status.textContent = 'Guardando…';

    var fd = new FormData();
    fd.append('title',    document.getElementById('ef-title').value);
    fd.append('authors',  document.getElementById('ef-authors').value);
    fd.append('pub_year', document.getElementById('ef-year').value);
    fd.append('genres',   document.getElementById('ef-genres').value);
    fd.append('isbn',     document.getElementById('ef-isbn').value);
    fd.append('plot',     document.getElementById('ef-plot').value);
    var coverFile = document.getElementById('ef-cover').files[0];
    if (coverFile) fd.append('cover', coverFile);
    var authorPhotoFile = document.getElementById('ef-author-photo').files[0];
    if (authorPhotoFile) fd.append('author_photo', authorPhotoFile);

    fetch(API_URL + '/book/' + BOOK_ID, {{method: 'POST', body: fd}})
      .then(function(r) {{ return r.json(); }})
      .then(function(data) {{
        if (data.status === 'ok') {{
          status.textContent = '✓ Guardado. Recargando…';
          setTimeout(function() {{ window.location.reload(); }}, 1200);
        }} else {{
          status.textContent = '✗ Error: ' + (data.detail || JSON.stringify(data));
          btn.disabled = false;
        }}
      }})
      .catch(function(e) {{
        status.textContent = '✗ Error de conexión';
        btn.disabled = false;
      }});
  }};
}})();
</script>
"""
    html += FOOTER_HTML
    return html

# ─── Procesamiento de índice incremental ─────────────────────────────────────

def get_existing_ids_from_index(index_path: Path) -> set[str]:
    """Extrae los IDs ya presentes en index.html buscando 'books/ID.html'."""
    if not index_path.exists():
        return set()
    content = index_path.read_text(encoding="utf-8")
    import re
    return set(re.findall(r'href="books/(\d+)\.html"', content))

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Tellico2HTML processor")
    parser.add_argument(
        "--force", action="store_true",
        help="Regenerar todas las fichas aunque ya existan (útil al actualizar temas/plantillas)"
    )
    args = parser.parse_args()

    log.info("=== Tellico2HTML iniciando ===")
    if args.force:
        log.info("Modo --force: se regenerarán todas las fichas existentes")

    # Resolver ruta de la base de datos
    tc_path = Path(TELLICOPATH) / TELLICODB
    if not tc_path.exists():
        log.error(f"Base de datos no encontrada: {tc_path}")
        sys.exit(1)
    log.info(f"Base de datos: {tc_path}")

    # Directorio de imágenes fuente: mismo nombre de DB sin extensión + _files
    db_stem    = Path(TELLICODB).stem          # "libros 1"
    img_source = Path(TELLICOPATH) / f"{db_stem}_files"
    log.info(f"Imágenes fuente: {img_source}")

    # Crear directorios destino
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)

    # Parsear XML
    root    = parse_tellico(tc_path)
    entries = parse_entries(root)
    log.info(f"Entradas parseadas: {len(entries)}")

    index_path   = STATIC_DIR / "index.html"
    existing_ids = get_existing_ids_from_index(index_path)
    log.info(f"IDs ya en index: {len(existing_ids)}")

    all_ids = {e["id"] for e in entries}
    new_ids = all_ids - existing_ids
    log.info(f"Nuevas entradas: {len(new_ids)}")

    # Si no había ningún libro previo, es una importación inicial: no notificar
    primera_vez = len(existing_ids) == 0
    if primera_vez:
        log.info("Primera ejecución detectada (colección vacía): las notificaciones Gotify están desactivadas para esta corrida.")

    # Siempre regenerar index (eficiente: barato)
    log.info("Generando index.html …")
    index_html = build_index(entries, THEME)
    index_path.write_text(index_html, encoding="utf-8")
    log.info("index.html generado.")

    # Generar fichas: solo nuevas, o todas si --force
    for entry in entries:
        book_path = BOOKS_DIR / f"{entry['id']}.html"
        is_new = not book_path.exists()

        if not is_new and not args.force:
            continue

        # Copiar imagen (siempre, por si no estaba)
        if entry["cover"] and img_source.exists():
            copy_cover(entry["cover"], img_source)

        # Generar HTML
        page = build_book_page(entry, THEME)
        book_path.write_text(page, encoding="utf-8")
        log.info(f"Ficha {'regenerada' if not is_new else 'generada'}: {book_path.name}")

        # Notificar Gotify solo para fichas realmente nuevas y fuera de importación inicial
        if is_new and not primera_vez and not args.force:
            notify_gotify(entry["title"], entry["id"])

    log.info("=== Procesamiento completado ===")

if __name__ == "__main__":
    main()
