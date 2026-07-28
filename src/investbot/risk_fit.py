"""Comparación beta/tipo de activo vs perfil de riesgo guardado (Decisión #5).

Regla default (ajustable, no bloqueante — documentada explícitamente en la
spec como asunción del `architect`, no del material fuente de Daniela):
- beta < 0.8   → compatible con Muy Conservador / Conservador
- 0.8 <= beta <= 1.2 → compatible con Moderado
- beta > 1.2   → compatible con Agresivo

Toda acción individual se etiqueta como "renta variable" para el mensaje de
encaje.
"""

from __future__ import annotations

from dataclasses import dataclass

RENTA_VARIABLE_LABEL = "renta variable"

_PERFILES_CONSERVADORES = {"muy_conservador", "conservador"}


@dataclass
class RiskFitResult:
    encaja: bool
    perfil: str
    beta: float
    etiqueta_activo: str = RENTA_VARIABLE_LABEL


def evaluate_risk_fit(beta: float, perfil: str) -> RiskFitResult:
    """Evalúa si una acción (por su beta) encaja con el perfil de riesgo guardado."""
    if beta < 0.8:
        compatible_con = _PERFILES_CONSERVADORES
    elif beta <= 1.2:
        compatible_con = {"moderado"}
    else:
        compatible_con = {"agresivo"}

    return RiskFitResult(encaja=perfil in compatible_con, perfil=perfil, beta=beta)
