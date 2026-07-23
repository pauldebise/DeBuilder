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
