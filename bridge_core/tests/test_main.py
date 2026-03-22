import asyncio
from unittest.mock import MagicMock, patch

import pytest
from bridge_core.core import EventBus, SourceRegistry
from bridge_core.main import _schedule_adapter_startup, register_ingress_adapters


@pytest.mark.asyncio
async def test_register_ingress_adapters_linux() -> None:
    event_bus = EventBus()
    registry = SourceRegistry(event_bus)

    with (
        patch("bridge_core.main.LinuxAudioAdapter") as linux_audio_cls,
        patch("bridge_core.main.LinuxBluetoothAdapter") as linux_bluetooth_cls,
        patch("bridge_core.main.WindowsAudioAdapter") as windows_audio_cls,
        patch("bridge_core.main.SyntheticAdapter") as synthetic_cls,
        patch("bridge_core.main._schedule_adapter_startup") as schedule_startup,
    ):
        synthetic_adapter = MagicMock()
        synthetic_adapter.id.return_value = "synthetic-adapter"
        synthetic_adapter.platform.return_value = "any"
        synthetic_adapter.capabilities.return_value = MagicMock()
        synthetic_adapter.list_sources.return_value = []
        synthetic_cls.return_value = synthetic_adapter

        linux_audio_adapter = MagicMock()
        linux_audio_adapter.id.return_value = "linux-audio-adapter"
        linux_audio_adapter.platform.return_value = "linux"
        linux_audio_adapter.capabilities.return_value = MagicMock()
        linux_audio_adapter.list_sources.return_value = []
        linux_audio_cls.return_value = linux_audio_adapter

        linux_bluetooth_adapter = MagicMock()
        linux_bluetooth_adapter.id.return_value = "linux-bluetooth-adapter"
        linux_bluetooth_adapter.platform.return_value = "linux"
        linux_bluetooth_adapter.capabilities.return_value = MagicMock()
        linux_bluetooth_adapter.list_sources.return_value = []
        linux_bluetooth_adapter.on_startup.return_value = None
        linux_bluetooth_cls.return_value = linux_bluetooth_adapter

        register_ingress_adapters(registry, event_bus, config_store=None, host_platform="Linux")

        linux_audio_cls.assert_called_once()
        args, kwargs = linux_audio_cls.call_args
        assert args == (event_bus,)
        assert kwargs["metrics"] is None
        assert "runner" in kwargs

        linux_bluetooth_cls.assert_called_once()
        args, kwargs = linux_bluetooth_cls.call_args
        assert args == (event_bus,)
        assert kwargs["config_store"] is None
        assert kwargs["metrics"] is None
        assert "runner" in kwargs
        windows_audio_cls.assert_not_called()
        schedule_startup.assert_called_once_with(None, "linux_bluetooth_adapter")


@pytest.mark.asyncio
async def test_register_ingress_adapters_windows() -> None:
    event_bus = EventBus()
    registry = SourceRegistry(event_bus)

    with (
        patch("bridge_core.main.LinuxAudioAdapter") as linux_audio_cls,
        patch("bridge_core.main.LinuxBluetoothAdapter") as linux_bluetooth_cls,
        patch("bridge_core.main.WindowsAudioAdapter") as windows_audio_cls,
        patch("bridge_core.main.SyntheticAdapter") as synthetic_cls,
    ):
        synthetic_adapter = MagicMock()
        synthetic_adapter.id.return_value = "synthetic-adapter"
        synthetic_adapter.platform.return_value = "any"
        synthetic_adapter.capabilities.return_value = MagicMock()
        synthetic_adapter.list_sources.return_value = []
        synthetic_cls.return_value = synthetic_adapter

        windows_audio_adapter = MagicMock()
        windows_audio_adapter.id.return_value = "windows-audio-adapter"
        windows_audio_adapter.platform.return_value = "windows"
        windows_audio_adapter.capabilities.return_value = MagicMock()
        windows_audio_adapter.list_sources.return_value = []
        windows_audio_cls.return_value = windows_audio_adapter

        register_ingress_adapters(registry, event_bus, host_platform="Windows")

        windows_audio_cls.assert_called_once_with()
        linux_audio_cls.assert_not_called()
        linux_bluetooth_cls.assert_not_called()


@pytest.mark.asyncio
async def test_schedule_adapter_startup_schedules_coroutine() -> None:
    async def startup() -> None:
        return None

    coro = startup()
    task = _schedule_adapter_startup(coro, "adapter")
    assert isinstance(task, asyncio.Task)
    await task


def test_schedule_adapter_startup_tolerates_none() -> None:
    assert _schedule_adapter_startup(None, "adapter") is None


@pytest.mark.asyncio
async def test_schedule_adapter_startup_returns_existing_future() -> None:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[object] = loop.create_future()
    future.set_result(None)

    assert _schedule_adapter_startup(future, "adapter") is future


@pytest.mark.asyncio
async def test_schedule_adapter_startup_skips_non_awaitable(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("WARNING")

    result = _schedule_adapter_startup(object(), "adapter")

    assert result is None
    assert "returned non-coroutine" in caplog.text
