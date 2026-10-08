"""Cliente HTTP común a todas las fuentes de la extracción.

- Se identifica con un User-Agent propio y respeta robots.txt (incluido Crawl-delay) en
  las fuentes web. Las APIs documentadas (GDELT, Banco Mundial, USGS) no usan robots.txt.
- Mantiene una pausa mínima entre llamadas al mismo host.
- Reintenta ante 429, errores 5xx y fallos de red, con espera creciente (o Retry-After).
- Guarda cada respuesta exitosa cruda en <salida>/_respuestas/<fuente>/ y registra cada
  consulta (exitosa o fallida) para el manifest.
"""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.robotparser import RobotFileParser

import httpx

ESTADOS_REINTENTABLES = {429, 500, 502, 503, 504}
MAX_ESPERA_S = 60.0


class ErrorExtraccion(Exception):
    """La consulta no se pudo completar; el motivo queda en el registro de consultas."""


class BloqueadoPorRobots(ErrorExtraccion):
    """robots.txt no permite la URL o no se pudo leer (se asume prohibido)."""


@dataclass
class Respuesta:
    url: str
    estado: int
    contenido: bytes
    tipo_contenido: str
    fecha_utc: datetime  # momento en que llegó la respuesta: es la fecha de extracción
    sha256: str
    archivo: str | None  # ruta relativa del crudo guardado, para el manifest

    @property
    def texto(self) -> str:
        return self.contenido.decode("utf-8", errors="replace")

    def json(self) -> Any:
        # strict=False tolera caracteres de control dentro de cadenas (GDELT los envía a veces).
        return json.loads(self.texto, strict=False)


def _extension(tipo_contenido: str) -> str:
    tipo = tipo_contenido.lower()
    for marca, ext in (("json", "json"), ("xml", "xml"), ("rss", "xml"), ("html", "html")):
        if marca in tipo:
            return ext
    return "txt" if tipo.startswith("text/") else "bin"


