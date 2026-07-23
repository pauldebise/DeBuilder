"""Audit materiel (Hardware Awareness).

Audite les ressources de la machine hote (RAM, GPU, CPU)
pour permettre a l'agent de prendre des decisions
d'implementation autonomes et realistes.
"""

import dataclasses
import os
import platform
import shutil
import subprocess


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
    cpu_cores = os.cpu_count() or 1

    ram_total_gb = _detect_ram()

    gpu_info = _detect_gpu()

    return HardwareInfo(
        cpu_cores=cpu_cores,
        ram_total_gb=ram_total_gb,
        gpu_available=gpu_info is not None,
        gpu_name=gpu_info[0] if gpu_info else None,
        gpu_memory_gb=gpu_info[1] if gpu_info else None,
    )


def format_for_agent(info: HardwareInfo) -> str:
    """Formate les infos hardware pour l'agent (Markdown).

    Args:
        info: Informations materielles.

    Returns:
        Texte Markdown lisible par l'agent.
    """
    lines = [
        "- **CPU** : {} cœurs logiques".format(info.cpu_cores),
        "- **RAM** : {:.1f} Go totale".format(info.ram_total_gb),
    ]
    if info.gpu_available:
        lines.append(
            "- **GPU** : {} ({:.1f} Go VRAM)".format(
                info.gpu_name, info.gpu_memory_gb or 0
            )
        )
    else:
        lines.append("- **GPU** : Non detecte")
    return "\n".join(lines)


def _detect_ram() -> float:
    try:
        import psutil

        mem = psutil.virtual_memory()
        return round(mem.total / (1024**3), 1)
    except ImportError:
        pass

    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024**2), 1)
    except (OSError, ValueError, IndexError):
        pass

    return 0.0


def _detect_gpu() -> tuple[str, float] | None:
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            result = subprocess.run(
                [
                    nvidia_smi,
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split(",", 1)
                if len(parts) == 2:
                    name = parts[0].strip()
                    memory_mb = float(parts[1].strip().split()[0])
                    return (name, round(memory_mb / 1024, 1))
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass

    try:
        import torch

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            mem_bytes = torch.cuda.get_device_properties(0).total_memory
            return (name, round(mem_bytes / (1024**3), 1))
    except ImportError:
        pass

    return None
