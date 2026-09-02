---
name: analisis-fundamental-avanzado
description: Aplica 5 modelos cuantitativos de análisis fundamental (Altman Z-Score, Piotroski F-Score, Beneish M-Score, Magic Formula de Greenblatt, y un scoring de factores estilo AQR) a una acción individual con estados financieros. Úsalo cuando el usuario pida "analizame esta acción como inversionista", puntaje de calidad/riesgo de quiebra/manipulación contable, o comparar varias acciones con un ranking cuantitativo. NO aplica a criptomonedas, ETFs, fondos indexados ni activos sin estados financieros propios — solo empresas individuales con balance/estado de resultados/flujo de caja.
---

# Análisis fundamental avanzado — 5 modelos cuantitativos

Origen: construida para InvestBot (bot de análisis fundamental) y para análisis puntual de portafolios personales — mismo estilo de código que `src/investbot/valuation.py`/`rules.py`: funciones puras, manejo defensivo de `None`/datos faltantes, nunca inventar un número que falte (reportar "no disponible", no estimarlo).

## Cuándo NO aplica

Antes de calcular nada, confirmá que el activo es una **empresa individual con estados financieros propios**:
- ❌ Bitcoin, ETH, cualquier criptomoneda — no tienen balance ni estado de resultados.
- ❌ ETFs, fondos indexados, fondos mutuos (ej. CSPX, VXUS, un "Fondo de acciones dinámico") — son canastas de muchas empresas, no se les calcula un Z-Score individual. Como máximo, se puede promediar/ponderar los scores de sus principales posiciones si el usuario lo pide explícitamente, dejando claro que es una aproximación.
- ✅ Acciones individuales listadas (EEUU, Colombia, cualquier bolsa) con balance general, estado de resultados y flujo de caja disponibles — sí aplica.

## Datos necesarios por empresa (2 años fiscales, más reciente + anterior)

De balance general: activos totales, pasivos totales, activos corrientes, pasivos corrientes, utilidades retenidas, patrimonio, deuda de largo plazo, cuentas por cobrar, PP&E (propiedad/planta/equipo) bruto.
De estado de resultados: ingresos (ventas), costo de ventas, EBIT, utilidad neta, gastos SG&A, depreciación.
De flujo de caja: flujo de caja operativo (CFO).
De mercado: precio de la acción, acciones en circulación (para capitalización de mercado), acciones en circulación del año anterior (para detectar dilución).

Fuentes públicas gratuitas sin API key: `stockanalysis.com/stocks/<TICKER>/financials/` (EEUU, resumen limpio de 2+ años), SEC EDGAR (`sec.gov/cgi-bin/browse-edgar`, 10-K/10-Q oficiales, EEUU únicamente), `macrotrends.net/stocks/charts/<TICKER>` (histórico largo). Para acciones colombianas (ej. Terpel, ISA), la Superintendencia Financiera de Colombia o la propia página de relación con inversionistas de la empresa — los agregadores gringos casi nunca las cubren.

---

## 1. Altman Z-Score — riesgo de quiebra

```
Z = 1.2·A + 1.4·B + 3.3·C + 0.6·D + 1.0·E

A = Capital de trabajo / Activos totales       (Capital de trabajo = Activos corrientes − Pasivos corrientes)
B = Utilidades retenidas / Activos totales
C = EBIT / Activos totales
D = Valor de mercado del patrimonio / Pasivos totales
E = Ventas / Activos totales
```

**Interpretación (empresas manufactureras/industriales — la fórmula original):**
- Z > 2.99 → zona segura, riesgo de quiebra bajo.
- 1.81 < Z < 2.99 → zona gris, monitorear.
- Z < 1.81 → zona de riesgo, señal de alerta de quiebra.

