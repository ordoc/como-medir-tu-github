"""Genera datos SINTÉTICOS con el mismo esquema que produce extraer_actividad_github.py.

Sirve para explorar el informe de Power BI sin usar la actividad real de nadie: los repositorios, las
fechas, las horas y las cifras son inventados (semilla fija, así que siempre salen los mismos).
No representan a ninguna persona ni a ningún proyecto real.

Uso:
  python generar_datos_sinteticos.py                  # escribe en ./Datos
  python generar_datos_sinteticos.py --salida otra\\carpeta
"""
import argparse
import csv
import datetime as dt
import random
from collections import Counter, defaultdict
from pathlib import Path

from extraer_actividad_github import asignar_colores  # misma paleta que el extractor real

SEMILLA = 20261114
FIN_GLOBAL = dt.date(2026, 11, 13)   # "hoy" de los datos de ejemplo
INICIO_CALENDARIO = dt.date(2024, 1, 1)

TIPOS_BASE = {"Funcionalidad": 0.34, "Corrección": 0.22, "Pruebas": 0.08, "Documentación": 0.09,
              "Mantenimiento": 0.15, "Otro": 0.12}

# Perfiles horarios (peso relativo de cada hora 0-23)
HORAS = {
    "oficina": [1, .5, .3, .2, .3, .5, 1, 2, 5, 8, 9, 9, 6, 5, 8, 9, 9, 7, 4, 3, 2.5, 2, 1.5, 1.2],
    "noche":   [4, 3, 2, 1, .5, .3, .3, .5, 1, 1.5, 2, 2, 2, 2, 2, 2, 2, 3, 4, 6, 8, 9, 8, 6],
}
HORAS["mixto"] = [0.7 * a + 0.3 * b for a, b in zip(HORAS["oficina"], HORAS["noche"])]

# Repositorios ficticios con perfiles distintos. `rafagas`: (inicio, fin, multiplicador de probabilidad diaria).
PROYECTOS = [
    dict(key="TiendaDemo", tipo="Aplicación propia (privada)", vis="Privado", leng="TypeScript",
         otros=["CSS", "HTML"], ini="2024-02-05", fin="2024-09-27", p=0.50, media=3.4, horas="oficina", finde=0.2,
         pruebas=0.55, ia_desde=None, ia=0.0, issues=0.20, ci=2, archivos=(260, 420)),
    dict(key="InventarioAPI", tipo="Aplicación propia (privada)", vis="Privado", leng="Python",
         otros=["Dockerfile", "Shell"], ini="2024-06-10", fin="2025-04-18", p=0.12, media=3.0, horas="mixto", finde=0.35,
         pruebas=0.40, ia_desde=None, ia=0.0, issues=0.10, ci=1, archivos=(120, 220),
         rafagas=[("2024-08-12", "2024-08-30", 5.0), ("2025-02-03", "2025-02-14", 5.5)]),
    dict(key="GastosApp", tipo="Aplicación propia (privada)", vis="Privado", leng="Dart",
         otros=["Kotlin", "Swift"], ini="2025-01-13", fin="2026-10-30", p=0.28, media=2.1, horas="mixto", finde=0.45,
         pruebas=0.25, ia_desde="2025-09-01", ia=0.35, issues=0.05, ci=1, archivos=(300, 520)),
    dict(key="BlogTecnico", tipo="Portafolio (público)", vis="Público", leng="JavaScript",
         otros=["HTML", "CSS"], ini="2025-03-03", fin="2026-10-10", p=0.11, media=1.8, horas="oficina", finde=0.5,
         pruebas=0.0, ia_desde="2026-01-01", ia=0.20, issues=0.0, ci=0, archivos=(35, 70),
         tipos={"Funcionalidad": 0.25, "Documentación": 0.32, "Mantenimiento": 0.2, "Corrección": 0.1, "Otro": 0.13}),
    dict(key="NotasDatos", tipo="Portafolio (público)", vis="Público", leng="Jupyter Notebook",
         otros=["Python", "SQL"], ini="2025-05-12", fin="2026-08-28", p=0.17, media=2.2, horas="noche", finde=1.8,
         pruebas=0.0, ia_desde=None, ia=0.0, issues=0.0, ci=0, archivos=(40, 90),
         tipos={"Funcionalidad": 0.3, "Documentación": 0.25, "Mantenimiento": 0.15, "Corrección": 0.1, "Otro": 0.2}),
    dict(key="CursoSQL", tipo="Portafolio (público)", vis="Público", leng="SQL",
         otros=["Python"], ini="2024-10-01", fin="2025-01-31", p=0.33, media=2.6, horas="noche", finde=1.2,
         pruebas=0.0, ia_desde=None, ia=0.0, issues=0.0, ci=0, archivos=(25, 55),
         tipos={"Funcionalidad": 0.4, "Documentación": 0.2, "Corrección": 0.15, "Mantenimiento": 0.1, "Otro": 0.15}),
    dict(key="ChatbotSoporte", tipo="Aplicación propia (privada)", vis="Privado", leng="Python",
         otros=["TypeScript", "Dockerfile"], ini="2026-03-02", fin="2026-10-23", p=0.45, media=3.8, horas="mixto", finde=0.6,
         pruebas=0.35, ia_desde="2026-03-02", ia=0.62, issues=0.15, ci=2, archivos=(180, 340),
         rafagas=[("2026-06-01", "2026-06-19", 1.8), ("2026-10-05", "2026-10-23", 1.7)]),
    dict(key="cliente-1", nombre="Proyecto de cliente (consultoría)", tipo="Proyecto de cliente (anónimo)",
         vis="Privado de terceros", leng="CSS", otros=["JavaScript", "PHP"], ini="2024-04-01", fin="2026-05-29",
         p=0.13, media=1.5, horas="oficina", finde=0.15, pruebas=0.0, ia_desde=None, ia=0.0, issues=0.03, ci=0,
         archivos=(520, 780),
         tipos={"Corrección": 0.38, "Funcionalidad": 0.2, "Mantenimiento": 0.14, "Documentación": 0.03, "Otro": 0.25}),
]


