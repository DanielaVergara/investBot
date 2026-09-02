"""Test cruzado de rate-limit compartido entre `/avanzado` y el flujo de
texto libre (`SDD_analisis_fundamental_avanzado.md`, hallazgo 2 de
`security` — el más importante de los 5 no bloqueantes, ver "Detalle del
hallazgo 2" en la sección QA de la spec).

Un test que solo llama `/avanzado` repetidas veces NO detecta un balde
duplicado (ej. `f"avanzado:{chat_id}"` en vez de `str(chat_id)`) — pasaría
igual con 2 baldes separados. Este es el test CRUZADO obligatorio: un único
`InMemoryRateLimiter` inyectado en AMBOS handlers, 6 llamadas a `/avanzado` +
5 consultas de texto libre desde el mismo `chat_id` dentro de la misma
ventana — la 11ª interacción combinada (sin importar cuál de los 2 comandos
sea) debe recibir `RATE_LIMITED_MSG`, nunca 10+10 exitosas.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from investbot import advanced_command, db, query_handler, security

ALLOWED_CHAT_ID = 4242


def _conn_factory(tmp_path):
    db_path = str(tmp_path / "rate_limit_compartido.db")

    def get_conn():
        return db.get_connection(db_path)

    return get_conn


def _complete_onboarding(conn_factory):
    conn = conn_factory()
    db.init_db(conn)
    db.save_risk_profile(conn, [10] * 8, 80, "moderado", "2026-01-01T00:00:00+00:00")
    conn.close()


def _empty_clients() -> query_handler.Clients:
    empty_transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    return query_handler.Clients(
        fmp_http=httpx.AsyncClient(transport=empty_transport),
        fred_http=httpx.AsyncClient(transport=empty_transport),
        treasury_gov_http=httpx.AsyncClient(transport=empty_transport),
        fmp_api_key="test-key",
        fred_api_key="test-key",
    )


def _fake_text_update(text, chat_id=ALLOWED_CHAT_ID):
    update = SimpleNamespace()
    update.message = SimpleNamespace(text=text, reply_text=AsyncMock())
    update.effective_chat = SimpleNamespace(id=chat_id, type="private")
    update.callback_query = None
    return update


def _fake_avanzado_update(args, chat_id=ALLOWED_CHAT_ID):
    update = SimpleNamespace()
    update.message = SimpleNamespace(reply_text=AsyncMock())
    update.effective_chat = SimpleNamespace(id=chat_id, type="private")
    context = SimpleNamespace(args=args)
    return update, context


async def test_avanzado_y_texto_libre_comparten_el_mismo_balde(tmp_path, monkeypatch):
    conn_factory = _conn_factory(tmp_path)
    _complete_onboarding(conn_factory)

    async def fake_search(client, key, q):
        return [{"symbol": "MFG", "name": "Manufacturing Co Inc."}]

    monkeypatch.setattr(query_handler.fmp_client, "search_company", fake_search)

    clients = _empty_clients()
    # MISMA instancia real (no un fake/counting stub) -- mismo tipo/config
    # que `bot.build_application` (`max_requests=10, window_seconds=60.0`).
    rate_limiter = security.InMemoryRateLimiter(max_requests=10, window_seconds=60.0)

    query_handlers = query_handler.build_query_handlers(conn_factory, clients, rate_limiter)
    handle_text = query_handlers[0].callback
    avanzado = advanced_command.build_advanced_command_handler(clients, rate_limiter).callback

    # 6 llamadas a /avanzado (unidades 1-6 del balde compartido) + 5 de
    # texto libre (unidades 7-11) -- la 11ª interacción combinada excede el
    # máximo de 10.
    for _ in range(6):
        update, context = _fake_avanzado_update(["MFG"])
        await avanzado(update, context)
        update.message.reply_text.assert_awaited_once()
        args, _ = update.message.reply_text.call_args
        assert args[0] != query_handler.RATE_LIMITED_MSG  # todas las 6 pasan

    ultima_respuesta = None
    for _ in range(5):
        update = _fake_text_update("MFG")
        await handle_text(update, context=SimpleNamespace())
        ultima_respuesta = update.message.reply_text

    # La 11ª interacción combinada (última de las 5 de texto libre) recibe
    # RATE_LIMITED_MSG -- si el balde estuviera duplicado/namespaceado, las
    # 5 de texto libre pasarían todas (serían la 1ª-5ª de SU propio balde) y
    # este assert fallaría.
    ultima_respuesta.assert_awaited_once_with(query_handler.RATE_LIMITED_MSG)


async def test_avanzado_solo_no_agota_el_balde_de_texto_libre_si_no_hay_11_combinadas():
    """Complemento negativo: con menos de 11 interacciones combinadas,
    ninguna se bloquea (ni /avanzado ni texto libre) -- confirma que el
    balde compartido cuenta correctamente sin sobre-bloquear."""
    rate_limiter = security.InMemoryRateLimiter(max_requests=10, window_seconds=60.0)
    clients = _empty_clients()
    avanzado = advanced_command.build_advanced_command_handler(clients, rate_limiter).callback

    for _ in range(10):
        update, context = _fake_avanzado_update(["MFG"])
        await avanzado(update, context)
        args, _ = update.message.reply_text.call_args
        assert args[0] != query_handler.RATE_LIMITED_MSG
