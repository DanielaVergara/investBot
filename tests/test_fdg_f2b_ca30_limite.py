"""F2b de SDD_fuentes_datos_gratuitas.md: CA-30 de punta a punta (20 consultas rapidas de analisis completo no
superan el bucket de Finnhub y degradan a N/D o a FMP, nunca a error) y el kill-switch (13.4). Reloj
inyectado y SIN tiempo real: el `sleep` del bucket nunca duerme."""

from __future__ import annotations

import pytest

from investbot import finnhub_client, summary

from .fdg_f2b_helpers import World, build_clients
from .test_fdg_f1b3_edgar_provider import _reset_global_map  # noqa: F401
from .test_fdg_f2b_ca28_integracion import _analizar
from .test_fdg_f2b_ca28_integracion import pytestmark  # noqa: F401

SIN_DATOS = "No pude obtener suficientes datos de MELI para analizarlo ahora mismo."


def _sleeps():
    esperas: list[float] = []

    async def sleep(delay: float) -> None:
        esperas.append(delay)

    return esperas, sleep


async def test_fdg_ca30_veinte_consultas_rapidas_de_analisis_completo_no_superan_el_bucket_y_degradan_a_na():
    world = World(fmp="down")
    esperas, sleep = _sleeps()
    clients, clock = build_clients(world, rate_per_minute=50, sleep=sleep)
    resultados = []
    for _ in range(20):
        parts, _sink = await _analizar(clients)  # ninguna lanza
        resultados.append(parts)
    assert len(world.host_requests("finnhub.io")) == 50  # justo el bucket configurado: ni una mas
    assert esperas == []  # el limite degrada, no espera (el cupo se libera recien a los 60 s)
    # las primeras consultas salen completas; las siguientes degradan a N/D (mensaje de siempre), no a error
    assert SIN_DATOS not in resultados[0][0] and "Veredicto" in "\n".join(resultados[0])
    assert resultados[-1] == [SIN_DATOS]
    assert all(isinstance(p, list) and all(isinstance(x, str) for x in p) for p in resultados)
    degradadas = [r for r in resultados if r == [SIN_DATOS]]
    assert 0 < len(degradadas) < 20


async def test_fdg_ca30_con_fmp_sirviendo_el_limite_de_finnhub_degrada_a_fmp_con_aviso_y_sin_error():
    world = World(fmp="ok")
    esperas, sleep = _sleeps()
    clients, _ = build_clients(world, rate_per_minute=50, sleep=sleep)
    textos = []
    for _ in range(20):
        parts, _sink = await _analizar(clients)
        textos.append("\n".join(parts))
    assert len(world.host_requests("finnhub.io")) == 50
    assert all(SIN_DATOS not in t for t in textos)  # siempre analisis completo (EDGAR + FMP de respaldo)
    assert f"_{summary.AVISO_FINNHUB_RESPALDO}_" in textos[-1].split("\n")
    assert "cotización: FMP" in textos[-1] and "cotización: Finnhub" in textos[0]
    # el limite de tasa NO es un rechazo: no abrio el breaker
    assert finnhub_client.gate_for(clients.finnhub_http).open_until is None
    assert summary.AVISO_FINNHUB_BLOQUEADO not in textos[-1]


async def test_fdg_ca30_la_ventana_de_60_segundos_se_libera_y_finnhub_vuelve_a_servir():
    world = World(fmp="down")
    esperas, sleep = _sleeps()
    clients, clock = build_clients(world, rate_per_minute=50, sleep=sleep)
    for _ in range(20):
        await _analizar(clients)
    assert (await _analizar(clients))[0] == [SIN_DATOS]
    clock.t += 61.0  # pasa un minuto (reloj inyectado)
    parts, _ = await _analizar(clients)
    assert parts != [SIN_DATOS] and "Veredicto" in "\n".join(parts)
    assert esperas == []


async def test_fdg_ca30_el_presupuesto_de_20s_por_consulta_degrada_las_llamadas_restantes_sin_error():
    """CS-27: con el presupuesto agotado la consulta no pide mas a Finnhub y degrada."""
    world = World(fmp="down")
    clients, clock = build_clients(world)
    budget = clients.market_data.new_budget()
    clock.t += budget.total + 1  # ya vencio
    snapshot = await clients.market_data.fetch("MELI", budget=budget)
    assert snapshot.provenance() == {"quote": None, "profile": None, "metrics": None, "vix": "fred"}
    assert world.host_requests("finnhub.io") == []
    assert finnhub_client.CODE_BUDGET in snapshot.fallback_codes()