**Nota obligatoria al reportar:** para empresas de servicios/tecnología (sin mucho activo fijo), Altman propuso una variante Z'' sin el factor E (ventas/activos) — si la empresa analizada es de servicios/software/plataforma digital, calculá también Z'' y aclarálo, porque la fórmula original (pensada para manufactura) puede subestimar la solidez de empresas asset-light.

```
Z'' = 6.56·A + 3.26·B + 6.72·C + 1.05·D
Z'' > 2.6 → seguro | 1.1–2.6 → gris | < 1.1 → riesgo
```

---

## 2. Piotroski F-Score — fortaleza fundamental (0 a 9 puntos)

9 criterios binarios (1 punto si se cumple, 0 si no), comparando el año más reciente contra el anterior:

**Rentabilidad (4 puntos):**
1. ROA > 0 (utilidad neta positiva)
2. Flujo de caja operativo (CFO) > 0
3. ROA del año actual > ROA del año anterior
4. CFO > Utilidad neta (calidad de las ganancias — el efectivo real supera la utilidad contable)

**Apalancamiento/liquidez (3 puntos):**
5. Deuda de largo plazo / Activos totales del año actual < la del año anterior (bajó el apalancamiento)
6. Razón corriente (Activos corrientes / Pasivos corrientes) del año actual > la del año anterior
7. No emitió acciones nuevas (acciones en circulación del año actual ≤ las del año anterior — no hubo dilución)

**Eficiencia operativa (2 puntos):**
8. Margen bruto del año actual > el del año anterior
9. Rotación de activos (Ventas / Activos totales) del año actual > la del año anterior

**Interpretación:** 8-9 = empresa fundamentalmente muy fuerte. 0-2 = fundamentalmente débil, señal de alerta. El score en sí no dice "barata" o "cara" — solo mide salud del negocio, se combina con valoración (ver Magic Formula abajo).

---

## 3. Beneish M-Score — probabilidad de manipulación contable

8 variables, cada una comparando año actual (t) contra año anterior (t-1):

```
DSRI = (CxC_t / Ventas_t) / (CxC_t-1 / Ventas_t-1)                    -- días de cobro, sube = alerta
GMI  = Margen_bruto_t-1 / Margen_bruto_t                              -- deterioro de margen, sube = alerta
AQI  = (1 − (Activos_corrientes_t + PPE_t) / Activos_totales_t) /
       (1 − (Activos_corrientes_t-1 + PPE_t-1) / Activos_totales_t-1) -- calidad de activos
SGI  = Ventas_t / Ventas_t-1                                          -- crecimiento de ventas, muy alto = alerta
DEPI = (Depreciación_t-1 / (Depreciación_t-1 + PPE_t-1)) /
       (Depreciación_t / (Depreciación_t + PPE_t))                    -- tasa de depreciación bajando = alerta
SGAI = (SG&A_t / Ventas_t) / (SG&A_t-1 / Ventas_t-1)                  -- gastos administrativos
LVGI = ((Pasivos_totales_t) / Activos_totales_t) /
       ((Pasivos_totales_t-1) / Activos_totales_t-1)                  -- apalancamiento subiendo
TATA = (Utilidad_neta_t − CFO_t) / Activos_totales_t                  -- accruals totales, el más importante

M = -4.84 + 0.92·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI
    + 0.115·DEPI - 0.172·SGAI + 4.679·TATA - 0.327·LVGI
```

**Interpretación:** M > -1.78 → probabilidad alta de manipulación contable, señal de alerta seria. M < -2.22 → poco probable. Zona intermedia (-2.22 a -1.78) → ambigua, revisar manualmente.

**Nunca reportar esto como acusación directa** — es una señal estadística de probabilidad, no una prueba. Redactar como "el M-Score sugiere revisar con más cuidado los estados financieros de X" cuando dé alto, no "la empresa está manipulando sus cuentas".

---

## 4. Magic Formula de Joel Greenblatt — ranking valor + calidad

Dos métricas, cada empresa se rankea en ambas y se suman los rankings (menor suma = mejor puesto en la Magic Formula):