class ClienteHTTP:
    def __init__(
        self,
        user_agent: str,
        dir_respuestas: Path,
        timeout_s: float = 30.0,
        pausa_s: float = 1.0,
        reintentos: int = 2,
        max_bytes: int = 25_000_000,
        http: httpx.Client | None = None,
        dormir: Callable[[float], None] = time.sleep,
        reloj: Callable[[], float] = time.monotonic,
        ahora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.user_agent = user_agent
        self.agente_robots = user_agent.split("/", 1)[0].strip().lower()
        self.dir_respuestas = Path(dir_respuestas)
        self.pausa_s = pausa_s
        self.reintentos = reintentos
        self.max_bytes = max_bytes
        self._http = http or httpx.Client(timeout=timeout_s, follow_redirects=True)
        self._dormir = dormir
        self._reloj = reloj
        self._ahora = ahora
        self._pausas: dict[str, float] = {}
        self._ultimo: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | str] = {}
        self.consultas: list[dict[str, Any]] = []
        self.robots: dict[str, str] = {}  # host -> qué se aplicó, para el manifest

    def fijar_pausa(self, host: str, segundos: float) -> None:
        self._pausas[host] = max(segundos, self._pausas.get(host, 0.0))

    # --- API pública ------------------------------------------------------------------

    def get(
        self,
        url: str,
        fuente: str,
        params: dict[str, Any] | None = None,
        respetar_robots: bool = True,
    ) -> Respuesta:
        completa = str(httpx.URL(url, params=params) if params else httpx.URL(url))
        registro: dict[str, Any] = {"fuente": fuente, "url": completa}
        try:
            if respetar_robots:
                self._verificar_robots(completa)
            respuesta, intentos = self._solicitar(completa)
        except ErrorExtraccion as e:
            self._registrar(registro, error=str(e))
            raise
        registro["intentos"] = intentos
        if respuesta.status_code != 200:
            error = f"HTTP {respuesta.status_code}: {respuesta.text[:200]}"
            self._registrar(registro, estado=respuesta.status_code, error=error)
            raise ErrorExtraccion(error)
        contenido = respuesta.content
        if len(contenido) > self.max_bytes:
            error = f"respuesta de {len(contenido)} bytes supera el máximo de {self.max_bytes}"
            self._registrar(registro, estado=200, error=error)
            raise ErrorExtraccion(error)

        fecha = self._ahora()
        sha = hashlib.sha256(contenido).hexdigest()
        tipo = respuesta.headers.get("content-type", "")
        relativa = f"{fuente}/{fecha:%Y%m%dT%H%M%S%f}Z_{sha[:10]}.{_extension(tipo)}"
        ruta = self.dir_respuestas / relativa
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(contenido)
        archivo = f"{self.dir_respuestas.name}/{relativa}"
        self._registrar(
            registro, estado=200, bytes=len(contenido), sha256=sha, archivo=archivo, fecha=fecha
        )
        return Respuesta(completa, 200, contenido, tipo, fecha, sha, archivo)

    # --- Internos ---------------------------------------------------------------------

    def _registrar(self, registro: dict[str, Any], fecha: datetime | None = None, **campos) -> None:
        fecha = fecha or self._ahora()
        registro.update(campos)
        registro["fecha_utc"] = fecha.isoformat(timespec="seconds").replace("+00:00", "Z")
        self.consultas.append(registro)

    def _esperar_turno(self, host: str) -> None:
        anterior = self._ultimo.get(host)
        if anterior is None:
            return
        falta = self._pausas.get(host, self.pausa_s) - (self._reloj() - anterior)
        if falta > 0:
            self._dormir(falta)

    def _solicitar(self, url: str) -> tuple[httpx.Response, int]:
        host = httpx.URL(url).host
        cabeceras = {"User-Agent": self.user_agent}
        for intento in range(self.reintentos + 1):
            self._esperar_turno(host)
            try:
                respuesta = self._http.get(url, headers=cabeceras)
            except httpx.TransportError as e:
                self._ultimo[host] = self._reloj()
                if intento < self.reintentos:
                    self._dormir(min(2.0 * 2**intento, MAX_ESPERA_S))
                    continue
                raise ErrorExtraccion(f"fallo de red: {type(e).__name__}: {e}") from e
            self._ultimo[host] = self._reloj()
            if respuesta.status_code in ESTADOS_REINTENTABLES and intento < self.reintentos:
                self._dormir(self._espera(respuesta, intento))
                continue
            return respuesta, intento + 1
        raise AssertionError("inalcanzable")  # pragma: no cover

    @staticmethod
    def _espera(respuesta: httpx.Response, intento: int) -> float:
        try:
            return min(float(respuesta.headers.get("retry-after", "")), MAX_ESPERA_S)
        except ValueError:
            return min(2.0 * 2**intento, MAX_ESPERA_S)

    def _verificar_robots(self, url: str) -> None:
        partes = httpx.URL(url)
        clave = f"{partes.scheme}://{partes.host}"
        if clave not in self._robots:
            self._robots[clave] = self._leer_robots(clave)
        robots = self._robots[clave]
        if isinstance(robots, str):
            raise BloqueadoPorRobots(robots)
        if not robots.can_fetch(self.agente_robots, url):
            raise BloqueadoPorRobots(f"robots.txt de {partes.host} no permite {partes.path}")

    def _leer_robots(self, base: str) -> RobotFileParser | str:
        """Devuelve el parser, o un texto con el motivo si se debe asumir prohibición.
        2xx: se aplican las reglas. 401/403: todo prohibido. Otro 4xx: no hay reglas.
        5xx o fallo de red: no se sabe qué permite, así que se asume prohibido."""
        url = f"{base}/robots.txt"
        host = httpx.URL(base).host
        try:
            respuesta, _ = self._solicitar(url)
        except ErrorExtraccion as e:
            motivo = f"no se pudo leer {url} ({e}); se asume prohibido"
            self.robots[host] = motivo
            return motivo
        parser = RobotFileParser(url)
        if respuesta.status_code in (401, 403):
            parser.disallow_all = True
            self.robots[host] = f"HTTP {respuesta.status_code}: todo prohibido"
        elif 400 <= respuesta.status_code < 500:
            parser.allow_all = True
            self.robots[host] = f"HTTP {respuesta.status_code}: sin reglas"
        elif respuesta.status_code >= 300:
            motivo = f"{url} respondió HTTP {respuesta.status_code}; se asume prohibido"
            self.robots[host] = motivo
            return motivo
        else:
            parser.parse(respuesta.text.splitlines())
            demora = parser.crawl_delay(self.agente_robots)
            if demora:
                self.fijar_pausa(host, float(demora))
            self.robots[host] = "reglas aplicadas" + (f"; Crawl-delay {demora}" if demora else "")
        return parser
