import os
import re
import sys

# Define package dependency rules
# format: package_module_name -> list of allowed internal package module dependencies
# Note: we use the module names as imported in code.
ALLOWED_DEPENDENCIES = {
    "bridge_core": [
        "ingress_sdk",
        "shared",
        "renderer_sonos",
        "adapter_synthetic",
        "adapter_linux_audio",
        "adapter_linux_bluetooth",
        "adapter_windows_audio",
    ],
    "ingress_sdk": ["shared"],
    "shared": [],
    "renderer_sonos": ["ingress_sdk", "shared", "bridge_core"],
    "adapter_synthetic": ["ingress_sdk", "shared"],
    "adapter_linux_audio": ["ingress_sdk", "shared"],
    "adapter_linux_bluetooth": ["ingress_sdk", "shared", "bridge_core"],
    "adapter_windows_audio": ["ingress_sdk", "shared"],
}

# Map of package module names to their source directory
PACKAGE_MAP = {
    "bridge_core": "bridge_core/src/bridge_core",
    "ingress_sdk": "ingress_sdk/src/ingress_sdk",
    "shared": "shared/src/shared",
    "renderer_sonos": "renderer_sonos/src/renderer_sonos",
    "adapter_synthetic": "adapters/synthetic/src/adapter_synthetic",
    "adapter_linux_audio": "adapters/linux_audio/src/adapter_linux_audio",
    "adapter_linux_bluetooth": "adapters/linux_bluetooth/src/adapter_linux_bluetooth",
    "adapter_windows_audio": "adapters/windows_audio/src/adapter_windows_audio",
}


def get_imports_from_file(filepath: str) -> set[str]:
    imports = set()
    with open(filepath, encoding="utf-8") as f:
        for line in f:
            # Simple regex for imports
            match = re.match(r"^(?:from|import)\s+([a-zA-Z0-9_]+)", line)
            if match:
                imports.add(match.group(1))
    return imports


def check_boundaries() -> None:
    print("Checking package boundaries...")
    errors = 0

    for module_name, src_dir in PACKAGE_MAP.items():
        if not os.path.exists(src_dir):
            print(f"Warning: Directory {src_dir} not found, skipping.")
            continue

        allowed = ALLOWED_DEPENDENCIES.get(module_name, [])

        for root, _, files in os.walk(src_dir):
            for file in files:
                if file.endswith(".py"):
                    filepath = os.path.join(root, file)
                    imports = get_imports_from_file(filepath)

                    for imp in imports:
                        # If the import is one of our internal packages
                        if imp in PACKAGE_MAP and imp != module_name:
                            # And it's not in the allowed list for this package
                            if imp not in allowed:
                                print(f"Illegal import in {filepath}: '{imp}' is not in allowed dependencies for '{module_name}'")
                                errors += 1

    if errors > 0:
        print(f"Found {errors} boundary violations.")
        sys.exit(1)
    else:
        print("No boundary violations detected.")


if __name__ == "__main__":
    check_boundaries()