def elegir(rng, pesos):
    return rng.choices(list(pesos), weights=list(pesos.values()))[0]


def generar_proyecto(p, rng):
    ini, fin = dt.date.fromisoformat(p["ini"]), dt.date.fromisoformat(p["fin"])
    rafagas = [(dt.date.fromisoformat(a), dt.date.fromisoformat(b), m) for a, b, m in p.get("rafagas", [])]
    tipos = p.get("tipos", TIPOS_BASE)
    ia_desde = dt.date.fromisoformat(p["ia_desde"]) if p.get("ia_desde") else None
    filas = []
    dia = ini
    while dia <= fin:
        prob = p["p"] * (p["finde"] if dia.weekday() >= 5 else 1.0)
        for a, b, m in rafagas:
            if a <= dia <= b:
                prob *= m
        if rng.random() < min(prob, 0.95):
            n = 1 + min(int(rng.expovariate(1 / max(p["media"] - 1, 0.1))), 22)
            for _ in range(n):
                hora = rng.choices(range(24), weights=HORAS[p["horas"]])[0]
                t = dt.datetime(dia.year, dia.month, dia.day, hora, rng.randint(0, 59), rng.randint(0, 59))
                tipo = elegir(rng, tipos)
                archivos = max(1, int(rng.lognormvariate(1.0, 0.8)))
                anadidas = max(1, int(rng.lognormvariate(3.6 if tipo != "Documentación" else 3.0, 1.1)))
                eliminadas = int(anadidas * rng.uniform(0.05, 0.7))
                if tipo == "Pruebas":
                    pruebas = min(archivos, 1 + int(rng.expovariate(1.0))) if p["pruebas"] > 0 else 0
                elif tipo in ("Funcionalidad", "Corrección") and rng.random() < p["pruebas"]:
                    pruebas = min(archivos, rng.randint(1, 3))
                else:
                    pruebas = 0
                con_ia = ia_desde is not None and dia >= ia_desde and rng.random() < p["ia"]
                filas.append({
                    "repo_key": p["key"], "_t": t, "fecha": dia.isoformat(), "hora_local": hora,
                    "fecha_hora_local": t.strftime("%Y-%m-%d %H:%M:%S"), "tipo_cambio": tipo,
                    "referencia_issue": 1 if rng.random() < p["issues"] else 0,
                    "es_merge": 1 if rng.random() < 0.04 else 0,
                    "ia_coautor": 1 if con_ia else 0,
                    "ia_herramienta": ("Claude" if rng.random() < 0.85 else "Copilot") if con_ia else "Ninguna",
                    "archivos_cambiados": archivos, "lineas_anadidas": anadidas, "lineas_eliminadas": eliminadas,
                    "archivos_prueba_tocados": pruebas,
                })
        dia += dt.timedelta(days=1)
    filas.sort(key=lambda r: r["_t"])
    for i, f in enumerate(filas, 1):
        f["commit_id"] = f"{p['key']}-{i:04d}"
    return filas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default=str(Path(__file__).parent / "Datos"))
    out = Path(ap.parse_args().salida)
    out.mkdir(parents=True, exist_ok=True)

    commits, repos, lenguajes = [], [], []
    for i, p in enumerate(PROYECTOS):
        rng = random.Random(SEMILLA + i)
        filas = generar_proyecto(p, rng)
        commits += filas
        a, b = p["archivos"]
        archivos = rng.randint(a, b)
        repos.append({
            "repo_key": p["key"], "nombre_visible": p.get("nombre", p["key"]), "tipo_repo": p["tipo"],
            "visibilidad": p["vis"], "lenguaje_principal": p["leng"], "commits": len(filas),
            "dias_activos": len({f["fecha"] for f in filas}),
            "primer_commit": filas[0]["fecha"], "ultimo_commit": filas[-1]["fecha"],
            "archivos": archivos,
            "archivos_prueba": int(archivos * rng.uniform(0.08, 0.2)) if p["pruebas"] > 0 else 0,
            "workflows_ci": p["ci"],
            "origen": "sintetico",
        })
        bytes_ = {p["leng"]: rng.randint(60_000, 400_000)}
        for otro in p["otros"]:
            bytes_[otro] = int(bytes_[p["leng"]] * rng.uniform(0.04, 0.5))
        total = sum(bytes_.values())
        for lg, b_ in sorted(bytes_.items(), key=lambda x: -x[1]):
            lenguajes.append({"repo_key": p["key"], "lenguaje": lg, "bytes": b_, "porcentaje": round(100 * b_ / total, 2)})
    asignar_colores(repos)

    # calendario de contribuciones: commits del dia + otras contribuciones (incidencias, revisiones) inventadas
    rng = random.Random(SEMILLA + 99)
    por_dia = Counter(f["fecha"] for f in commits)
    contrib, dia = [], INICIO_CALENDARIO
    while dia <= FIN_GLOBAL:
        n = por_dia.get(dia.isoformat(), 0)
        if n and rng.random() < 0.12:
            n += rng.randint(1, 3)
        elif not n and rng.random() < 0.03:
            n = rng.randint(1, 2)
        contrib.append({"fecha": dia.isoformat(), "contribuciones": n})
        dia += dt.timedelta(days=1)

    def escribir(nombre, filas, columnas):
        with open(out / nombre, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=columnas, extrasaction="ignore")
            w.writeheader()
            w.writerows(filas)
        print(f"   {nombre}: {len(filas)} filas")

    escribir("fact_commits.csv", commits, ["commit_id", "repo_key", "fecha", "hora_local", "fecha_hora_local", "tipo_cambio",
             "referencia_issue", "es_merge", "ia_coautor", "ia_herramienta", "archivos_cambiados", "lineas_anadidas",
             "lineas_eliminadas", "archivos_prueba_tocados"])
    escribir("dim_repo.csv", repos, ["repo_key", "nombre_visible", "tipo_repo", "visibilidad", "lenguaje_principal", "commits",
             "dias_activos", "primer_commit", "ultimo_commit", "archivos", "archivos_prueba", "workflows_ci", "color", "origen"])
    escribir("lenguajes_repo.csv", lenguajes, ["repo_key", "lenguaje", "bytes", "porcentaje"])
    escribir("contribuciones_dia.csv", contrib, ["fecha", "contribuciones"])
    print("Listo (datos sintéticos):", out)


if __name__ == "__main__":
    main()
