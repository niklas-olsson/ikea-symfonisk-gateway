from adapter_linux_bluetooth import LinuxBluetoothAdapter

def test_linux_bluetooth_adapter():
    adapter = LinuxBluetoothAdapter()
    assert adapter.platform() == "linux"
    caps = adapter.capabilities()
    assert caps.supports_bluetooth_audio is True
    assert caps.supports_pairing is True
