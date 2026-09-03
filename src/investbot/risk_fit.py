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

# SDD_explicacion_paso_a_paso.md, Decisión de diseño #5 -- mismo patrón que
# `advanced_scoring.LOW_VOL_BETA_UMBRAL_BAJO`/`_ALTO`: nombra los literales
# 0.8/1.2 ya existentes, sin cambio de comportamiento. Necesario para que el
# botón "paso a paso" de `rsk` pueda mandar estos 2 números a Ollama como
# dato garantizado (Bug 2 -- un número del marco conceptual del bot tiene
# que viajar nombrado o el guard lo rechaza).
BETA_UMBRAL_BAJO = 0.8
BETA_UMBRAL_ALTO = 1.2


@dataclass
class RiskFitResult:
    encaja: bool
    perfil: str
    beta: float
    etiqueta_activo: str = RENTA_VARIABLE_LABEL


def evaluate_risk_fit(beta: float, perfil: str) -> RiskFitResult:
    """Evalúa si una acción (por su beta) encaja con el perfil de riesgo guardado."""
    if beta < BETA_UMBRAL_BAJO:
        compatible_con = _PERFILES_CONSERVADORES
    elif beta <= BETA_UMBRAL_ALTO:
        compatible_con = {"moderado"}
    else:
        compatible_con = {"agresivo"}

    return RiskFitResult(encaja=perfil in compatible_con, perfil=perfil, beta=beta)
