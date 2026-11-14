"""F1b-2 de SDD_fuentes_datos_gratuitas.md: endurecimientos independientes de EDGAR.

CS-19 (Finnhub: token por header, sin fuga en errores/repr, key validada al arrancar),
CS-22 (sin emails reales en el repo), CS-23 (UA de la SEC validado solo en `bot.main()`),
CS-26 (`.env.example` sin secretos) y CS-29 (URL del 8-K con datos de red validados).

Sin red real (`httpx.MockTransport`; NetworkGuard activo) y sin tiempo real. Nombres
`test_fdg_tcs<n>_*` / `test_fdg_ca25_*` (meta-test de trazabilidad de la spec). Este
archivo solo contiene emails de la lista blanca (example.com / ejemplo.com); los
emails "de forma real" de los controles se arman por concatenación.
"""

from __future__ import annotations

import ast
import dataclasses
import logging
import re
import traceback
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from investbot import bot, corporate_events, finnhub_client, query_handler, summary

from .test_query_handler import _make_clients, _no_call_handler

pytestmark = pytest.mark.filterwarnings("ignore::telegram.warnings.PTBUserWarning")

REPO = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO / ".env.example"
README = REPO / "README.md"
PEERS_SPEC = REPO / "contexto/specs/abiertas/SDD_peers_dinamicos_y_eventos_corporativos.md"

def _at(local: str, dominio: str) -> str:
    """Arma un email por concatenación: el archivo no contiene emails fuera de la lista blanca."""
    return local + "@" + dominio


UA_VALIDO = "InvestBot ana@ejemplo.test"  # pasa la validación estricta (CS-23)
UA_PLACEHOLDER = "InvestBot contacto@ejemplo.com"
CANARY_FINNHUB = "CANARY-FINNHUB-KEY-0123456789"


# ---------------------------------------------------------------------------
# Helpers de `main()` (misma forma que tests/test_bot.py, sin importarlos)
# ---------------------------------------------------------------------------


