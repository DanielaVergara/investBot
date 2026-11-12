"""Guarda de red para los tests (CS-16, SDD_migracion_gemini.md).

Registra cada intento de red real en `attempts` y permite afirmar al final
del test (`assert_clean`) que no hubo ninguno. Un `except Exception` del
código bajo prueba puede tragarse el `AssertionError` que lanza el parche en
el momento del intento; el registro persiste y el teardown lo detecta.
"""

from __future__ import annotations


class NetworkGuard:
    def __init__(self) -> None:
        self.attempts: list[str] = []

    def record(self, description: str) -> str:
        """Registra un intento y devuelve el mensaje para el `AssertionError`."""
        self.attempts.append(description)
        return (
            f"Intento de red real bloqueado en tests: {description}. "
            "Los tests no tocan la red: usar httpx.MockTransport."
        )

    def assert_clean(self) -> None:
        if self.attempts:
            raise AssertionError(
                f"Se detectaron {len(self.attempts)} intento(s) de red real "
                f"durante el test: {self.attempts}"
            )
