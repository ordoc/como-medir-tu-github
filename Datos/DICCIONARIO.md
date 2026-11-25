# Diccionario de datos · Actividad de GitHub

Estos archivos se generan con `extraer_actividad_github.py` (solo lectura, vía `gh api`) o, para explorar el informe sin usar datos reales, con `generar_datos_sinteticos.py`. **Los que trae este repositorio son sintéticos:** los repositorios, fechas, horas y cifras son inventados y no representan a ninguna persona ni proyecto real.

Codificación UTF-8 con BOM, separador coma, fechas `AAAA-MM-DD`. La hora local se calcula con `--utc-offset` (por defecto UTC-5).

**Privacidad.** Los archivos contienen solo conteos, fechas, horas y clasificaciones. No hay mensajes de commits, nombres de archivos, correos ni nombres de personas. Un repositorio de terceros se incluye con un alias (`--anonimo propietario/repositorio=alias`) y su nombre real nunca se escribe en la salida.

## Tablas

### `dim_repo.csv` · una fila por repositorio
| Columna | Descripción |
|---|---|
| `repo_key` | Clave del repositorio (para relacionar). |
| `nombre_visible` | Nombre para mostrar en los gráficos. |
| `tipo_repo` | `Aplicación propia (privada)`, `Portafolio (público)` o `Proyecto de cliente (anónimo)`. |
| `visibilidad` | `Privado`, `Público` o `Privado de terceros`. |
| `lenguaje_principal` | Lenguaje con más bytes según GitHub. |
| `commits`, `dias_activos` | Conteos calculados sobre `fact_commits`. |
| `primer_commit`, `ultimo_commit` | Fechas locales. |
| `archivos`, `archivos_prueba`, `workflows_ci` | Archivos del repositorio, los que son pruebas (`.test.`, `.spec.`, `e2e/`, `tests/`…) y flujos de CI en `.github/workflows/`. |
| `color` | Color del proyecto (`#RRGGBB`). Lo asigna el extractor por orden alfabético del nombre visible, con una paleta de 12 colores; el informe lo usa en todos los gráficos por proyecto. |

### `fact_commits.csv` · una fila por commit
| Columna | Descripción |
|---|---|
| `commit_id` | Identificador secuencial por repositorio (`NombreRepo-0001`…), en orden cronológico. |
| `repo_key` | Relación con `dim_repo`. |
| `fecha` | Fecha local. Relación con el calendario. |
| `hora_local` | Hora del día, 0 a 23. Relación con la tabla de horas. |
| `fecha_hora_local` | Fecha y hora completas. |
| `tipo_cambio` | `Funcionalidad`, `Corrección`, `Pruebas`, `Documentación`, `Mantenimiento` u `Otro`. **Clasificación aproximada** por palabras del título del commit. |
| `referencia_issue` | 1 si el título cita una incidencia (`#123`). |
| `es_merge` | 1 si el commit une dos ramas. |
| `ia_coautor` | 1 si el commit declara una IA como coautora (`Co-Authored-By`). |
| `ia_herramienta` | Herramienta declarada (`Claude`, `Gemini`, `ChatGPT`, `Copilot`) o `Ninguna`. |
| `archivos_cambiados`, `lineas_anadidas`, `lineas_eliminadas` | Tamaño del cambio. Las líneas no cuentan archivos generados o que no son código (datos, PDF, imágenes, `.pbix`, dependencias, compilados). |
| `archivos_prueba_tocados` | Cuántos de esos archivos son de prueba. |

### `lenguajes_repo.csv` · lenguajes por repositorio
`repo_key`, `lenguaje`, `bytes`, `porcentaje`.

### `contribuciones_dia.csv` · calendario de contribuciones
`fecha`, `contribuciones`. Es el mismo conteo que muestra el gráfico verde de GitHub: incluye commits, incidencias, solicitudes de cambio y revisiones.

### `calidad_extraccion.csv` · solo lo genera el extractor real
Una fila por verificación: `verificacion`, `repo_key`, `esperado`, `obtenido`, `resultado` (`OK`, `REVISAR` o `INFO`). Compara, por ejemplo, los commits listados con el total que reporta GitHub, comprueba ids únicos y fechas válidas. Solo contiene conteos.

## Modelo en Power BI
- `dim_repo[repo_key]` (1) → (*) `fact_commits[repo_key]` y `lenguajes_repo[repo_key]`.
- `Calendario` (consulta de Power Query) → `fact_commits[fecha]` y `contribuciones_dia[fecha]`.
- `Dim_Hora` (horas 0 a 23) → `fact_commits[hora_local]`, para que el mapa de calor mantenga las 24 columnas.
- `_Medidas`: tabla sin datos que reúne las 48 medidas DAX, organizadas en carpetas (Volumen, Ritmo, Calidad, IA, Visual y Títulos). Está en `Como medir tu Github.SemanticModel/definition/tables/_Medidas.tmdl`.
- El parámetro `RutaDatos` apunta a la carpeta con los CSV.

## Límites que conviene tener presentes
1. **Los commits no miden productividad.** Un día de trabajo puede ser un commit grande o diez pequeños.
2. **`ia_coautor` es un piso.** Solo cuenta los commits donde la herramienta dejó su firma. No significa que ese porcentaje del código lo escribió una IA, y muchas herramientas no dejan firma.
3. **`tipo_cambio` es una inferencia** a partir del título. La categoría `Otro` agrupa los que no encajan en ninguna regla.
4. **Las líneas pueden estar infladas** por archivos generados. Para comparar, usa la mediana o los archivos cambiados, y no la suma.
5. **Los repositorios nuevos** tienen pocos commits y sus porcentajes no son representativos (el informe deja en blanco los de menos de 10 commits).
