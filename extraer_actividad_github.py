"""Extrae la actividad de GitHub de un usuario y la guarda en CSV para Power BI.

Solo lee (usa `gh api`, que debe estar autenticado). No guarda mensajes de commits,
nombres de archivos ni nombres de personas: solo conteos, fechas, horas y clasificaciones.

Uso:
  python extraer_actividad_github.py --usuario mi-usuario \
      --repos mi-repo-1 mi-repo-2 mi-repo-3 \
      --anonimo propietario/repositorio=cliente-1

--anonimo (se puede repetir): repositorio de terceros que se incluye con un alias.
El nombre real solo se usa para consultar la API y nunca se escribe en los archivos de salida.

Carga incremental: el detalle de cada commit (archivos y líneas) se guarda en `.cache_detalle.json`
dentro de la carpeta de salida, por hash del SHA. Las siguientes ejecuciones solo consultan los
commits nuevos. Usa --completo para recalcular todo (por ejemplo, si cambias las reglas de este
script; la caché se descarta sola cuando cambian RX_GENERADO o RX_PRUEBA). Si GitHub responde con
límite de llamadas, el avance se conserva y basta con volver a ejecutar.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ----------------------------------------------------------------- clasificaciones
TIPOS = [  # orden de prioridad: el primero que coincide gana
    ("Corrección", r"\b(fix|fixes|fixed|correcci|corrig|bug|arregl|error|solucion|resolve)"),
    ("Funcionalidad", r"\b(feat|feature|agreg|nuevo|nueva|implement|crea|creaci|añad|add|módulo|modulo|página|pagina|vista)"),
    ("Pruebas", r"\b(test|tests|prueba|pruebas|e2e|vitest|rules|qa)\b"),
    ("Documentación", r"\b(docs?|readme|document|bitácora|bitacora|plan|ficha)"),
    ("Mantenimiento", r"\b(refactor|chore|lint|ci|build|deps|config|mejora|ajust|version|versión|bump|release)"),
]
IA = [("Claude", r"co-authored-by:\s*claude"), ("Gemini", r"co-authored-by:\s*gemini"),
      ("ChatGPT", r"co-authored-by:\s*(chatgpt|openai)"), ("Copilot", r"co-authored-by:\s*(github )?copilot")]
RX_PRUEBA = re.compile(r"(\.test\.|\.spec\.|/__tests__/|^e2e/|/e2e/|rules-tests/|firestore-tests/|^tests?/|/tests?/)", re.I)


# Archivos que no son codigo escrito a mano (datos, binarios, dependencias, compilados): no cuentan en el tamano del cambio
RX_GENERADO = re.compile(r"(^|/)(node_modules|dist|build|\.next|\.pbi)/|(^|/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock)$|\.min\.(js|css)$|\.(csv|pdf|png|jpe?g|gif|svg|ico|pbix|xlsx|lock|map)$", re.I)


# Colores por proyecto. Se asignan por orden alfabetico del nombre visible, ignorando mayusculas, acentos y
# signos (asi ordena Power BI las series). Es el mismo orden que `dataColors` del tema del informe, de modo
# que los graficos con series por proyecto y el ranking coinciden. Con mas de 12 proyectos se repite la paleta.
PALETA = ["#1F3864", "#2E75B6", "#17A2A2", "#8E5EA2", "#E0A100", "#7F8C9A",
          "#2E8B57", "#A94442", "#8DB3E2", "#D2691E", "#B5517A", "#6B8E23"]


def clave_orden(texto):
    n = unicodedata.normalize("NFKD", texto)
    return "".join(ch for ch in n if ch.isalnum()).casefold()


def asignar_colores(repo_rows):
    for i, r in enumerate(sorted(repo_rows, key=lambda r: clave_orden(r["nombre_visible"]))):
        r["color"] = PALETA[i % len(PALETA)]


def clasificar(msg):
    primera = msg.split("\n")[0]
    for nombre, rx in TIPOS:
        if re.search(rx, primera, re.I):
            return nombre
    return "Otro"


def ia_herramienta(msg):
    for nombre, rx in IA:
        if re.search(rx, msg, re.I):
            return nombre
    return "Ninguna"


# ----------------------------------------------------------------- acceso a la API
class LimiteTasa(Exception):
    """GitHub devolvio 403 por limite de llamadas (5.000/hora autenticado)."""


def gh(args, reintentos=3):
    for i in range(reintentos):
        try:
            r = subprocess.run(["gh"] + args, capture_output=True)
        except FileNotFoundError:
            raise SystemExit("No se encontró GitHub CLI (gh). Instálalo desde https://cli.github.com "
                             "y ejecuta una vez: gh auth login")
        if r.returncode == 0:
            try:
                return json.loads(r.stdout.decode("utf-8", "replace"))
            except Exception:
                return None
        err = r.stderr.decode("utf-8", "replace").lower()
        if "rate limit" in err:
            raise LimiteTasa(err.strip())
        if "http 404" in err or "not found" in err:
            return None  # repo inexistente o sin acceso: reintentar no ayuda
        time.sleep(1.5 * (i + 1))
    return None


def verificar_gh():
    try:
        r = subprocess.run(["gh", "auth", "status"], capture_output=True)
    except FileNotFoundError:
        raise SystemExit("No se encontró GitHub CLI (gh). Instálalo desde https://cli.github.com "
                         "y ejecuta una vez: gh auth login")
    if r.returncode != 0:
        raise SystemExit("gh no está autenticado. Ejecuta una vez: gh auth login")


# ----------------------------------------------------------------- cache incremental
# El listado de commits es barato (100 por llamada) y se recorre siempre completo; lo caro es el detalle
# (1 llamada por commit). Se guarda por hash del SHA y se consulta solo lo que falta.
def firma_reglas():
    return hashlib.sha1((RX_GENERADO.pattern + "|" + RX_PRUEBA.pattern).encode()).hexdigest()[:12]


def hash_sha(sha):
    return hashlib.sha1(sha.encode()).hexdigest()[:16]


def cargar_cache(ruta):
    try:
        d = json.loads(Path(ruta).read_text(encoding="utf-8"))
        if d.get("firma") == firma_reglas():
            return d.get("repos", {})
    except Exception:
        pass
    return {}


def guardar_cache(ruta, repos):
    Path(ruta).write_text(json.dumps({"firma": firma_reglas(), "repos": repos}, separators=(",", ":")), encoding="utf-8")


def api(ruta):
    return gh(["api", ruta])


def listar_commits(slug, autor=None):
    salida, pagina = [], 1
    while True:
        ruta = f"repos/{slug}/commits?per_page=100&page={pagina}" + (f"&author={autor}" if autor else "")
        d = api(ruta)
        if not d:
            break
        salida += d
        if len(d) < 100:
            break
        pagina += 1
    return salida


def detalle(slug, sha):
    try:
        d = api(f"repos/{slug}/commits/{sha}")
    except LimiteTasa:
        return "LIMITE"
    if not d:
        return None
    archivos = d.get("files") or []
    st = d.get("stats") or {}
    if 0 < len(archivos) < 300:   # la API recorta a 300 archivos: si hay detalle completo, se excluyen los generados
        utiles = [f for f in archivos if not RX_GENERADO.search(f.get("filename", ""))]
        mas, menos = sum(f.get("additions", 0) for f in utiles), sum(f.get("deletions", 0) for f in utiles)
    else:
        mas, menos = st.get("additions", 0), st.get("deletions", 0)
    return {
        "archivos": len(archivos),
        "mas": mas,
        "menos": menos,
        "pruebas": sum(1 for f in archivos if RX_PRUEBA.search(f.get("filename", ""))),
    }


def a_local(iso, utc_offset):
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return t.astimezone(dt.timezone(dt.timedelta(hours=utc_offset)))


# ----------------------------------------------------------------- calidad de la extraccion
def total_commits_api(slug, autor=None):
    """Total de commits segun GitHub por otra via: con per_page=1, la ultima pagina del encabezado Link es el total.
    Sirve para detectar que el listado paginado se corto a la mitad (una pagina fallida termina el listado en silencio)."""
    ruta = f"repos/{slug}/commits?per_page=1" + (f"&author={autor}" if autor else "")
    try:
        r = subprocess.run(["gh", "api", "-i", ruta], capture_output=True)
    except FileNotFoundError:
        return None
    if r.returncode != 0:
        if "rate limit" in r.stderr.decode("utf-8", "replace").lower():
            raise LimiteTasa("limite de tasa")
        return None
    partes = re.split(r"\r?\n\r?\n", r.stdout.decode("utf-8", "replace"), maxsplit=1)
    cab, cuerpo = (partes + [""])[:2]
    m = re.search(r'[?&]page=(\d+)[^>]*>;\s*rel="last"', cab)
    if m:
        return int(m.group(1))
    try:
        return len(json.loads(cuerpo))
    except Exception:
        return None


def informe_calidad(out, objetivos, commits_rows, repo_rows, contrib, totales, sin_det, hoy):
    """Verificaciones sobre lo extraido; solo conteos. Se guarda en calidad_extraccion.csv y se resume en pantalla."""
    res = []

    def add(verificacion, repo, esperado, obtenido, tipo="IGUAL"):
        if tipo == "IGUAL":
            r = "OK" if esperado == obtenido else "REVISAR"
        elif tipo == "CERO":
            r = "OK" if obtenido == 0 else "REVISAR"
        else:
            r = "INFO"
        res.append({"verificacion": verificacion, "repo_key": repo, "esperado": esperado, "obtenido": obtenido, "resultado": r})

    ids = [f["commit_id"] for f in commits_rows]
    add("ids_unicos", "(todos)", len(ids), len(set(ids)))
    por_repo = Counter(f["repo_key"] for f in commits_rows)
    dim = {r["repo_key"]: r["commits"] for r in repo_rows}
    for _, key, *_ in objetivos:
        tot = totales.get(key)
        add("commits_vs_total_github", key, "n/d" if tot is None else tot, por_repo.get(key, 0), "INFO" if tot is None else "IGUAL")
        add("dim_repo_vs_fact_commits", key, dim.get(key, 0), por_repo.get(key, 0))
        add("commits_sin_detalle", key, 0, sin_det.get(key, 0), "CERO")
    fuera = sum(1 for f in commits_rows if not (0 <= f["hora_local"] <= 23) or f["fecha"] > hoy.isoformat())
    add("fechas_y_horas_validas", "(todos)", 0, fuera, "CERO")
    otros = sum(1 for f in commits_rows if f["tipo_cambio"] == "Otro")
    add("pct_commits_sin_clasificar", "(todos)", "", round(100 * otros / max(len(commits_rows), 1), 1), "INFO")
    if contrib:
        con_contrib = {c["fecha"] for c in contrib if c["contribuciones"] > 0}
        add("dias_con_commit_sin_contribucion", "(todos)", "", len({f["fecha"] for f in commits_rows} - con_contrib), "INFO")
    with open(out / "calidad_extraccion.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["verificacion", "repo_key", "esperado", "obtenido", "resultado"])
        w.writeheader()
        w.writerows(res)
    revisar = [r for r in res if r["resultado"] == "REVISAR"]
    print(f"\nCalidad de la extracción: {sum(r['resultado'] == 'OK' for r in res)} OK, {len(revisar)} por revisar "
          f"(detalle en calidad_extraccion.csv)")
    for r in revisar:
        print(f"   REVISAR {r['verificacion']} [{r['repo_key']}]: esperado {r['esperado']}, obtenido {r['obtenido']}")


# ----------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usuario", required=True)
    ap.add_argument("--repos", nargs="+", default=[])
    ap.add_argument("--anonimo", action="append", default=[], help="propietario/repositorio=alias")
    ap.add_argument("--desde", type=int, default=2022, help="año inicial para el calendario de contribuciones")
    ap.add_argument("--utc-offset", type=int, default=-5, help="zona horaria local (Colombia = -5)")
    ap.add_argument("--salida", default=str(Path(__file__).parent / "Datos"))
    ap.add_argument("--completo", action="store_true",
                    help="ignora la caché y vuelve a consultar el detalle de todos los commits")
    a = ap.parse_args()
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    verificar_gh()
    ruta_cache = out / ".cache_detalle.json"
    cache = {} if a.completo else cargar_cache(ruta_cache)

    objetivos = []  # (slug_para_api, repo_key, nombre_visible, tipo, visibilidad, filtrar_autor)
    for nombre in a.repos:
        info = api(f"repos/{a.usuario}/{nombre}")
        if not info:
            print("  (no accesible, se omite):", nombre)
            continue
        privado = info.get("private", False)
        objetivos.append((f"{a.usuario}/{nombre}", nombre, nombre,
                          "Aplicación propia (privada)" if privado else "Portafolio (público)",
                          "Privado" if privado else "Público", None))
    for par in a.anonimo:
        slug, alias = par.split("=", 1)
        objetivos.append((slug, alias, "Proyecto de cliente (consultoría)", "Proyecto de cliente (anónimo)",
                          "Privado de terceros", a.usuario))

    commits_rows, repo_rows, leng_rows = [], [], []
    totales, sin_det = {}, {}
    for slug, key, visible, tipo, visib, autor in objetivos:
        print(f"-> {visible} ...", flush=True)
        totales[key] = total_commits_api(slug, autor)
        lista = listar_commits(slug, autor)
        if not lista:
            print("   AVISO: sin commits visibles o sin acceso (revisa el nombre y tus permisos); se omite este repositorio.")
            continue
        previo = cache.get(key, {})
        pendientes = [c for c in lista if hash_sha(c["sha"]) not in previo]
        with ThreadPoolExecutor(max_workers=8) as ex:
            nuevos = list(ex.map(lambda c: detalle(slug, c["sha"]), pendientes))
        vigente = {hash_sha(c["sha"]): previo[hash_sha(c["sha"])] for c in lista if hash_sha(c["sha"]) in previo}
        limite, sin_detalle = False, 0
        for c, d in zip(pendientes, nuevos):
            if d == "LIMITE":
                limite = True
            elif d is None:
                sin_detalle += 1
            else:
                vigente[hash_sha(c["sha"])] = d
        sin_det[key] = sin_detalle
        cache[key] = vigente  # tambien descarta commits que ya no existen en el repo
        guardar_cache(ruta_cache, cache)
        if limite:
            print("\nSe alcanzó el límite de llamadas de GitHub (5.000 por hora). El avance quedó guardado:\n"
                  "vuelve a ejecutar el mismo comando más tarde y continuará donde quedó. No se escribieron los CSV.")
            sys.exit(2)
        print(f"   {len(lista)} commits: {len(pendientes)} consultados, {len(lista) - len(pendientes)} desde caché"
              + (f", {sin_detalle} SIN detalle (cuentan con 0 archivos/líneas; reintenta)" if sin_detalle else ""))
        filas = []
        for c in lista:
            msg = c["commit"]["message"]
            t = a_local(c["commit"]["author"]["date"], a.utc_offset)
            d = vigente.get(hash_sha(c["sha"])) or {"archivos": 0, "mas": 0, "menos": 0, "pruebas": 0}
            filas.append({
                "repo_key": key, "_t": t,
                "fecha": t.date().isoformat(), "hora_local": t.hour, "fecha_hora_local": t.strftime("%Y-%m-%d %H:%M:%S"),
                "tipo_cambio": clasificar(msg),
                "referencia_issue": 1 if re.search(r"#\d+", msg.split("\n")[0]) else 0,
                "es_merge": 1 if len(c.get("parents", [])) > 1 else 0,
                "ia_coautor": 0 if ia_herramienta(msg) == "Ninguna" else 1,
                "ia_herramienta": ia_herramienta(msg),
                "archivos_cambiados": d["archivos"], "lineas_anadidas": d["mas"], "lineas_eliminadas": d["menos"],
                "archivos_prueba_tocados": d["pruebas"],
            })
        filas.sort(key=lambda r: r["_t"])
        for i, f in enumerate(filas, 1):
            f["commit_id"] = f"{key}-{i:04d}"
        commits_rows += filas

        # calidad del repositorio (solo conteos)
        info = api(f"repos/{slug}")
        rama = (info or {}).get("default_branch", "main")
        arbol = (api(f"repos/{slug}/git/trees/{rama}?recursive=1") or {}).get("tree", [])
        rutas = [x["path"] for x in arbol if x["type"] == "blob" and not x["path"].startswith(("node_modules/", ".git/"))]
        langs = api(f"repos/{slug}/languages") or {}
        total = sum(langs.values()) or 1
        for lg, b in sorted(langs.items(), key=lambda x: -x[1]):
            leng_rows.append({"repo_key": key, "lenguaje": lg, "bytes": b, "porcentaje": round(100 * b / total, 2)})
        repo_rows.append({
            "repo_key": key, "nombre_visible": visible, "tipo_repo": tipo, "visibilidad": visib,
            "lenguaje_principal": max(langs, key=langs.get) if langs else "",
            "commits": len(filas), "dias_activos": len({f["fecha"] for f in filas}),
            "primer_commit": filas[0]["fecha"], "ultimo_commit": filas[-1]["fecha"],
            "archivos": len(rutas), "archivos_prueba": sum(1 for p in rutas if RX_PRUEBA.search(p)),
            "workflows_ci": sum(1 for p in rutas if p.startswith(".github/workflows/")),
        })

    # calendario de contribuciones (incluye commits, incidencias, PR y revisiones; solo conteos)
    q = ("query($from:DateTime!,$to:DateTime!){viewer{contributionsCollection(from:$from,to:$to){"
         "contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}")
    contrib = []
    hoy = dt.date.today()
    for anio in range(a.desde, hoy.year + 1):
        d = gh(["api", "graphql", "-f", "query=" + q, "-f", f"from={anio}-01-01T00:00:00Z",
                "-f", f"to={min(dt.date(anio, 12, 31), hoy).isoformat()}T23:59:59Z"])
        try:
            semanas = d["data"]["viewer"]["contributionsCollection"]["contributionCalendar"]["weeks"]
        except Exception:
            continue
        for s in semanas:
            for dia in s["contributionDays"]:
                contrib.append({"fecha": dia["date"], "contribuciones": dia["contributionCount"]})

    def escribir(nombre, filas, columnas):
        with open(out / nombre, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=columnas, extrasaction="ignore")
            w.writeheader()
            w.writerows(filas)
        print(f"   {nombre}: {len(filas)} filas")

    escribir("fact_commits.csv", commits_rows, ["commit_id", "repo_key", "fecha", "hora_local", "fecha_hora_local", "tipo_cambio",
             "referencia_issue", "es_merge", "ia_coautor", "ia_herramienta", "archivos_cambiados", "lineas_anadidas",
             "lineas_eliminadas", "archivos_prueba_tocados"])
    asignar_colores(repo_rows)
    escribir("dim_repo.csv", repo_rows, ["repo_key", "nombre_visible", "tipo_repo", "visibilidad", "lenguaje_principal", "commits",
             "dias_activos", "primer_commit", "ultimo_commit", "archivos", "archivos_prueba", "workflows_ci", "color"])
    escribir("lenguajes_repo.csv", leng_rows, ["repo_key", "lenguaje", "bytes", "porcentaje"])
    escribir("contribuciones_dia.csv", contrib, ["fecha", "contribuciones"])
    informe_calidad(out, objetivos, commits_rows, repo_rows, contrib, totales, sin_det, hoy)
    print("Listo:", out)


if __name__ == "__main__":
    try:
        main()
    except LimiteTasa:
        sys.exit("Se alcanzó el límite de llamadas de GitHub (5.000 por hora). El detalle ya consultado quedó en la "
                 "caché: vuelve a ejecutar el mismo comando más tarde y continuará donde quedó.")
