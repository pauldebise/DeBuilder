"""Audit materiel (Hardware Awareness).

Audite les ressources de la machine hote (RAM, GPU, CPU)
pour permettre a l'agent de prendre des decisions
d'implementation autonomes et realistes.
"""

import dataclasses


@dataclasses.dataclass
class HardwareInfo:
    """Informations materielles de la machine hote."""

    cpu_cores: int
    ram_total_gb: float
    gpu_available: bool
    gpu_name: str | None
    gpu_memory_gb: float | None


def audit_hardware() -> HardwareInfo:
    """Audite les ressources materielles disponibles.

    Returns:
        HardwareInfo contenant les specifications detectees.
    """
    ...


def format_for_agent(info: HardwareInfo) -> str:
    """Formate les infos hardware pour l'agent (Markdown).

    Args:
        info: Informations materielles.

    Returns:
        Texte Markdown lisible par l'agent.
    """
    ...
