"""Normalización común: fila de noticia, IDs estables, URLs, titulares, idiomas y fechas."""

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from faro_editorial.contrato import parse_fecha_utc

# Qué texto hay disponible para cada fila (data/CONTRATO.md). El artículo nunca se descarga.
ALCANCE_TITULAR = "titular_metadatos"
ALCANCE_DESCRIPCION = "titular_descripcion"  # RSS con <description> (decisión #35)
ALCANCE_TITULO_IMAGEN = "titulo_imagen_sitemap"  # image:title, no siempre es el titular
MAX_DESCRIPCION = 1000  # la descripción es solo para análisis interno; se recorta

PARAMETROS_SEGUIMIENTO = re.compile(r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid|ref|cmpid)$", re.I)

IDIOMAS = {
    "spanish": "es",
    "english": "en",
    "portuguese": "pt",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "chinese": "zh",
}

COLUMNAS_NOTICIAS = (
    "id_noticia",
    "titulo",
    "url",
    "medio",
    "idioma",
    "fecha_publicacion",
    "fecha_deteccion",
    "fecha_extraccion",
    "tema",
    "origen",
    "alcance_texto",
    "descripcion",
)

# Fecha ya convertida a UTC, texto que no se pudo interpretar (la carga lo rechazará con
# su motivo, como pide T01) o None si la fuente no la trae.
Fecha = datetime | str | None


@dataclass
class FilaNoticia:
    titulo: str
    url: str
    medio: str
    origen: str
    fuente: str  # id de la fuente en la configuración
    fecha_extraccion: datetime
    idioma: str | None = None
    fecha_publicacion: Fecha = None
    fecha_deteccion: Fecha = None
    alcance_texto: str = ALCANCE_TITULAR
    # Descripción del RSS: solo análisis interno (decisión #35). El paquete de entrega la quita.
    descripcion: str | None = None
    # tema queda vacío a propósito: lo asigna la clasificación (#10), nunca la consulta.

    @property
    def clave(self) -> str:
        return clave_url(self.url)

    @property
    def id_noticia(self) -> str:
        return id_noticia(self.url)

    @property
    def fecha_referencia(self) -> datetime | None:
        """Fecha para filtrar por ventana: publicación y, si falta, detección."""
        for fecha in (self.fecha_publicacion, self.fecha_deteccion):
            if isinstance(fecha, datetime):
                return fecha
        return None

    def a_csv(self) -> dict[str, str]:
        valores = {
            "id_noticia": self.id_noticia,
            "titulo": self.titulo,
            "url": self.url,
            "medio": self.medio,
            "idioma": self.idioma or "",
            "fecha_publicacion": formatear_fecha(self.fecha_publicacion),
            "fecha_deteccion": formatear_fecha(self.fecha_deteccion),
            "fecha_extraccion": formatear_fecha(self.fecha_extraccion),
            "tema": "",
            "origen": self.origen,
            "alcance_texto": self.alcance_texto,
            "descripcion": self.descripcion or "",
        }
        return {c: valores[c] for c in COLUMNAS_NOTICIAS}


def clave_url(url: str) -> str:
    """Clave de deduplicación: misma noticia aunque cambien http/https, www, el fragmento,
    la barra final o los parámetros de seguimiento. La URL original se guarda intacta."""
    partes = urlsplit(url.strip())
    host = (partes.hostname or "").lower().removeprefix("www.")
    ruta = partes.path.rstrip("/") or "/"
    consulta = [(k, v) for k, v in parse_qsl(partes.query) if not PARAMETROS_SEGUIMIENTO.match(k)]
    return urlunsplit(("https", host, ruta, urlencode(sorted(consulta)), ""))


def id_noticia(url: str) -> str:
    """ID estable con el formato de data/CONTRATO.md: prefijo del medio + hash de la URL
    (p. ej. tvn2-3f2a9c1b7e04). El prefijo sale del dominio y no del origen, para que el ID
    no cambie si la misma noticia llega primero por GDELT y luego por el RSS."""
    clave = clave_url(url)
    etiqueta = (urlsplit(clave).hostname or "").split(".")[0]
    prefijo = re.sub(r"[^a-z0-9]", "", etiqueta)[:12] or "n"
    return f"{prefijo}-{hashlib.sha256(clave.encode('utf-8')).hexdigest()[:12]}"


def dominio(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def limpiar_titulo(texto: str | None) -> str:
    """Decodifica entidades HTML, quita etiquetas sueltas y normaliza espacios."""
    if not texto:
        return ""
    texto = html.unescape(html.unescape(texto))  # algunos feeds codifican dos veces
    texto = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def limpiar_descripcion(texto: str | None) -> str | None:
    limpio = limpiar_titulo(texto)
    if not limpio:
        return None
    return limpio if len(limpio) <= MAX_DESCRIPCION else limpio[: MAX_DESCRIPCION - 1] + "…"


def normalizar_idioma(valor: str | None) -> str | None:
    if not valor or not valor.strip():
        return None
    valor = valor.strip()
    corto = valor.split("-")[0].split("_")[0].lower()
    if len(corto) == 2:
        return corto
    return IDIOMAS.get(valor.lower(), valor.lower())


DIA_SEMANA = re.compile(r"^[A-Za-zÁÉÍÓÚáéíóú]{2,10}\.?,\s*")


def leer_fecha(valor: str | None, formato: str | None = None, zona: str | None = None) -> Fecha:
    """Convierte a UTC; si el texto no es una fecha válida lo devuelve tal cual (la carga lo
    rechazará con su motivo, T01).

    Con `formato` (strptime) se interpreta primero con ese formato, con y sin el día de la
    semana inicial; si no trae zona se usa `zona` (o UTC). Así se leen feeds con fechas no
    estándar, como "Wed, 10/07/2026 - 00:00", sin adivinar si el mes va primero."""
    if valor is None or not valor.strip():
        return None
    texto = valor.strip()
    if formato:
        for candidato in (texto, DIA_SEMANA.sub("", texto)):
            try:
                fecha = datetime.strptime(candidato, formato)
            except ValueError:
                continue
            if fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=ZoneInfo(zona) if zona else UTC)
            return fecha.astimezone(UTC)
    try:
        return parse_fecha_utc(texto)
    except ValueError:
        return texto


def formatear_fecha(fecha: Fecha) -> str:
    if fecha is None:
        return ""
    if isinstance(fecha, datetime):
        return fecha.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return fecha
