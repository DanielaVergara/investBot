"""Series estilizadas para testear el criterio de crecimiento (Parte 1,
SDD_fix_crecimiento_y_redaccion.md) y el mensaje completo de regresión de
formato (Parte 3, mismo spec). Deliberadamente NO son los números reales de
NVIDIA -- son una forma estilizada que reproduce la forma del caso real (una
caída intermedia rodeada de crecimiento fuerte) sin afirmar cifras
financieras no verificadas contra el 10-K real de la empresa. Si Daniela
provee los números reales más adelante, se agrega un fixture aparte con
esos datos exactos -- no reemplaza a este, que sigue siendo válido como
caso estilizado de regresión."""

HISTORIAL_INGRESOS_CASO_ESTILIZADO = [100_000, 200_000, 250_000, 400_000, 700_000]
# monótono creciente -- los ingresos de una empresa que llega a este
# análisis casi nunca caen año a año, a diferencia de las utilidades.

HISTORIAL_UTILIDADES_CASO_ESTILIZADO = [100, 200, 150, 300, 500]
# una caída intermedia (tercer año) rodeada de crecimiento fuerte -- la
# forma exacta del bug reportado por Daniela con NVIDIA (FY2023 con una
# baja de utilidades antes de la explosión de ganancias por IA).