def _env_main(monkeypatch, tmp_path, name="f1b2.db", **extra):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:dummy-token-for-tests")
    monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")
    for var in (
        "FRED_API_KEY", "FINNHUB_API_KEY", "SEC_EDGAR_USER_AGENT", "GEMINI_API_KEY",
        "GEMINI_REWRITE_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("INVESTBOT_DB_PATH", str(tmp_path / name))
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


def _spy_main(monkeypatch):
    """Espía `build_application` y los `Clients` construidos; no hace polling."""
    from telegram.ext import Application

    captured: dict = {"build": None, "clients": None}
    real_build = bot.build_application
    real_clients = query_handler.Clients

    def spy_build(**kwargs):
        captured["build"] = kwargs
        return real_build(**kwargs)

    def spy_clients(*args, **kwargs):
        captured["clients"] = real_clients(*args, **kwargs)
        return captured["clients"]

    monkeypatch.setattr(bot, "build_application", spy_build)
    monkeypatch.setattr(bot.query_handler, "Clients", spy_clients)
    monkeypatch.setattr(Application, "run_polling", lambda self, **kw: None)
    return captured


def _errores(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


# ---------------------------------------------------------------------------
# T-CS-5 (CS-7/CS-23): UA inválido deshabilita EDGAR con ERROR sin el valor y
# la cadena sigue con FMP
# ---------------------------------------------------------------------------

_UA_INVALIDOS = [
    "CanaryApp canary@example.com",
    "CanaryApp/1.0 ana@ejemplo.test",
    "CanaryApp " + _at("tu-correo-canary", "ejemplo.test"),
    "CanaryApp (ana@ejemplo.test)",
    "canary-sin-email-ni-app",
]


@pytest.mark.parametrize("ua", _UA_INVALIDOS)
def test_fdg_tcs5_ua_invalido_deshabilita_edgar_con_error_sin_valor(monkeypatch, tmp_path, caplog, ua):
    _env_main(monkeypatch, tmp_path, SEC_EDGAR_USER_AGENT=ua)
    caplog.set_level(logging.INFO)
    captured = _spy_main(monkeypatch)
    bot.main()
    assert captured["build"]["sec_edgar_user_agent"] is None
    assert captured["clients"].sec_edgar_user_agent is None
    errores = _errores(caplog)
    assert len(errores) == 1 and "SEC_EDGAR_USER_AGENT" in errores[0].getMessage()
    assert ua not in caplog.text and "canary" not in caplog.text.lower()


def test_fdg_tcs5_ua_valido_pasa_intacto_y_sin_error(monkeypatch, tmp_path, caplog):
    _env_main(monkeypatch, tmp_path, SEC_EDGAR_USER_AGENT=UA_VALIDO)
    caplog.set_level(logging.INFO)
    captured = _spy_main(monkeypatch)
    bot.main()
    assert captured["build"]["sec_edgar_user_agent"] == UA_VALIDO
    assert not _errores(caplog)


@pytest.mark.parametrize("valor", [None, ""])
def test_fdg_tcs5_ua_ausente_o_vacio_no_loguea_error(monkeypatch, tmp_path, caplog, valor):
    _env_main(monkeypatch, tmp_path)
    if valor is not None:
        monkeypatch.setenv("SEC_EDGAR_USER_AGENT", valor)
    caplog.set_level(logging.INFO)
    captured = _spy_main(monkeypatch)
    bot.main()
    assert not captured["build"]["sec_edgar_user_agent"]
    assert not _errores(caplog)


async def test_fdg_tcs5_con_edgar_deshabilitado_la_cadena_sigue_con_fmp(monkeypatch, tmp_path, adobe_fixtures):
    _env_main(monkeypatch, tmp_path, SEC_EDGAR_USER_AGENT="CanaryApp canary@example.com")
    captured = _spy_main(monkeypatch)
    bot.main()
    desde_main = captured["clients"]
    assert desde_main.sec_edgar_user_agent is None and desde_main.fundamentals is None
    # Mismos clientes FMP de la fixture, con el UA tal como lo dejó `main()`:
    # el análisis sale completo desde FMP y `sec.gov` no recibe ni una petición.
    clients = dataclasses.replace(
        _make_clients(adobe_fixtures),
        sec_edgar_http=httpx.AsyncClient(transport=httpx.MockTransport(_no_call_handler)),
        sec_edgar_user_agent=desde_main.sec_edgar_user_agent,
    )
    text = await query_handler.fetch_and_analyze("ADBE", clients, perfil="moderado")
    assert "Adobe" in text and "Eventos corporativos" not in text


# ---------------------------------------------------------------------------
# T-CS-23 (CS-23): el ejemplo literal de `.env.example` y del README no pasa la
# validación; la validación vive solo en `bot.main()`
# ---------------------------------------------------------------------------


def _ejemplo_literal_env_example() -> str:
    m = re.search(r'^#\s*Ejemplo literal: "([^"]+)"\s*$', ENV_EXAMPLE.read_text(encoding="utf-8"), re.M)
    assert m, "falta la línea de comentario con el ejemplo literal en .env.example"
    return m.group(1)


def _ejemplo_literal_readme() -> str:
    m = re.search(r'Ejemplo literal\s*\n?\s*\(placeholder[^)]*\):\s*`"([^"]+)"`', README.read_text(encoding="utf-8"))
    assert m, "falta el ejemplo literal de UA en el README"
    return m.group(1)


@pytest.mark.parametrize("fuente", [_ejemplo_literal_env_example, _ejemplo_literal_readme])
def test_fdg_tcs23_ejemplo_literal_de_la_documentacion_deshabilita_edgar(
    monkeypatch, tmp_path, caplog, fuente
):
    ejemplo = fuente()
    assert ejemplo == UA_PLACEHOLDER
    assert not bot._valid_sec_user_agent(ejemplo)
    _env_main(monkeypatch, tmp_path, SEC_EDGAR_USER_AGENT=ejemplo)
    caplog.set_level(logging.INFO)
    captured = _spy_main(monkeypatch)
    bot.main()
    assert captured["build"]["sec_edgar_user_agent"] is None
    assert len(_errores(caplog)) == 1 and ejemplo not in caplog.text


_UA_RECHAZADOS = [
    "InvestBot test@example.com",
    "InvestBot TEST@EXAMPLE.COM",
    "InvestBot " + _at("a", "sub.example.com"),
    "InvestBot " + _at("a", "foo.example.org"),
    "InvestBot contacto@ejemplo.com",
    "InvestBot a@x.localhost",
    "InvestBot " + _at("tu-correo", "ejemplo.test"),
    "InvestBot " + _at("TU-CORREO", "ejemplo.test"),
    "InvestBot/1.0 ana@ejemplo.test",
    "InvestBot (ana@ejemplo.test)",
    "ana@ejemplo.test",
    "InvestBot ana@ejemplo.test\n",
    "InvestBot\r\nX-Evil: 1 ana@ejemplo.test",
    "InvestBot ñ@ejemplo.test",
    "Invést ana@ejemplo.test",
    "InvestBot ana@gmail",
]


@pytest.mark.parametrize("ua", _UA_RECHAZADOS)
def test_fdg_tcs23_validador_rechaza(ua):
    assert not bot._valid_sec_user_agent(ua)


@pytest.mark.parametrize("ua", [UA_VALIDO, "Mi-App_1.0 " + _at("ana.lopez+sec", "midominio.org"), "A B C " + _at("d", "e.co")])
def test_fdg_tcs23_validador_acepta(ua):
    assert bot._valid_sec_user_agent(ua)


def test_fdg_tcs23_clients_y_build_application_no_validan_el_ua(monkeypatch, tmp_path):
    ua = "InvestBot test@example.com"  # lo usan los tests existentes: no debe cambiar
    clients = query_handler.Clients(
        fmp_http=None, fred_http=None, treasury_gov_http=None,
        fmp_api_key="k", fred_api_key=None, sec_edgar_user_agent=ua,
    )
    assert clients.sec_edgar_user_agent == ua
    captured: dict = {}
    real_clients = query_handler.Clients

    def spy_clients(*args, **kwargs):
        captured["c"] = real_clients(*args, **kwargs)
        return captured["c"]

    monkeypatch.setattr(bot.query_handler, "Clients", spy_clients)
    app = bot.build_application(
        telegram_token="123456:dummy-token-for-tests",
        allowed_chat_ids=frozenset({12345}),
        db_path=str(tmp_path / "f1b2_build.db"),
        fmp_api_key="test-fmp-key",
        fred_api_key=None,
        sec_edgar_user_agent=ua,
    )
    assert app is not None
    assert captured["c"].sec_edgar_user_agent == ua  # `build_application` no valida (CS-23)


def test_fdg_tcs23_env_example_documenta_el_formato_sin_barra_ni_parentesis():
    texto = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "<app> <email>" in texto and "sin barra ni paréntesis" in texto
    assert "alias dedicado" in texto


def test_fdg_f1b2_bot_usa_build_sec_http_client_para_sec_gov(monkeypatch, tmp_path):
    construidos = []
    real = bot.sec_edgar_client.build_sec_http_client

    def spy():
        client = real()
        construidos.append(client)
        return client

    monkeypatch.setattr(bot.sec_edgar_client, "build_sec_http_client", spy)
    captured: dict = {}
    real_clients = query_handler.Clients

    def spy_clients(*a, **kw):
        captured["c"] = real_clients(*a, **kw)
        return captured["c"]

    monkeypatch.setattr(bot.query_handler, "Clients", spy_clients)
    bot.build_application(
        telegram_token="123456:dummy-token-for-tests",
        allowed_chat_ids=frozenset({12345}),
        db_path=str(tmp_path / "f1b2_sec.db"),
        fmp_api_key="test-fmp-key",
        fred_api_key=None,
    )
    assert len(construidos) == 1
    assert captured["c"].sec_edgar_http is construidos[0]
    assert construidos[0].follow_redirects is False


# ---------------------------------------------------------------------------
# T-CS-19 / CA-25 (CS-19): Finnhub, token por header y sin fuga
# ---------------------------------------------------------------------------


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fdg_ca25_get_peers_envia_el_token_por_header_y_no_por_query():
    vistos = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        return httpx.Response(200, json=["MSFT"])

    assert await finnhub_client.get_peers(_client(handler), CANARY_FINNHUB, "ADBE") == ["MSFT"]
    (request,) = vistos
    assert request.headers["X-Finnhub-Token"] == CANARY_FINNHUB
    assert "token" not in request.url.params
    assert CANARY_FINNHUB not in str(request.url) and CANARY_FINNHUB not in request.url.query.decode()
    assert request.url.params["symbol"] == "ADBE"


def _boom_timeout(request):
    raise httpx.TimeoutException("timeout", request=request)


def _boom_red(request):
    raise httpx.ConnectError("sin red", request=request)


def _boom_http(request):
    return httpx.Response(402, json={"error": "plan"})


_CAMINOS_DE_ERROR = [_boom_timeout, _boom_http, _boom_red]


@pytest.mark.parametrize("handler", _CAMINOS_DE_ERROR, ids=["timeout", "http_status", "red"])
async def test_fdg_tcs19_la_key_de_finnhub_no_aparece_en_ningun_camino_de_error(caplog, handler):
    caplog.set_level(logging.DEBUG)
    vistos = []

    def espia(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        return handler(request)

    with pytest.raises(finnhub_client.FinnhubError) as info:
        await finnhub_client.get_peers(_client(espia), CANARY_FINNHUB, "ADBE")
    exc = info.value
    assert CANARY_FINNHUB not in str(vistos[0].url) and "token" not in vistos[0].url.params
    assert vistos[0].headers["X-Finnhub-Token"] == CANARY_FINNHUB
    assert CANARY_FINNHUB not in caplog.text
    assert CANARY_FINNHUB not in repr(exc) and CANARY_FINNHUB not in str(exc)
    completo = "".join(traceback.format_exception(exc))
    assert CANARY_FINNHUB not in completo
    # `from None`: el contexto de httpx (que lleva la URL) no se encadena.
    assert exc.__cause__ is None and exc.__suppress_context__ is True
    assert "During handling of the above exception" not in completo
    assert "httpx" not in completo.replace("investbot", "")


def test_fdg_tcs19_repr_de_clients_no_incluye_ninguna_key():
    keys = {
        "fmp_api_key": "CANARY-FMP-KEY-AAAA",
        "fred_api_key": "CANARY-FRED-KEY-BBBB",
        "finnhub_api_key": "CANARY-FINNHUB-KEY-CCCC",
        "sec_edgar_user_agent": "CanaryApp canary-ua@example.com",
    }
    clients = query_handler.Clients(
        fmp_http=None, fred_http=None, treasury_gov_http=None, **keys
    )
    for texto in (repr(clients), str(clients)):
        assert texto.startswith("Clients(") and "fmp_http=" in texto  # el repr sigue siendo útil
        for campo, valor in keys.items():
            assert valor not in texto and f"{campo}=" not in texto
    # los valores siguen accesibles: solo se oculta el repr
    assert clients.finnhub_api_key == keys["finnhub_api_key"]


@pytest.mark.parametrize("campo", ["fmp_api_key", "fred_api_key", "finnhub_api_key", "sec_edgar_user_agent"])
def test_fdg_tcs19_cada_campo_secreto_de_clients_tiene_repr_false(campo):
    por_nombre = {f.name: f for f in dataclasses.fields(query_handler.Clients)}
    assert por_nombre[campo].repr is False


_KEYS_FINNHUB_INVALIDAS = [
    "CANARY KEY CON ESPACIOS 0123456789",
    "CANARY-KEY-CON-COMILLA'-0123456789",
    "CANARY-KEY-CON-NEWLINE-0123456789\n",
    "CANARY-CORTA",
    "CANARY-ñ-NO-ASCII-0123456789",
    "C" * 257,
]


@pytest.mark.parametrize("key", _KEYS_FINNHUB_INVALIDAS)
def test_fdg_tcs19_key_invalida_de_finnhub_deshabilita_finnhub_con_error_sin_valor(
    monkeypatch, tmp_path, caplog, key
):
    _env_main(monkeypatch, tmp_path, FINNHUB_API_KEY=key)
    caplog.set_level(logging.INFO)
    captured = _spy_main(monkeypatch)
    bot.main()
    assert captured["build"]["finnhub_api_key"] is None
    errores = _errores(caplog)
    assert len(errores) == 1 and "FINNHUB_API_KEY" in errores[0].getMessage()
    assert key.strip() not in caplog.text and "CANARY" not in caplog.text


@pytest.mark.parametrize("key", [CANARY_FINNHUB, "x" * 16, "x" * 256])
def test_fdg_tcs19_key_valida_de_finnhub_pasa_intacta(monkeypatch, tmp_path, caplog, key):
    _env_main(monkeypatch, tmp_path, FINNHUB_API_KEY=key)
    caplog.set_level(logging.INFO)
    captured = _spy_main(monkeypatch)
    bot.main()
    assert captured["build"]["finnhub_api_key"] == key
    assert not _errores(caplog)


# ---------------------------------------------------------------------------
# T-CS-8: NetworkGuard activo (un intento de red real falla y queda registrado)
# ---------------------------------------------------------------------------


async def test_fdg_tcs8_networkguard_activo_un_intento_de_red_real_falla(net_guard):
    async with httpx.AsyncClient() as client:  # sin MockTransport: transporte real
        with pytest.raises(AssertionError, match="red real bloqueado"):
            await client.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")
    assert len(net_guard.attempts) == 1 and "data.sec.gov" in net_guard.attempts[0]
    net_guard.attempts.clear()  # el intento era el objeto del test


async def test_fdg_tcs8_el_cliente_de_sec_de_bot_tampoco_sale_a_la_red(net_guard):
    client = bot.sec_edgar_client.build_sec_http_client()
    with pytest.raises(AssertionError, match="red real bloqueado"):
        await client.get("https://www.sec.gov/files/company_tickers.json")
    assert len(net_guard.attempts) == 1
    net_guard.attempts.clear()
    await client.aclose()


# ---------------------------------------------------------------------------
# T-CS-6 / T-CS-22 (CS-7, CS-22): escaneo de emails
# ---------------------------------------------------------------------------

# Parte local obligatoria: `@pytest.mark...` (decorador) no es un email.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_DOMINIOS_PERMITIDOS = frozenset({"example.com", "ejemplo.com"})
# TLD reservados por RFC 2606/6761: no pueden ser un buzón real (se usan en
# tests de F1b-1: `ejemplo.test`, `data.sec.gov.evil.example`).
_TLD_RESERVADOS = (".test", ".example", ".invalid", ".localhost")
# La spec CONGELADA (no editable) cita un ejemplo histórico con un dominio real en
# una línea de su sección 15. Es la única excepción, por ARCHIVO (nunca por
# dirección): ni `src/`, ni `tests/`, ni el README, ni `.env.example` la tienen.
_SPEC_CONGELADA = "contexto/specs/abiertas/SDD_fuentes_datos_gratuitas.md"
_RAICES = ("src", "scripts", "tests", "README.md", ".env.example", "contexto")


def _emails_no_permitidos(texto: str) -> list[str]:
    malos = []
    for email in _EMAIL_RE.findall(texto):
        dominio = email.rsplit("@", 1)[1].lower()
        if dominio in _DOMINIOS_PERMITIDOS:
            continue
        if dominio.endswith(_TLD_RESERVADOS):
            continue
        malos.append(email)
    return malos


def _archivos_a_escanear():
    for raiz in _RAICES:
        base = REPO / raiz
        if not base.exists():
            continue
        candidatos = [base] if base.is_file() else sorted(base.rglob("*"))
        for f in candidatos:
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                yield f


def test_fdg_tcs6_ningun_email_real_en_el_repo():
    escaneados = 0
    hallazgos = []
    for f in _archivos_a_escanear():
        try:
            texto = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        escaneados += 1
        if f.relative_to(REPO).as_posix() == _SPEC_CONGELADA:
            continue
        hallazgos += [f"{f.relative_to(REPO)}:{i}" for i, linea in enumerate(texto.splitlines(), 1)
                      if _emails_no_permitidos(linea)]
    assert escaneados > 100  # control positivo: el recorrido vio el repo real
    assert hallazgos == [], f"emails fuera de la lista blanca (valores omitidos a propósito): {hallazgos}"


def test_fdg_tcs6_el_detector_distingue_emails_reales_de_decoradores_y_placeholders():
    real = "usuario" + "@" + "gmail.com"
    assert _emails_no_permitidos(f"contacto: {real}") == [real]
    assert _emails_no_permitidos("x@" + "empresa.co, y@" + "dominio.org") != []
    assert _emails_no_permitidos("@pytest.mark.asyncio\n    @pytest.mark.parametrize") == []
    assert _emails_no_permitidos("a@example.com b@ejemplo.com A@EXAMPLE.COM") == []
    assert _emails_no_permitidos(_at("a", "sub.example.com")) != []  # subdominio no está en la lista blanca
    assert _emails_no_permitidos("a@ejemplo.test b@data.sec.gov.evil.example") == []
    assert _emails_no_permitidos("ana@" + "gmail.com") != []  # ya no hay excepcion por direccion
    assert _emails_no_permitidos("ana@ejemplo.test") == []


def test_fdg_tcs23_el_ejemplo_documentado_en_env_example_pasa_el_validador():
    m = re.search(r'p\. ej\. "([^"]+)"', ENV_EXAMPLE.read_text(encoding="utf-8"))
    assert m and m.group(1) == "InvestBot ana@ejemplo.test"
    assert bot._valid_sec_user_agent(m.group(1))


def test_fdg_tcs6_el_alcance_incluye_todas_las_raices_pedidas():
    vistos = {f.relative_to(REPO).parts[0] for f in _archivos_a_escanear()}
    assert {"src", "scripts", "tests", "README.md", ".env.example", "contexto"} <= vistos


def test_fdg_tcs22_spec_de_peers_solo_tiene_el_placeholder_de_contacto():
    texto = PEERS_SPEC.read_text(encoding="utf-8")
    emails = _EMAIL_RE.findall(texto)
    assert len(emails) >= 8  # las líneas redactadas siguen ahí, con el placeholder
    assert set(emails) == {"contacto@ejemplo.com"}


# ---------------------------------------------------------------------------
# T-CS-26 (CS-26): `.env.example` sin secretos en líneas de valor
# ---------------------------------------------------------------------------

_MARCAS_SECRETAS = ("KEY", "TOKEN", "USER_AGENT", "CHAT_ID")


def _lineas_de_valor_secretas_con_valor(texto: str) -> list[str]:
    """Nombres de variables con marca secreta cuya línea de VALOR no termina en `=`.
    Solo líneas de valor (`NOMBRE=...`): los comentarios no cuentan (QM-6)."""
    malas = []
    for linea in texto.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", linea)
        if m and any(marca in m.group(1) for marca in _MARCAS_SECRETAS) and m.group(2) != "":
            malas.append(m.group(1))
    return malas


def test_fdg_tcs26_env_example_lineas_secretas_terminan_en_igual_vacio():
    texto = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert _lineas_de_valor_secretas_con_valor(texto) == []
    # control positivo: las 6 variables secretas existen como líneas de valor vacías
    vacias = {l[:-1] for l in texto.splitlines() if re.fullmatch(r"[A-Z_]+=", l)}
    assert {
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_ID", "FMP_API_KEY", "FRED_API_KEY",
        "FINNHUB_API_KEY", "SEC_EDGAR_USER_AGENT", "GEMINI_API_KEY",
    } <= vacias


def test_fdg_tcs26_el_detector_ignora_comentarios_y_marca_valores():
    assert _lineas_de_valor_secretas_con_valor("# FOO_KEY=abc\n  # BAR_TOKEN=x\nFOO_KEY=\n") == []
    assert _lineas_de_valor_secretas_con_valor("FOO_KEY=abc") == ["FOO_KEY"]
    assert _lineas_de_valor_secretas_con_valor("X_USER_AGENT=ua\nY_CHAT_ID=1\nZ_TOKEN= ") == [
        "X_USER_AGENT", "Y_CHAT_ID", "Z_TOKEN",
    ]
    assert _lineas_de_valor_secretas_con_valor("GEMINI_MODEL=gemini-3.5-flash-lite") == []


# ---------------------------------------------------------------------------
# T-CS-29 (CS-29): URL del 8-K con `accessionNumber` y `primaryDocument` validados
# ---------------------------------------------------------------------------

_ACCN = "0000796343-26-000123"
_DOC = "doc.htm"
_URL_OK = "https://www.sec.gov/Archives/edgar/data/796343/000079634326000123/doc.htm"


def test_fdg_tcs29_datos_validos_generan_la_url_de_siempre():
    assert corporate_events._build_filing_url("0000796343", _ACCN, _DOC) == _URL_OK
    assert corporate_events._build_filing_url("0000796343", _ACCN, "a8-k_2026.v2.htm") is not None
    assert corporate_events._build_filing_url("0000796343", _ACCN, "a" * 100) is not None


@pytest.mark.parametrize(
    "accn",
    [
        "../../0000796343-26-000123", "0000796343-26-000123/../x", "0000796343-26-00012)",
        "0000796343_26_000123", "0000796343-26-000123 ", " 0000796343-26-000123",
        "0000796343 26 000123", "0000796343-26-000123\n", "000079634326000123",
        "0000796343-26-0001234", "000079634-26-000123", "a-1", "0000796343-26-00012x",
        "٠٠٠٠٧٩٦٣٤٣-٢٦-٠٠٠١٢٣", [], 123,
    ],
)
def test_fdg_tcs29_accession_invalido_no_genera_url(accn):
    assert corporate_events._build_filing_url("0000796343", accn, _DOC) is None


@pytest.mark.parametrize(
    "doc",
    [
        "../doc.htm", "..", "a..b.htm", "x/../../etc", "doc/x.htm", "doc).htm", "doc(x).htm",
        "doc htm", "doc.htm ", "doc.htm\n", "doc?x=1", "do c.htm", "a" * 101, "dóc.htm", [], 5,
    ],
)
def test_fdg_tcs29_primary_document_invalido_no_genera_url(doc):
    assert corporate_events._build_filing_url("0000796343", _ACCN, doc) is None


def test_fdg_tcs29_el_guion_bajo_es_valido_en_primary_document_pero_no_en_accession():
    # `_` pertenece a `[A-Za-z0-9._-]` del documento (nombres reales como `a8-k_2026.htm`);
    # en el accession el formato SEC es solo dígitos y guiones.
    assert corporate_events._build_filing_url("0000796343", _ACCN, "a_b.htm") is not None
    assert corporate_events._build_filing_url("0000796343", "0000796343_26_000123", "a_b.htm") is None


def _submissions(accn, doc):
    return {
        "filings": {
            "recent": {
                "form": ["8-K"],
                "filingDate": [(date.today() - timedelta(days=10)).isoformat()],
                "accessionNumber": [accn],
                "primaryDocument": [doc],
                "items": ["5.02"],
            }
        }
    }


def test_fdg_tcs29_evento_con_datos_hostiles_se_conserva_sin_link():
    for accn, doc in ((_ACCN, "../doc.htm"), ("x)y", _DOC), (_ACCN, "doc).htm")):
        (evento,) = corporate_events.extract_relevant_8k_events(_submissions(accn, doc), "0000796343")
        assert evento.filing_url == "" and evento.labels == ["Cambio de directivos o ejecutivos"]
    (bueno,) = corporate_events.extract_relevant_8k_events(_submissions(_ACCN, _DOC), "0000796343")
    assert bueno.filing_url == _URL_OK


def test_fdg_tcs29_campos_ausentes_o_cik_invalido_siguen_descartando_el_evento():
    assert corporate_events.extract_relevant_8k_events(_submissions(_ACCN, ""), "0000796343") == []
    assert corporate_events.extract_relevant_8k_events(_submissions("", _DOC), "0000796343") == []
    assert corporate_events.extract_relevant_8k_events(_submissions(_ACCN, _DOC), "no-es-cik") == []


def test_fdg_tcs29_la_seccion_del_resumen_omite_el_link_cuando_no_hay_url():
    base = {"filing_date": "2026-06-15", "labels": ["Resultados"]}
    con = summary.build_corporate_events_section([{**base, "filing_url": _URL_OK}])
    sin = summary.build_corporate_events_section([{**base, "filing_url": ""}])
    assert f"[ver el filing]({_URL_OK})" in con
    assert "- 2026-06-15: Resultados\n" in sin and "ver el filing" not in sin and "]()" not in sin


# ---------------------------------------------------------------------------
# Higiene transversal de F1b-2: sin redirecciones ni verificación TLS desactivada
# ---------------------------------------------------------------------------


def test_fdg_f1b2_src_sin_follow_redirects_true_ni_verify_false():
    for f in sorted((REPO / "src").rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.keyword) and isinstance(node.value, ast.Constant):
                assert not (node.arg == "follow_redirects" and node.value.value is True), f
                assert not (node.arg == "verify" and node.value.value is False), f
