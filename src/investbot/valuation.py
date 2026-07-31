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

# Spec Patch [Iter-3], sección 1 — desplazamientos absolutos (puntos porcentuales
# en fracción decimal) para los escenarios Pesimista/Optimista alrededor del
# escenario Conservador. Constantes documentadas, ajustables sin que sea una
# "regresión" de un criterio verde (mismo tratamiento que MARKET_RISK_PREMIUM).
DELTA_G = 0.03  # ±3pp — Graham (g) y g_fcf del DCF
DELTA_WACC = 0.01  # ±1pp — WACC del DCF

# SDD_calidad_peers_multiplos.md, Decisión #1 — confirmado por Daniela
# 2026-07-31: con menos de este mínimo de peers con PER válido, el modelo de
# Múltiplos se excluye del promedio de Valor Justo Total (mismo tratamiento
# que eps_ttm_no_positivo/per_peers_no_disponible) — evita que 1 solo peer
# con PER extremo/no representativo (ej. caso real ADBE/PLTR, PER 259x)
# infle valor_justo_total con el mismo peso que Graham/DCF.
MIN_PEERS_VALIDOS_PARA_MULTIPLOS = 2


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

    Guarda (Spec Patch Iter-3, sección 1 — cierra un hueco preexistente de
    Iter-2/B1): si el multiplicador `(8.5 + 2×g_pct) <= 0`, se devuelve `None`
    en vez de un "valor justo" negativo o cero sin sentido de negocio. Con `g`
    muy negativo (empresa con EPS fuertemente decreciente) esto puede ocurrir
    incluso en el escenario conservador — la guarda aplica por igual, sin
    distinguir escenario, motivo por el cual vive aquí y no en
    `compute_valuation_scenarios`.
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
    if multiplicador <= 0:
        return None
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
    g_fcf_override: Optional[float] = None,
    fcf_base_override: Optional[float] = None,
    periodos_por_anio: int = 1,
) -> Optional[float]:
    """DCF por acción: proyección de FCF a `years` + valor terminal (Gordon Growth).

    `fcf_historial` debe venir ordenado de más antiguo a más reciente (mismo
    orden que `/cash-flow-statement`). El CAGR propio de FCF se calcula
    internamente con `calculate_cagr()` (misma guarda B1/B2) — si no es
    calculable, el modelo completo devuelve `None` (excluido del promedio).

    `g_fcf_override` (Spec Patch Iter-3, sección 1): si se provee, se usa este
    valor de `g_fcf` en vez de recalcularlo desde `fcf_historial` — permite a
    `compute_valuation_scenarios` desplazar el crecimiento proyectado
    (`g_fcf ∓ DELTA_G`) sin triplicar la lógica de proyección/descuento.

    `fcf_base_override`/`periodos_por_anio` (`SDD_eps_ttm_real.md`, Decisión
    #14): ambos opcionales, con default retrocompatible (`None`/`1`).
    `periodos_por_anio` corrige el piso mínimo de longitud y el `n_años` del
    CAGR cuando `fcf_historial` es una serie trimestral cruda (4 períodos por
    año) en vez de anual. `fcf_base_override`, si no es `None`, reemplaza
    `fcf_historial[-1]` como ancla de la PROYECCIÓN (nivel) — el CAGR
    (`g_fcf`) sigue midiéndose siempre sobre los valores crudos de
    `fcf_historial[0]`/`fcf_historial[-1]` (tendencia), nunca sobre
    `fcf_base_override`: son 2 preguntas distintas (cuánto genera hoy vs. si
    crece o decrece).
    """
    if not fcf_historial or len(fcf_historial) < (CAGR_MIN_N_AÑOS * periodos_por_anio) + 1:
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
    n_años = (len(fcf_historial) - 1) / periodos_por_anio

    if g_fcf_override is not None:
        g_fcf = g_fcf_override
    else:
        g_fcf = calculate_cagr(fcf_reciente, fcf_antiguo, n_años)
        if g_fcf is None:
            return None

    # Ancla de la PROYECCIÓN (nivel, no tendencia): FCF TTM cuando está
    # disponible (`fcf_base_override`), el último punto crudo del historial
    # en caso contrario — comportamiento anual de hoy, sin cambios, cuando
    # `fcf_base_override` es `None` (default).
    fcf_proyectado = []
    fcf = fcf_base_override if fcf_base_override is not None else fcf_reciente
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
    periodos_por_anio_eps: int = 1,
    periodos_por_anio_fcf: int = 1,
    fcf_base: Optional[float] = None,
    n_peers_validos: Optional[int] = None,
) -> ValuationResult:
    """Orquesta los 3 modelos y arma la estructura de retorno del Spec Patch Iter-2.

    `wacc_inputs` es el diccionario de kwargs para `calculate_wacc()`
    (interest_expense, total_debt, income_tax_expense, income_before_tax,
    beta, market_cap) — `y` se pasa aparte porque también lo usa Graham.

    Cada modelo se calcula de forma independiente; si no es calculable, se
    excluye del promedio con su motivo (nunca se sustituye por 0).

    `periodos_por_anio_eps`/`periodos_por_anio_fcf`/`fcf_base`
    (`SDD_eps_ttm_real.md`, Decisión #13/#14): parámetros aditivos, con
    default retrocompatible (`1`/`1`/`None`) que preserva byte a byte el
    comportamiento anual de antes de esta spec para cualquier llamador que
    no los pase. `periodos_por_anio_eps`/`_fcf` corrigen el `n_años` usado en
    el CAGR de Graham/DCF cuando el historial recibido es trimestral (4
    períodos por año) en vez de anual (1). `fcf_base` es el FCF TTM (si está
    disponible) usado como ancla de nivel de la proyección del DCF — ver
    `calculate_dcf_fair_value`.

    `n_peers_validos` (`SDD_calidad_peers_multiplos.md`, Decisión #2):
    opcional, default `None` (comportamiento idéntico al de antes de esta
    spec — nadie llama a esta función en producción, ver `compute_valuation_
    scenarios` para el camino real). Si se pasa un entero por debajo de
    `MIN_PEERS_VALIDOS_PARA_MULTIPLOS`, Múltiplos se excluye del promedio con
    motivo `"peers_validos_insuficientes"` — mismo criterio que la versión
    de escenarios, para preservar el invariante ya testeado de paridad
    campo a campo entre `compute_valuation(...)` y
    `compute_valuation_scenarios(...).conservador`.
    """
    result = ValuationResult()

    eps_no_positivo = eps_ttm is None or eps_ttm <= 0

    # --- Múltiplos ---
    if eps_no_positivo:
        result.modelos_excluidos.append(
            ModeloExcluido("multiplos", "eps_ttm_no_positivo")
        )
    elif n_peers_validos is not None and n_peers_validos < MIN_PEERS_VALIDOS_PARA_MULTIPLOS:
        result.modelos_excluidos.append(
            ModeloExcluido("multiplos", "peers_validos_insuficientes")
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
        n_años_eps = (len(eps_historial) - 1) / periodos_por_anio_eps if eps_historial else 0
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
            if result.valor_justo_graham is None:
                # Guarda de multiplicador (Spec Patch Iter-3, sección 1): g_eps,
                # y y eps_ttm ya son válidos aquí — el único motivo restante por
                # el que `calculate_graham_fair_value` puede devolver `None` es
                # `(8.5 + 2×g_pct) <= 0`.
                result.modelos_excluidos.append(
                    ModeloExcluido("graham", "graham_multiplicador_no_positivo")
                )

    # --- DCF ---
    n_años_fcf = (len(fcf_historial) - 1) / periodos_por_anio_fcf if fcf_historial else 0
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
                periodos_por_anio=periodos_por_anio_fcf,
                fcf_base_override=fcf_base,
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


# ---------------------------------------------------------------------------
# Spec Patch [Iter-3] — escenarios Pesimista/Conservador/Optimista + [Iter-4]
# (C1-C3). `compute_valuation(...)` arriba no cambia — sigue siendo,
# conceptualmente, el escenario conservador; estas funciones son la capa
# nueva que reutiliza sus mismos bloques de cálculo con parámetros
# desplazados (sin triplicar lógica de negocio, criterio de `qa` Iter-1
# sección 6).
# ---------------------------------------------------------------------------


@dataclass
class ScenarioValuationResult:
    valor_justo_multiplos: Optional[float] = None
    valor_justo_graham: Optional[float] = None
    valor_justo_dcf: Optional[float] = None
    valor_justo_total: Optional[float] = None
    modelos_excluidos: list[ModeloExcluido] = field(default_factory=list)  # nivel 1 + nivel 2

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


@dataclass
class ValuationScenarios:
    pesimista: ScenarioValuationResult
    conservador: ScenarioValuationResult  # idéntico, campo a campo, a compute_valuation()
    optimista: ScenarioValuationResult
    modelos_excluidos_base: list[ModeloExcluido] = field(default_factory=list)  # solo nivel 1

    def as_dict(self) -> dict:
        return {
            "pesimista": self.pesimista.as_dict(),
            "conservador": self.conservador.as_dict(),
            "optimista": self.optimista.as_dict(),
            "modelos_excluidos_base": [
                {"modelo": m.modelo, "motivo": m.motivo} for m in self.modelos_excluidos_base
            ],
        }


def classify_scenario(
    precio_actual: float, valor_justo_total: Optional[float]
) -> Optional[bool]:
    """`True` = barata (precio < valor justo), `False` = cara, `None` = no
    determinable (`valor_justo_total` es `None` en ese escenario — los 3
    modelos quedaron excluidos justo en ese escenario). Función pura, sin I/O
    (Spec Patch Iter-3, sección 3)."""
    if valor_justo_total is None:
        return None
    return precio_actual < valor_justo_total


def compute_valuation_scenarios(
    *,
    eps_ttm: float,
    eps_historial: list[float],
    peer_average,  # peers.PeerAverageResult — no se importa el tipo para evitar ciclo de import
    fcf_historial: list[float],
    y: Optional[float],
    wacc_inputs: dict,
    shares_outstanding: float,
    delta_g: float = DELTA_G,
    delta_wacc: float = DELTA_WACC,
    periodos_por_anio_eps: int = 1,
    periodos_por_anio_fcf: int = 1,
    fcf_base: Optional[float] = None,
) -> ValuationScenarios:
    """Calcula los 3 escenarios (Pesimista/Conservador/Optimista) de los 3
    modelos (Spec Patch Iter-3, secciones 1-2).

    Recibe `peer_average` (`peers.PeerAverageResult` completo, con
    `per_minimo`/`per_promedio`/`per_maximo`) en vez de 3 floats sueltos —
    aclaración de implementación sobre el hueco de firma que dejó abierto
    `qa` en su revisión de Iter-3 (sección 4).

    Dos niveles de exclusión (sección 2 del patch):
    - Nivel 1 (`modelos_excluidos_base`): dato de entrada inválido
      independientemente del escenario (EPS TTM<=0, historial insuficiente,
      Y no disponible, market_cap<=0, 0 peers con PER válido, o menos de
      `MIN_PEERS_VALIDOS_PARA_MULTIPLOS` peers con PER válido —
      `SDD_calidad_peers_multiplos.md`) — igual en los 3 escenarios,
      reportado una sola vez.
    - Nivel 2 (dentro de `ScenarioValuationResult.modelos_excluidos` de cada
      escenario): el dato de base es válido, pero el desplazamiento
      pesimista/optimista empuja un valor intermedio fuera de rango
      matemáticamente válido (guarda de multiplicador de Graham, o
      `wacc <= terminal_growth` en el DCF).

    El escenario conservador usa exactamente los mismos parámetros que
    `compute_valuation(...)` — por diseño, produce un resultado campo a campo
    idéntico.
    """
    eps_no_positivo = eps_ttm is None or eps_ttm <= 0

    # --- Nivel 1: Múltiplos ---
    if eps_no_positivo:
        nivel1_multiplos = ModeloExcluido("multiplos", "eps_ttm_no_positivo")
    elif peer_average.per_promedio is None:
        nivel1_multiplos = ModeloExcluido("multiplos", "per_peers_no_disponible")
    elif len(peer_average.peers_usados) < MIN_PEERS_VALIDOS_PARA_MULTIPLOS:
        # SDD_calidad_peers_multiplos.md, Decisión #1 — 1 solo peer válido
        # (per_promedio ya no-None) no es muestra suficiente para un
        # promedio confiable; distinto del caso de 0 peers de arriba.
        nivel1_multiplos = ModeloExcluido("multiplos", "peers_validos_insuficientes")
    else:
        nivel1_multiplos = None
    multiplos_valido = nivel1_multiplos is None

    # --- Nivel 1: Graham (depende del CAGR de EPS, "g") ---
    n_años_eps = (len(eps_historial) - 1) / periodos_por_anio_eps if eps_historial else 0
    eps_antiguo = eps_historial[0] if eps_historial else None
    eps_reciente = eps_historial[-1] if eps_historial else None
    g_eps = calculate_cagr(eps_reciente, eps_antiguo, n_años_eps)

    if eps_no_positivo:
        nivel1_graham = ModeloExcluido("graham", "eps_ttm_no_positivo")
    elif g_eps is None:
        motivo = _motivo_cagr_invalido(eps_reciente, eps_antiguo, n_años_eps, "eps")
        nivel1_graham = ModeloExcluido("graham", motivo)
    elif y is None or y <= 0:
        nivel1_graham = ModeloExcluido("graham", "y_no_disponible")
    else:
        nivel1_graham = None
    graham_valido = nivel1_graham is None

    # --- Nivel 1: DCF (depende del CAGR de FCF y del WACC conservador) ---
    n_años_fcf = (len(fcf_historial) - 1) / periodos_por_anio_fcf if fcf_historial else 0
    fcf_antiguo = fcf_historial[0] if fcf_historial else None
    fcf_reciente = fcf_historial[-1] if fcf_historial else None
    g_fcf = calculate_cagr(fcf_reciente, fcf_antiguo, n_años_fcf)
    wacc_conservador = calculate_wacc(y=y or 0.0, **wacc_inputs) if y is not None else None

    if g_fcf is None:
        motivo = _motivo_cagr_invalido(fcf_reciente, fcf_antiguo, n_años_fcf, "fcf")
        nivel1_dcf = ModeloExcluido("dcf", motivo)
    elif wacc_conservador is None:
        nivel1_dcf = ModeloExcluido("dcf", "wacc_no_calculable")
    else:
        nivel1_dcf = None
    dcf_valido = nivel1_dcf is None

    modelos_excluidos_base = [
        m for m in (nivel1_multiplos, nivel1_graham, nivel1_dcf) if m is not None
    ]

    def _escenario(
        per_peers: Optional[float], g_delta: float, wacc_delta: float
    ) -> ScenarioValuationResult:
        scenario = ScenarioValuationResult()

        # --- Múltiplos ---
        if not multiplos_valido:
            scenario.modelos_excluidos.append(nivel1_multiplos)
        else:
            scenario.valor_justo_multiplos = calculate_multiplos_fair_value(
                eps_ttm, per_peers
            )
            if scenario.valor_justo_multiplos is None:  # pragma: no cover - defensivo
                scenario.modelos_excluidos.append(
                    ModeloExcluido("multiplos", "per_peers_no_disponible")
                )

        # --- Graham ---
        if not graham_valido:
            scenario.modelos_excluidos.append(nivel1_graham)
        else:
            g_escenario = g_eps + g_delta
            scenario.valor_justo_graham = calculate_graham_fair_value(
                eps_ttm, g_escenario, y
            )
            if scenario.valor_justo_graham is None:
                scenario.modelos_excluidos.append(
                    ModeloExcluido("graham", "graham_multiplicador_no_positivo")
                )

        # --- DCF ---
        if not dcf_valido:
            scenario.modelos_excluidos.append(nivel1_dcf)
        else:
            wacc_escenario = wacc_conservador + wacc_delta
            g_fcf_escenario = g_fcf + g_delta
            scenario.valor_justo_dcf = calculate_dcf_fair_value(
                fcf_historial=fcf_historial,
                wacc=wacc_escenario,
                shares_outstanding=shares_outstanding,
                g_fcf_override=g_fcf_escenario,
                periodos_por_anio=periodos_por_anio_fcf,
                fcf_base_override=fcf_base,
            )
            if scenario.valor_justo_dcf is None:
                scenario.modelos_excluidos.append(ModeloExcluido("dcf", "dcf_no_calculable"))

        valores = [
            v
            for v in (
                scenario.valor_justo_multiplos,
                scenario.valor_justo_graham,
                scenario.valor_justo_dcf,
            )
            if v is not None
        ]
        scenario.valor_justo_total = sum(valores) / len(valores) if valores else None
        return scenario

    pesimista = _escenario(peer_average.per_minimo, -delta_g, +delta_wacc)
    conservador = _escenario(peer_average.per_promedio, 0.0, 0.0)
    optimista = _escenario(peer_average.per_maximo, +delta_g, -delta_wacc)

    return ValuationScenarios(
        pesimista=pesimista,
        conservador=conservador,
        optimista=optimista,
        modelos_excluidos_base=modelos_excluidos_base,
    )


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
