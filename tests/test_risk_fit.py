"""Tests de `risk_fit.py` — regla beta ↔ perfil de riesgo (Decisión de diseño #5)."""

from __future__ import annotations

import pytest

from investbot import risk_fit


@pytest.mark.parametrize(
    "beta,perfil,encaja_esperado",
    [
        (0.5, "muy_conservador", True),
        (0.5, "conservador", True),
        (0.5, "moderado", False),
        (0.5, "agresivo", False),
        (1.0, "moderado", True),
        (1.0, "conservador", False),
        (0.8, "moderado", True),  # frontera inclusiva 0.8
        (1.2, "moderado", True),  # frontera inclusiva 1.2
        (1.5, "agresivo", True),
        (1.5, "moderado", False),
    ],
)
def test_evaluate_risk_fit(beta, perfil, encaja_esperado):
    result = risk_fit.evaluate_risk_fit(beta, perfil)
    assert result.encaja is encaja_esperado
    assert result.etiqueta_activo == "renta variable"
    assert result.beta == beta
    assert result.perfil == perfil