```
Rentabilidad sobre el capital invertido (ROIC) = EBIT / (Capital de trabajo neto + Activos fijos netos)
Rendimiento de las ganancias (Earnings Yield) = EBIT / Valor de empresa (EV)

EV (Enterprise Value) = Capitalización de mercado + Deuda total − Efectivo y equivalentes
```

**Cómo aplicarlo con un portafolio de varias empresas:** rankear cada empresa de 1 a N por ROIC (1 = mejor), rankear de 1 a N por Earnings Yield (1 = mejor), sumar los 2 rankings — la empresa con la suma más baja es la "mejor" según Greenblatt (negocio de alta calidad comprado barato). Con menos de ~5-6 empresas el ranking pierde utilidad estadística — decirlo explícitamente si el portafolio analizado es chico.

---

## 5. Scoring de factores estilo fondos quant (AQR/factor investing)

No es un score único con fórmula fija como los anteriores — es una evaluación de 4 factores clásicos, cada uno con una métrica simple y una interpretación cualitativa (alto/medio/bajo relativo a las otras empresas del mismo análisis, no a un benchmark externo salvo que el usuario lo pida):

| Factor | Métrica simple | Qué mide |
|---|---|---|
| **Value** | Earnings Yield (EBIT/EV) o P/E inverso | ¿Está barata respecto a sus ganancias? |
| **Quality** | ROE + margen bruto + Piotroski F-Score | ¿Es un negocio rentable y sólido? |
| **Momentum** | Retorno de precio de los últimos 6-12 meses | ¿Viene subiendo o bajando? |
| **Low volatility** | Beta (si está disponible) o desviación estándar de retornos mensuales | ¿Qué tan volátil es comparada con el resto? |

Reportar cada empresa con un perfil de 4 letras (ej. "Value alto / Quality alto / Momentum bajo / Low-vol medio"), sin inventar un número compuesto único — combinar factores en un solo score es una decisión de diseño de cada fondo (AQR, MSCI, etc. lo hacen distinto), no hay un estándar universal a replicar sin más contexto del usuario sobre cómo quiere ponderar cada factor.

---

## MSCI — uso como referencia, no como modelo de cálculo

MSCI no es un modelo de scoring (es un proveedor de índices/clasificación) — usarlo así:
- **Clasificación GICS** (sector/industria) de cada empresa, para poder comparar dentro del mismo sector, no entre sectores distintos (comparar el Piotroski de una petrolera contra un banco no dice mucho).
- Si el usuario tiene acceso a datos de un índice MSCI relevante (ej. MSCI Emerging Markets para las acciones colombianas, MSCI USA para las de EEUU), se puede citar el retorno del índice como benchmark de comparación — nunca inventar el valor del índice, buscarlo o pedirlo.

---

## Formato de reporte recomendado (por empresa)

```
TICKER — Nombre
Altman Z-Score: X.XX (zona: segura/gris/riesgo) [Z'' si aplica: X.XX]
Piotroski F-Score: X/9
Beneish M-Score: -X.XX (probabilidad de manipulación: baja/ambigua/alta)
Magic Formula: ROIC XX% (ranking N de M) | Earnings Yield XX% (ranking N de M)
Factores: Value [alto/medio/bajo] · Quality [alto/medio/bajo] · Momentum [alto/medio/bajo] · Low-vol [alto/medio/bajo]
Fuente de los datos: [sitio/documento exacto usado, con fecha del estado financiero]
```

**Regla dura, no negociable:** cualquier dato financiero que no se pueda encontrar de una fuente verificable se reporta como "no disponible", nunca se estima ni se inventa — mismo principio que ya sigue InvestBot ("nunca alucinar un número financiero"). Si falta un dato necesario para un modelo completo, se reporta el modelo como "no calculable con los datos disponibles" en vez de rellenar con un supuesto.
