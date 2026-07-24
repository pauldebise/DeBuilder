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
from pathlib import Path

_CGROUP_V2_CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
_CGROUP_V1_CPU_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
_CGROUP_V1_CPU_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
_CGROUP_V2_MEM_MAX = Path("/sys/fs/cgroup/memory.max")
_CGROUP_V1_MEM_LIMIT = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
# cgroup v1 signale "illimite" par une valeur sentinelle proche de
# 2**63 (arrondie a la page), tres au-dessus de toute RAM physique
# reelle : au-dela de ce seuil, on considere qu'il n'y a pas de limite.
_CGROUP_V1_UNLIMITED_THRESHOLD = 1 << 62


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

    os.cpu_count() et psutil/``/proc/meminfo`` refletent les ressources
    vues par le noyau hote, pas le quota reellement alloue au
    conteneur : sur un pod partageant un hote physique bien plus
    puissant (cgroups mal isoles ou non consultes), ils remontent les
    specs de l'hote entier. On les plafonne donc avec la limite cgroup
    du conteneur quand elle existe.

    Returns:
        HardwareInfo contenant les specifications detectees.
    """
    cpu_cores = os.cpu_count() or 1
    cgroup_cpu_limit = _cgroup_cpu_limit()
    if cgroup_cpu_limit is not None:
        cpu_cores = min(cpu_cores, max(1, round(cgroup_cpu_limit)))

    ram_total_gb = _detect_ram()
    cgroup_mem_limit_bytes = _cgroup_memory_limit_bytes()
    if cgroup_mem_limit_bytes is not None:
        ram_total_gb = min(ram_total_gb, round(cgroup_mem_limit_bytes / (1024**3), 1))

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


def _cgroup_cpu_limit() -> float | None:
    """Nombre de coeurs alloues au conteneur d'apres le cgroup.

    Returns:
        Le quota CPU (en equivalent coeurs), ou None si aucune limite
        cgroup n'est definie (pas de fichier cgroup, ou quota "max"/
        illimite).
    """
    try:
        if _CGROUP_V2_CPU_MAX.exists():
            quota_str, period_str = _CGROUP_V2_CPU_MAX.read_text().split()
            if quota_str != "max":
                return int(quota_str) / int(period_str)
            return None
    except (OSError, ValueError):
        pass

    try:
        if _CGROUP_V1_CPU_QUOTA.exists() and _CGROUP_V1_CPU_PERIOD.exists():
            quota = int(_CGROUP_V1_CPU_QUOTA.read_text().strip())
            period = int(_CGROUP_V1_CPU_PERIOD.read_text().strip())
            if quota > 0:
                return quota / period
    except (OSError, ValueError):
        pass

    return None


def _cgroup_memory_limit_bytes() -> int | None:
    """Limite memoire allouee au conteneur d'apres le cgroup.

    Returns:
        La limite en octets, ou None si aucune limite cgroup n'est
        definie (pas de fichier cgroup, limite "max", ou valeur
        sentinelle "illimite" de cgroup v1).
    """
    try:
        if _CGROUP_V2_MEM_MAX.exists():
            value = _CGROUP_V2_MEM_MAX.read_text().strip()
            if value != "max":
                return int(value)
            return None
    except (OSError, ValueError):
        pass

    try:
        if _CGROUP_V1_MEM_LIMIT.exists():
            value = int(_CGROUP_V1_MEM_LIMIT.read_text().strip())
            if value < _CGROUP_V1_UNLIMITED_THRESHOLD:
                return value
    except (OSError, ValueError):
        pass

    return None


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
