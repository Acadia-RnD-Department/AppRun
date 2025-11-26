import os
import time
import json
import tempfile
import subprocess
from collections import defaultdict

# Updating config requires restart of the service
CONFIG = {
    "MakeDirectoryIfPossible": True,
    "BaseDirectory": "/home",
    "ApplicationsDirectory": "applications",
    "ProbingIntervalSeconds": 3,
    "GlobalApplicationProbeTargets": ["/applications", "/opt/applications", "/opt/aisp/sys/applications", "/opt/aisp/applications"],
    # Where to persist registry of bundle->desktop links
    "RegistryDir": "/var/lib/apprun",
    "RegistryFile": "desktop-links.json",
}

Template = """
[Desktop Entry]
Version=$Version$
Name=$Name$
Comment=$Comment$
Exec=/usr/local/sbin/apprun.sh "$BundlePath$" $Args$
Icon=$Icon.png$
Terminal=$Terminal$
Type=$Type$
Categories=$Categories$;
"""

# -------- Persistent registry utilities --------

def _registry_path() -> str:
    reg_dir = CONFIG.get("RegistryDir", "/var/lib/apprun")
    reg_file = CONFIG.get("RegistryFile", "desktop-links.json")
    os.makedirs(reg_dir, exist_ok=True)
    return os.path.join(reg_dir, reg_file)

def load_registry() -> dict:
    path = _registry_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure shape is {bundle_path: {"desktop_files": [paths...]}}
            if isinstance(data, dict):
                return data
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Warning: could not read registry {path}: {e}")
    return {}

_last_saved_registry_cache = None  # Global cache of last-saved content

def save_registry(registry: dict) -> None:
    global _last_saved_registry_cache
    path = _registry_path()

    # Serialize current state
    current_json = json.dumps(registry, indent=2, sort_keys=True)

    # Skip write if unchanged
    if _last_saved_registry_cache == current_json:
        return

    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".apprun-reg.", dir=os.path.dirname(path))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(current_json)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        _last_saved_registry_cache = current_json  # update cache after successful write
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass


def remove_file_safely(path: str) -> None:
    try:
        os.remove(path)
        print(f"Removed stale desktop file: {path}")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Warning: could not remove {path}: {e}")

def write_if_changed(path: str, content: str, mode: int = 0o644) -> None:
    # Only write if content differs; write atomically
    try:
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
        if existing == content:
            return
    except FileNotFoundError:
        pass
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".apprun-desktop.", dir=os.path.dirname(path))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        os.chmod(path, mode)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass

# -------- Core logic --------

def get_all_user_dirs(append_dir="") -> dict[str, str]:
    user_dirs = {}
    base_dir = CONFIG.get("BaseDirectory", "/home")
    for entry in os.scandir(base_dir):
        if entry.is_dir():
            user_dir = os.path.join(base_dir, entry.name, append_dir)
            if os.path.exists(user_dir):
                user_dirs[entry.name] = user_dir
            else:
                if CONFIG.get("MakeDirectoryIfPossible", False):
                    try:
                        os.makedirs(user_dir, exist_ok=True)
                        # Change ownership to the user
                        uid = os.stat(os.path.join(base_dir, entry.name)).st_uid
                        gid = os.stat(os.path.join(base_dir, entry.name)).st_gid
                        os.chown(user_dir, uid, gid)
                        user_dirs[entry.name] = user_dir
                    except Exception as e:
                        print(f"Could not create directory {user_dir}: {e}")
    return user_dirs

def generate_desktop_entry(property_dict: dict[str, str]) -> str:
    desktop_entry_content = Template
    for key, value in property_dict.items():
        placeholder = f"${key}$"
        desktop_entry_content = desktop_entry_content.replace(placeholder, value)
    # Replace any still-unset placeholders with empty strings
    # (prevents literal $Key$ leakage)
    # Identify placeholders in Template by dollar markers
    # for frag in ["Version", "Name", "Comment", "BundlePath", "Icon", "Terminal", "Type", "Categories"]:
    #     desktop_entry_content = desktop_entry_content.replace(f"${frag}$", "")
    # return desktop_entry_content
    fallbacks = {
        "Version": "1.0",
        "Comment": "",
        "Args": "",
        "Icon.png": "/usr/local/AppRun/unknown-app-icon.png",
        "Terminal": "false",
        "Type": "Application",
        "Categories": "Utility"
    }
    for frag, default_value in fallbacks.items():
        desktop_entry_content = desktop_entry_content.replace(f"${frag}$", default_value)
    return desktop_entry_content

