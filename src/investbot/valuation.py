"""Motor propio de valoración — 3 métodos + promedio parcial.

Funciones puras: reciben datos ya resueltos (por `fmp_client.py`, `peers.py`,
`treasury_client.py`) como parámetros, sin I/O adentro (criterio de `qa`,
sección 6 — "no hay lógica de negocio escondida", testeable sin mockear HTTP).

Implementa el Spec Patch [Iter-2] (B1-B5):

- B1: `calculate_cagr()` nunca lanza excepción ni devuelve un número complejo;
  devuelve `None` cuando la base es <= 0, el valor reciente es <= 0, o
  `n_años < 2`. Se reutiliza tanto para el CAGR de EPS (Graham) como para el
  CAGR de FCF (DCF).
- B2: piso mínimo de 3 registros anuales (`n_años >= 2`) para cualquier CAGR.
- B4: Múltiplos y Graham se excluyen del promedio cuando `EPS TTM <= 0`.
- Principio general (B1+B2+B4): cada modelo se calcula de forma independiente;
  si no es calculable, se excluye del promedio (nunca se sustituye por 0).
  `Valor Justo Total` = promedio simple únicamente de los modelos calculables.

Convención de unidades (decisión de implementación, documentada aquí porque la
spec no fija unidades explícitas):
- `g` (CAGR) es una fracción decimal (ej. 0.10 = 10%), igual que devuelve
  `calculate_cagr()`.
- `y` (rendimiento del bono del tesoro) es una fracción decimal (ej. 0.044 =
  4.4%), consistente con su uso en CAPM/WACC en `calculate_wacc()`.
- La fórmula de Graham (`EPS × (8.5 + 2×g) × 4.4 / Y`) es la fórmula clásica
  de Benjamin Graham, calibrada históricamente con `g` e `Y` expresados como
  números porcentuales (ej. 10, no 0.10) — así que `calculate_graham_fair_value`
  convierte `g` e `y` a puntos porcentuales *solo dentro de esa función*,
  manteniendo el resto del sistema (WACC, CAPM, descuento de flujos) en la
  convención de fracción decimal estándar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

MARKET_RISK_PREMIUM = 0.055  # 5.5% — constante documentada, Decisión de diseño #10
GRAHAM_HISTORICAL_YIELD = 4.4  # constante de la fórmula original de Graham
TERMINAL_GROWTH_RATE = 0.025  # 2.5% — supuesto explícito de largo plazo (perpetuidad)
DCF_PROJECTION_YEARS = 5
CAGR_MIN_N_AÑOS = 2  # piso B2: al menos 3 registros anuales (n_años = registros - 1)


def calculate_cagr(
    valor_reciente: float, valor_antiguo: float, n_años: int
) -> Optional[float]:
    """CAGR con guardas explícitas (Spec Patch Iter-2, B1 + B2).

    Nunca lanza excepción, nunca devuelve un número complejo. Devuelve `None`
    cuando el CAGR no es matemáticamente válido o financieramente
    significativo:
      - `valor_antiguo <= 0` → año base en pérdidas o en cero.
      - `valor_reciente <= 0` → año más reciente en pérdidas o en cero.
      - `n_años < 2` → menos de 3 registros anuales (piso B2).

    El llamador es responsable de excluir el modelo correspondiente del
    promedio y registrar el motivo en `modelos_excluidos` — nunca de
    sustituir el resultado por un valor de respaldo.
    """
    if valor_antiguo is None or valor_reciente is None:
        return None
    if valor_antiguo <= 0 or valor_reciente <= 0:
        return None
    if n_años < CAGR_MIN_N_AÑOS:
        return None
    try:
        return (valor_reciente / valor_antiguo) ** (1 / n_años) - 1
    except (ValueError, ZeroDivisionError, OverflowError):  # pragma: no cover
        return None


def calculate_multiplos_fair_value(
    eps_ttm: float, per_promedio_peers: float
) -> Optional[float]:
    """Valor Justo por Múltiplos = EPS TTM × PER promedio de peers (Decisión #9).

    B4: `None` si `eps_ttm <= 0` (excluido del promedio, nunca se calcula un
    "valor justo" negativo o sin sentido de negocio).
    """
    if eps_ttm is None or eps_ttm <= 0:
        return None
    if per_promedio_peers is None:
        return None
    return eps_ttm * per_promedio_peers


def calculate_graham_fair_value(
    eps_ttm: float, g: Optional[float], y: float
) -> Optional[float]:
    """Graham EPS Model = EPS × (8.5 + 2×g_pct) × 4.4 / Y_pct.

    `g` e `y` se reciben como fracción decimal y se convierten a puntos
    porcentuales dentro de esta función (ver docstring del módulo).

    B4: `None` si `eps_ttm <= 0`. B1: `None` si `g` es `None` (CAGR no
    calculable) o si `y <= 0` (tasa libre de riesgo inválida/no disponible).
    """
    if eps_ttm is None or eps_ttm <= 0:
        return None
    if g is None:
        return None
    if y is None or y <= 0:
        return None
    g_pct = g * 100
    y_pct = y * 100
    multiplicador = 8.5 + 2 * g_pct
    return eps_ttm * multiplicador * GRAHAM_HISTORICAL_YIELD / y_pct


def calculate_wacc(
    *,
    interest_expense: float,
    total_debt: float,
    income_tax_expense: float,
    income_before_tax: float,
    beta: float,
    y: float,
    market_cap: float,
    market_risk_premium: float = MARKET_RISK_PREMIUM,
) -> Optional[float]:
    """WACC simplificado (Decisión de diseño #10). Devuelve fracción decimal.

    - Ke (CAPM simplificado) = Y + beta × prima_de_riesgo_de_mercado.
    - Kd = Gastos por intereses / Deuda total, ajustado por la tasa
      impositiva efectiva: Kd × (1 - tasa_impositiva_efectiva).
    - Ponderación por deuda total vs market cap.
    - `WACC = (E/V × Ke) + (D/V × Kd × (1 - t))`.

    Devuelve `None` (sin lanzar excepción) si los datos de entrada no
    permiten un cálculo con sentido (deuda total y market cap ambos <= 0, o
    `income_before_tax <= 0`, que haría la tasa impositiva efectiva
    indefinida).
    """
    if market_cap is None or market_cap <= 0:
        return None
    total_debt = total_debt or 0.0
    if total_debt < 0:
        return None

    v = market_cap + total_debt
    if v <= 0:  # pragma: no cover - inalcanzable (market_cap>0 y total_debt>=0 ya garantizados arriba)
        return None

    ke = y + beta * market_risk_premium

    if total_debt == 0:
        # Sin deuda: WACC colapsa a Ke (no hay componente de deuda que ponderar).
        return ke

    if income_before_tax is None or income_before_tax <= 0:
        # Tasa impositiva efectiva indefinida con utilidad antes de impuestos <= 0.
        # Se usa Kd pre-impuesto como aproximación conservadora en vez de crashear.
        tax_rate = 0.0
    else:
        tax_rate = income_tax_expense / income_before_tax

    kd_pretax = interest_expense / total_debt
    kd_aftertax = kd_pretax * (1 - tax_rate)

    e_weight = market_cap / v
    d_weight = total_debt / v

    return e_weight * ke + d_weight * kd_aftertax


def calculate_dcf_fair_value(
    *,
    fcf_historial: list[float],
    wacc: Optional[float],
    shares_outstanding: float,
    terminal_growth: float = TERMINAL_GROWTH_RATE,
    years: int = DCF_PROJECTION_YEARS,
) -> Optional[float]:
    """DCF por acción: proyección de FCF a `years` + valor terminal (Gordon Growth).

    `fcf_historial` debe venir ordenado de más antiguo a más reciente (mismo
    orden que `/cash-flow-statement`). El CAGR propio de FCF se calcula
    internamente con `calculate_cagr()` (misma guarda B1/B2) — si no es
    calculable, el modelo completo devuelve `None` (excluido del promedio).
    """
    if not fcf_historial or len(fcf_historial) < CAGR_MIN_N_AÑOS + 1:
        return None
    if shares_outstanding is None or shares_outstanding <= 0:
        return None
    if wacc is None or wacc <= terminal_growth:
        # WACC debe superar el crecimiento terminal para que la perpetuidad
        # tenga un denominador positivo — de lo contrario no hay valor
        # terminal financieramente válido.
        return None

    fcf_reciente = fcf_historial[-1]
    fcf_antiguo = fcf_historial[0]
    n_años = len(fcf_historial) - 1

    g_fcf = calculate_cagr(fcf_reciente, fcf_antiguo, n_años)
    if g_fcf is None:
        return None

    fcf_proyectado = []
    fcf = fcf_reciente
    for _ in range(years):
        fcf = fcf * (1 + g_fcf)
        fcf_proyectado.append(fcf)

    valor_presente = sum(
        flujo / (1 + wacc) ** t for t, flujo in enumerate(fcf_proyectado, start=1)
    )

    valor_terminal = (
        fcf_proyectado[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
    )
    valor_presente_terminal = valor_terminal / (1 + wacc) ** years

    equity_value = valor_presente + valor_presente_terminal
    return equity_value / shares_outstanding


@dataclass
class ModeloExcluido:
    modelo: str
    motivo: str


@dataclass
class ValuationResult:
    valor_justo_multiplos: Optional[float] = None
    valor_justo_graham: Optional[float] = None
    valor_justo_dcf: Optional[float] = None
    valor_justo_total: Optional[float] = None
    modelos_excluidos: list[ModeloExcluido] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "valor_justo_multiplos": self.valor_justo_multiplos,
            "valor_justo_graham": self.valor_justo_graham,
            "valor_justo_dcf": self.valor_justo_dcf,
            "valor_justo_total": self.valor_justo_total,
            "modelos_excluidos": [
                {"modelo": m.modelo, "motivo": m.motivo} for m in self.modelos_excluidos
            ],
        }


def compute_valuation(
    *,
    eps_ttm: float,
    eps_historial: list[float],
    per_promedio_peers: Optional[float],
    fcf_historial: list[float],
    y: Optional[float],
    wacc_inputs: dict,
    shares_outstanding: float,
) -> ValuationResult:
    """Orquesta los 3 modelos y arma la estructura de retorno del Spec Patch Iter-2.

    `wacc_inputs` es el diccionario de kwargs para `calculate_wacc()`
    (interest_expense, total_debt, income_tax_expense, income_before_tax,
    beta, market_cap) — `y` se pasa aparte porque también lo usa Graham.

    Cada modelo se calcula de forma independiente; si no es calculable, se
    excluye del promedio con su motivo (nunca se sustituye por 0).
    """
    result = ValuationResult()

    eps_no_positivo = eps_ttm is None or eps_ttm <= 0

    # --- Múltiplos ---
    if eps_no_positivo:
        result.modelos_excluidos.append(
            ModeloExcluido("multiplos", "eps_ttm_no_positivo")
        )
    else:
        result.valor_justo_multiplos = calculate_multiplos_fair_value(
            eps_ttm, per_promedio_peers
        )
        if result.valor_justo_multiplos is None:
            result.modelos_excluidos.append(
                ModeloExcluido("multiplos", "per_peers_no_disponible")
            )

    # --- Graham EPS Model ---
    if eps_no_positivo:
        result.modelos_excluidos.append(ModeloExcluido("graham", "eps_ttm_no_positivo"))
    else:
        n_años_eps = len(eps_historial) - 1 if eps_historial else 0
        eps_antiguo = eps_historial[0] if eps_historial else None
        eps_reciente = eps_historial[-1] if eps_historial else None
        g_eps = calculate_cagr(eps_reciente, eps_antiguo, n_años_eps)
        if g_eps is None:
            motivo = _motivo_cagr_invalido(eps_reciente, eps_antiguo, n_años_eps, "eps")
            result.modelos_excluidos.append(ModeloExcluido("graham", motivo))
        elif y is None or y <= 0:
            result.modelos_excluidos.append(ModeloExcluido("graham", "y_no_disponible"))
        else:
            result.valor_justo_graham = calculate_graham_fair_value(eps_ttm, g_eps, y)

    # --- DCF ---
    n_años_fcf = len(fcf_historial) - 1 if fcf_historial else 0
    fcf_antiguo = fcf_historial[0] if fcf_historial else None
    fcf_reciente = fcf_historial[-1] if fcf_historial else None
    g_fcf = calculate_cagr(fcf_reciente, fcf_antiguo, n_años_fcf)
    if g_fcf is None:
        motivo = _motivo_cagr_invalido(fcf_reciente, fcf_antiguo, n_años_fcf, "fcf")
        result.modelos_excluidos.append(ModeloExcluido("dcf", motivo))
    else:
        wacc = calculate_wacc(y=y or 0.0, **wacc_inputs) if y is not None else None
        if wacc is None:
            result.modelos_excluidos.append(ModeloExcluido("dcf", "wacc_no_calculable"))
        else:
            result.valor_justo_dcf = calculate_dcf_fair_value(
                fcf_historial=fcf_historial,
                wacc=wacc,
                shares_outstanding=shares_outstanding,
            )
            if result.valor_justo_dcf is None:
                result.modelos_excluidos.append(
                    ModeloExcluido("dcf", "dcf_no_calculable")
                )

    valores = [
        v
        for v in (
            result.valor_justo_multiplos,
            result.valor_justo_graham,
            result.valor_justo_dcf,
        )
        if v is not None
    ]
    result.valor_justo_total = sum(valores) / len(valores) if valores else None

    return result


def _motivo_cagr_invalido(
    reciente: Optional[float], antiguo: Optional[float], n_años: int, prefijo: str
) -> str:
    """Motivo específico de exclusión para logs/tests, sin romper `qa` B1/B2."""
    if n_años < CAGR_MIN_N_AÑOS:
        return "historial_insuficiente"
    if antiguo is None or antiguo <= 0:
        return f"{prefijo}_base_no_positivo"
    if reciente is None or reciente <= 0:
        return f"{prefijo}_reciente_no_positivo"
    return f"{prefijo}_cagr_no_calculable"  # pragma: no cover - defensivo
