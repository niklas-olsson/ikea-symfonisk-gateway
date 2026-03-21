import os
import subprocess
import sys


def main() -> None:
    print("Checking for circular imports...")
    # Packages to check
    packages = [
        "bridge_core",
        "ingress_sdk",
        "shared",
        "renderer_sonos",
        "adapters/synthetic",
        "adapters/linux_audio",
        "adapters/linux_bluetooth",
        "adapters/windows_audio",
    ]

    # Filter only existing directories
    packages = [pkg for pkg in packages if os.path.exists(pkg)]

    try:
        # Check if pylint is installed
        subprocess.run(["pylint", "--version"], capture_output=True, check=True)

        # We need to add src directories to PYTHONPATH for pylint to resolve imports correctly
        env = os.environ.copy()
        python_path = env.get("PYTHONPATH", "")
        for pkg in packages:
            src_path = os.path.abspath(os.path.join(pkg, "src"))
            if os.path.exists(src_path):
                python_path = f"{src_path}:{python_path}"
        env["PYTHONPATH"] = python_path

        # Actually, let's just point pylint at the src directories
        targets = []
        for pkg in packages:
            src_path = os.path.join(pkg, "src")
            if os.path.exists(src_path):
                # Find the package name inside src
                for item in os.listdir(src_path):
                    if os.path.isdir(os.path.join(src_path, item)) and os.path.exists(os.path.join(src_path, item, "__init__.py")):
                        targets.append(os.path.join(src_path, item))

        if not targets:
            print("No targets found for pylint check.")
            return

        result = subprocess.run(["pylint", "--disable=all", "--enable=cyclic-import"] + targets, capture_output=True, text=True, env=env)

        # pylint returns non-zero if it finds anything, but we only care about R0401
        if "R0401" in result.stdout:
            print("Circular imports detected!")
            print(result.stdout)
            sys.exit(1)
        else:
            print("No circular imports detected.")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Error running pylint: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