def build_property_dict(apprun_path: str) -> dict[str, str] | None:
    """Return dict of properties if bundle is valid, else None."""
    result = subprocess.run(
        ["/usr/local/sbin/apprunutil.sh", "BundleInfo", apprun_path],
        capture_output=True,
        text=True
    )
    # Proceed only if exit code is 0 (valid bundle)
    if result.returncode != 0:
        return None

    props: dict[str, str] = {"BundlePath": apprun_path}

    desktop_link_path = os.path.join(apprun_path, "AppRunMeta", "DesktopLink")
    if not os.path.isdir(desktop_link_path):
        # Valid bundle, but no DesktopLink; caller will treat as 0 links
        return props

    for prop_entry in os.scandir(desktop_link_path):
        if prop_entry.is_file():
            prop_name = prop_entry.name
            try:
                with open(prop_entry.path, "r", encoding="utf-8") as f:
                    prop_value = f.read().strip()
                    props[prop_name] = prop_value
            except UnicodeDecodeError:
                # Binary file, use path
                props[prop_name] = prop_entry.path
    return props

def ensure_user_applications_dir(username: str) -> str | None:
    user_apps = os.path.join("/home", username, ".local", "share", "applications")
    try:
        os.makedirs(user_apps, exist_ok=True)
        # Make sure ownership is correct
        uid = os.stat(os.path.join("/home", username)).st_uid
        gid = os.stat(os.path.join("/home", username)).st_gid
        os.chown(user_apps, uid, gid)
        return user_apps
    except Exception as e:
        print(f"Could not ensure applications dir for {username}: {e}")
        return None

def main():
    while True:
        registry = load_registry()
        current_links: dict[str, set[str]] = defaultdict(set)  # bundle_path -> {desktop_paths}
        observed_bundles: set[str] = set()  # bundles that still exist (valid BundleInfo)

        # -------- System-wide targets -> /usr/share/applications --------
        for target in CONFIG.get("GlobalApplicationProbeTargets", []):
            if not os.path.isdir(target):
                continue
            for entry in os.scandir(target):
                if not entry.is_dir():
                    continue
                apprun_path = entry.path
                props = build_property_dict(apprun_path)
                if props is None:
                    # not a valid bundle
                    continue
                observed_bundles.add(apprun_path)

                # If no Name, skip creating a .desktop file but still record 0 links for cleanup
                if "Name" in props:
                    desktop_entry_content = generate_desktop_entry(props)
                    dest_dir = "/usr/share/applications"
                    os.makedirs(dest_dir, exist_ok=True)
                    desktop_file_path = os.path.join(dest_dir, f"{props.get('Name', 'App')}.desktop")
                    write_if_changed(desktop_file_path, desktop_entry_content, mode=0o644)
                    current_links[apprun_path].add(desktop_file_path)

        # -------- Per-user targets -> ~/.local/share/applications --------
        user_apprun_dirs: dict[str, str] = get_all_user_dirs(CONFIG.get("ApplicationsDirectory", "Applications"))
        for username, apprun_dir in user_apprun_dirs.items():
            for entry in os.scandir(apprun_dir):
                if not entry.is_dir():
                    continue
                apprun_path = entry.path
                props = build_property_dict(apprun_path)
                if props is None:
                    continue
                observed_bundles.add(apprun_path)

                if "Name" in props:
                    desktop_entry_content = generate_desktop_entry(props)
                    user_apps_dir = ensure_user_applications_dir(username)
                    if not user_apps_dir:
                        continue
                    desktop_file_path = os.path.join(user_apps_dir, f"{props.get('Name', 'App')}.desktop")
                    write_if_changed(desktop_file_path, desktop_entry_content, mode=0o644)
                    # Ensure ownership of the file is the user
                    try:
                        uid = os.stat(os.path.join("/home", username)).st_uid
                        gid = os.stat(os.path.join("/home", username)).st_gid
                        os.chown(desktop_file_path, uid, gid)
                    except Exception as e:
                        print(f"Warning: could not chown {desktop_file_path}: {e}")
                    current_links[apprun_path].add(desktop_file_path)

        # -------- Cleanup & persist registry --------
        # 1) For bundles still present, remove any previously linked .desktop files that are no longer desired.
        for bundle, links_now in current_links.items():
            prev = set(registry.get(bundle, {}).get("desktop_files", []))
            stale = prev - links_now
            for path in stale:
                remove_file_safely(path)
            registry[bundle] = {"desktop_files": sorted(links_now)}

        # 2) For bundles that disappeared entirely, remove all their linked .desktop files.
        previously_known_bundles = set(registry.keys())
        removed_bundles = previously_known_bundles - observed_bundles
        for bundle in removed_bundles:
            for path in registry.get(bundle, {}).get("desktop_files", []):
                remove_file_safely(path)
            registry.pop(bundle, None)

        # 3) Save registry atomically
        save_registry(registry)

        time.sleep(CONFIG.get("ProbingIntervalSeconds", 3))

if __name__ == "__main__":
    main()
