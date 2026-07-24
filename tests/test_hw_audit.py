"""Tests pour le module hw_audit.py."""

from src.utils.hw_audit import HardwareInfo, audit_hardware, format_for_agent


def test_audit_hardware_basic():
    info = audit_hardware()
    assert info.cpu_cores >= 1
    assert info.ram_total_gb > 0


def test_format_for_agent_with_gpu():
    info = HardwareInfo(
        cpu_cores=8,
        ram_total_gb=32.0,
        gpu_available=True,
        gpu_name="NVIDIA A100",
        gpu_memory_gb=40.0,
    )
    text = format_for_agent(info)
    assert "8 cœurs logiques" in text
    assert "32.0 Go totale" in text
    assert "NVIDIA A100" in text
    assert "40.0 Go VRAM" in text


def test_format_for_agent_without_gpu():
    info = HardwareInfo(
        cpu_cores=4,
        ram_total_gb=16.0,
        gpu_available=False,
        gpu_name=None,
        gpu_memory_gb=None,
    )
    text = format_for_agent(info)
    assert "Non detecte" in text


def test_audit_hardware_no_gpu(monkeypatch):
    import src.utils.hw_audit as mod

    monkeypatch.setattr(mod, "_detect_gpu", lambda: None)
    info = mod.audit_hardware()
    assert not info.gpu_available
    assert info.gpu_name is None


def test_audit_hardware_with_gpu(monkeypatch):
    import src.utils.hw_audit as mod

    monkeypatch.setattr(mod, "_detect_gpu", lambda: ("Tesla T4", 16.0))
    info = mod.audit_hardware()
    assert info.gpu_available
    assert info.gpu_name == "Tesla T4"
    assert info.gpu_memory_gb == 16.0


def test_format_for_agent_output_structure():
    info = HardwareInfo(
        cpu_cores=2,
        ram_total_gb=4.0,
        gpu_available=False,
        gpu_name=None,
        gpu_memory_gb=None,
    )
    text = format_for_agent(info)
    lines = text.split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("- **CPU**")
    assert lines[1].startswith("- **RAM**")
    assert lines[2].startswith("- **GPU**")


def test_audit_hardware_caps_cpu_with_cgroup_limit(monkeypatch):
    # Cas d'un pod a 4 vCPU partageant un hote a 500 vCPU : os.cpu_count()
    # remonte le total hote, la limite cgroup doit primer.
    import src.utils.hw_audit as mod

    monkeypatch.setattr(mod.os, "cpu_count", lambda: 500)
    monkeypatch.setattr(mod, "_cgroup_cpu_limit", lambda: 4.0)
    monkeypatch.setattr(mod, "_detect_ram", lambda: 8.0)
    monkeypatch.setattr(mod, "_cgroup_memory_limit_bytes", lambda: None)
    monkeypatch.setattr(mod, "_detect_gpu", lambda: None)

    info = mod.audit_hardware()
    assert info.cpu_cores == 4


def test_audit_hardware_caps_ram_with_cgroup_limit(monkeypatch):
    # Meme cas pour la RAM : 8 Go alloues sur un hote a 1000 Go.
    import src.utils.hw_audit as mod

    monkeypatch.setattr(mod.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(mod, "_cgroup_cpu_limit", lambda: None)
    monkeypatch.setattr(mod, "_detect_ram", lambda: 1000.0)
    monkeypatch.setattr(mod, "_cgroup_memory_limit_bytes", lambda: 8 * (1024**3))
    monkeypatch.setattr(mod, "_detect_gpu", lambda: None)

    info = mod.audit_hardware()
    assert info.ram_total_gb == 8.0


def test_audit_hardware_no_cgroup_limit_keeps_host_values(monkeypatch):
    import src.utils.hw_audit as mod

    monkeypatch.setattr(mod.os, "cpu_count", lambda: 16)
    monkeypatch.setattr(mod, "_cgroup_cpu_limit", lambda: None)
    monkeypatch.setattr(mod, "_detect_ram", lambda: 64.0)
    monkeypatch.setattr(mod, "_cgroup_memory_limit_bytes", lambda: None)
    monkeypatch.setattr(mod, "_detect_gpu", lambda: None)

    info = mod.audit_hardware()
    assert info.cpu_cores == 16
    assert info.ram_total_gb == 64.0


def test_cgroup_cpu_limit_v2_reads_quota_over_period(monkeypatch, tmp_path):
    import src.utils.hw_audit as mod

    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("400000 100000\n")
    monkeypatch.setattr(mod, "_CGROUP_V2_CPU_MAX", cpu_max)

    assert mod._cgroup_cpu_limit() == 4.0


def test_cgroup_cpu_limit_v2_max_means_unlimited(monkeypatch, tmp_path):
    import src.utils.hw_audit as mod

    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("max 100000\n")
    monkeypatch.setattr(mod, "_CGROUP_V2_CPU_MAX", cpu_max)

    assert mod._cgroup_cpu_limit() is None


def test_cgroup_memory_limit_v2_reads_bytes(monkeypatch, tmp_path):
    import src.utils.hw_audit as mod

    mem_max = tmp_path / "memory.max"
    mem_max.write_text(str(8 * (1024**3)) + "\n")
    monkeypatch.setattr(mod, "_CGROUP_V2_MEM_MAX", mem_max)

    assert mod._cgroup_memory_limit_bytes() == 8 * (1024**3)


def test_cgroup_memory_limit_v2_max_means_unlimited(monkeypatch, tmp_path):
    import src.utils.hw_audit as mod

    mem_max = tmp_path / "memory.max"
    mem_max.write_text("max\n")
    monkeypatch.setattr(mod, "_CGROUP_V2_MEM_MAX", mem_max)

    assert mod._cgroup_memory_limit_bytes() is None


def test_cgroup_memory_limit_v1_sentinel_means_unlimited(monkeypatch, tmp_path):
    import src.utils.hw_audit as mod

    monkeypatch.setattr(mod, "_CGROUP_V2_MEM_MAX", tmp_path / "absent")
    mem_limit = tmp_path / "memory.limit_in_bytes"
    mem_limit.write_text("9223372036854771712\n")
    monkeypatch.setattr(mod, "_CGROUP_V1_MEM_LIMIT", mem_limit)

    assert mod._cgroup_memory_limit_bytes() is None
