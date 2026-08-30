"""Tests for optional system metric probes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import shutil

from ui import widgets


def _metrics_without_starting_thread() -> widgets._SysMetrics:
    return widgets._SysMetrics.__new__(widgets._SysMetrics)


def test_gpu_probe_returns_sentinel_when_nvidia_smi_is_missing() -> None:
    metrics = _metrics_without_starting_thread()
    with patch.object(shutil, "which", return_value=None), patch.object(
        widgets, "_OS", "Windows"
    ):
        assert metrics._get_gpu() == -1.0


def test_gpu_probe_averages_valid_nvidia_values() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(returncode=0, stdout="10\n30\n", stderr="")
    with patch.object(shutil, "which", return_value="nvidia-smi"), patch.object(
        widgets, "_quiet_run", return_value=result
    ):
        assert metrics._get_gpu() == 20.0


def test_gpu_probe_ignores_malformed_nvidia_output() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(returncode=0, stdout="GPU busy\n", stderr="")
    with patch.object(shutil, "which", return_value="nvidia-smi"), patch.object(
        widgets, "_quiet_run", return_value=result
    ):
        assert metrics._get_gpu() == -1.0


def test_linux_gpu_probe_skips_missing_optional_commands() -> None:
    metrics = _metrics_without_starting_thread()
    with patch.object(widgets, "_OS", "Linux"), patch.object(
        shutil, "which", return_value=None
    ), patch.object(widgets, "_quiet_run") as quiet_run:
        assert metrics._get_gpu() == -1.0
        quiet_run.assert_not_called()


def test_linux_gpu_probe_reads_rocm_usage() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(returncode=0, stdout="device,42%\n", stderr="")

    def which(name: str):
        return "rocm-smi" if name == "rocm-smi" else None

    with patch.object(widgets, "_OS", "Linux"), patch.object(
        shutil, "which", side_effect=which
    ), patch.object(widgets, "_quiet_run", return_value=result):
        assert metrics._get_gpu() == 42.0


def test_linux_gpu_probe_returns_sentinel_for_malformed_rocm_output() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(returncode=0, stdout="device,unknown\n", stderr="")
    with patch.object(widgets, "_OS", "Linux"), patch.object(
        shutil, "which", side_effect=lambda name: "rocm-smi" if name == "rocm-smi" else None
    ), patch.object(widgets, "_quiet_run", return_value=result):
        assert metrics._get_gpu() == -1.0


def test_linux_gpu_probe_reads_intel_usage() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(returncode=0, stdout='{"Render/3D": {"busy": 37.5}}', stderr="")

    def which(name: str):
        return "intel_gpu_top" if name == "intel_gpu_top" else None

    with patch.object(widgets, "_OS", "Linux"), patch.object(
        shutil, "which", side_effect=which
    ), patch.object(widgets, "_quiet_run", return_value=result):
        assert metrics._get_gpu() == 37.5


def test_linux_gpu_probe_returns_sentinel_for_malformed_intel_output() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(returncode=0, stdout="not json", stderr="")
    with patch.object(widgets, "_OS", "Linux"), patch.object(
        shutil, "which", side_effect=lambda name: "intel_gpu_top" if name == "intel_gpu_top" else None
    ), patch.object(widgets, "_quiet_run", return_value=result):
        assert metrics._get_gpu() == -1.0


def test_macos_gpu_probe_skips_missing_powermetrics() -> None:
    metrics = _metrics_without_starting_thread()
    with patch.object(widgets, "_OS", "Darwin"), patch.object(
        shutil, "which", return_value=None
    ), patch.object(widgets, "_quiet_run") as quiet_run:
        assert metrics._get_gpu() == -1.0
        quiet_run.assert_not_called()


def test_macos_gpu_probe_parses_powermetrics_output() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(returncode=0, stdout="GPU Active: 44.5%\n", stderr="")

    def which(name: str):
        return {"sudo": "C:/sudo", "powermetrics": "C:/powermetrics"}.get(name)

    with patch.object(widgets, "_OS", "Darwin"), patch.object(
        shutil, "which", side_effect=which
    ), patch.object(widgets, "_quiet_run", return_value=result) as quiet_run:
        assert metrics._get_gpu() == 44.5
        assert quiet_run.call_args.args[0][:3] == ["C:/sudo", "-n", "C:/powermetrics"]


def test_macos_gpu_probe_returns_sentinel_for_malformed_powermetrics() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(returncode=0, stdout="GPU Active: unknown\n", stderr="")

    with patch.object(widgets, "_OS", "Darwin"), patch.object(
        shutil, "which", side_effect=lambda name: {"sudo": "C:/sudo", "powermetrics": "C:/powermetrics"}.get(name)
    ), patch.object(widgets, "_quiet_run", return_value=result):
        assert metrics._get_gpu() == -1.0


def test_temperature_probe_returns_sentinel_when_psutil_has_no_temperature_api() -> None:
    metrics = _metrics_without_starting_thread()
    with patch.object(widgets.psutil, "sensors_temperatures", None, create=True), patch.object(
        widgets, "_OS", "Linux"
    ):
        assert metrics._get_temp() == -1.0


def test_windows_temperature_probe_skips_noise_and_parses_numeric_line() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(
        returncode=0,
        stdout="[env] LIVEKIT_URL loaded\r\n\n3015\r\n",
        stderr="",
    )
    with patch.object(widgets, "_OS", "Windows"), patch.object(
        widgets.psutil, "sensors_temperatures", None, create=True
    ), patch.object(widgets, "_quiet_run", return_value=result):
        assert metrics._get_temp() == pytest.approx(28.35)


def test_windows_temperature_probe_returns_sentinel_for_non_numeric_output() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(returncode=0, stdout="[env] LIVEKIT_URL loaded\n", stderr="")
    with patch.object(widgets, "_OS", "Windows"), patch.object(
        widgets.psutil, "sensors_temperatures", None, create=True
    ), patch.object(widgets, "_quiet_run", return_value=result):
        assert metrics._get_temp() == -1.0


def test_windows_temperature_probe_skips_missing_powershell() -> None:
    metrics = _metrics_without_starting_thread()
    with patch.object(widgets, "_OS", "Windows"), patch.object(
        widgets.psutil, "sensors_temperatures", None, create=True
    ), patch("shutil.which", return_value=None), patch.object(
        widgets, "_quiet_run"
    ) as quiet_run:
        assert metrics._get_temp() == -1.0
        quiet_run.assert_not_called()


def test_windows_temperature_probe_uses_discovered_powershell_executable() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(returncode=0, stdout="3015\n", stderr="")
    with patch.object(widgets, "_OS", "Windows"), patch.object(
        widgets.psutil, "sensors_temperatures", None, create=True
    ), patch("shutil.which", side_effect=lambda name: "C:/PowerShell.exe" if name == "powershell.exe" else None), patch.object(
        widgets, "_quiet_run", return_value=result
    ) as quiet_run:
        assert metrics._get_temp() == pytest.approx(28.35)
        assert quiet_run.call_args.args[0][0] == "C:/PowerShell.exe"


def test_macos_temperature_probe_skips_missing_optional_command() -> None:
    metrics = _metrics_without_starting_thread()
    with patch.object(widgets, "_OS", "Darwin"), patch.object(
        widgets.psutil, "sensors_temperatures", None, create=True
    ), patch("shutil.which", return_value=None), patch.object(widgets, "_quiet_run") as quiet_run:
        assert metrics._get_temp() == -1.0
        quiet_run.assert_not_called()


def test_macos_temperature_probe_parses_valid_output() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(returncode=0, stdout="52.5°C\n", stderr="")
    with patch.object(widgets, "_OS", "Darwin"), patch.object(
        widgets.psutil, "sensors_temperatures", None, create=True
    ), patch("shutil.which", return_value="C:/osx-cpu-temp"), patch.object(
        widgets, "_quiet_run", return_value=result
    ):
        assert metrics._get_temp() == 52.5


def test_macos_temperature_probe_returns_sentinel_for_malformed_output() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(returncode=0, stdout="temperature unavailable\n", stderr="")
    with patch.object(widgets, "_OS", "Darwin"), patch.object(
        widgets.psutil, "sensors_temperatures", None, create=True
    ), patch("shutil.which", return_value="C:/osx-cpu-temp"), patch.object(
        widgets, "_quiet_run", return_value=result
    ):
        assert metrics._get_temp() == -1.0


def test_macos_temperature_probe_rejects_number_in_error_output() -> None:
    metrics = _metrics_without_starting_thread()
    result = SimpleNamespace(returncode=0, stdout="sensor error 52.5\n", stderr="")
    with patch.object(widgets, "_OS", "Darwin"), patch.object(
        widgets.psutil, "sensors_temperatures", None, create=True
    ), patch("shutil.which", return_value="C:/osx-cpu-temp"), patch.object(
        widgets, "_quiet_run", return_value=result
    ):
        assert metrics._get_temp() == -1.0


def test_gpu_probe_rejects_nonfinite_or_out_of_range_values() -> None:
    metrics = _metrics_without_starting_thread()
    for output in ("nan\n", "inf\n", "101\n"):
        result = SimpleNamespace(returncode=0, stdout=output, stderr="")
        with patch.object(shutil, "which", return_value="nvidia-smi"), patch.object(
            widgets, "_quiet_run", return_value=result
        ):
            assert metrics._get_gpu() == -1.0


def test_temperature_probe_rejects_nonfinite_sensor_values() -> None:
    metrics = _metrics_without_starting_thread()
    sensor_values = [SimpleNamespace(current=float("nan")), SimpleNamespace(current="not-a-number")]
    for sensor in sensor_values:
        with patch.object(
            widgets.psutil,
            "sensors_temperatures",
            return_value={"coretemp": [sensor]},
            create=True,
        ), patch.object(widgets, "_OS", "Linux"), patch("shutil.which", return_value=None):
            assert metrics._get_temp() == -1.0


def test_temperature_probe_rejects_implausible_sensor_values() -> None:
    metrics = _metrics_without_starting_thread()
    sensor = SimpleNamespace(current=250.0)
    with patch.object(
        widgets.psutil,
        "sensors_temperatures",
        return_value={"coretemp": [sensor]},
        create=True,
    ), patch.object(widgets, "_OS", "Linux"), patch("shutil.which", return_value=None):
        assert metrics._get_temp() == -1.0
