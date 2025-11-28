import os
import time
import json
import tempfile
import subprocess
import argparse
import sys
from collections import defaultdict

# Updating config requires restart of the service
CONFIG = {
    "MakeDirectoryIfPossible": True,
    "BaseDirectory": "/home",
    "ApplicationsDirectory": "applications",
    "ProbingIntervalSeconds": 3,
    "GlobalApplicationProbeTargets": ["/applications", "/opt/applications", "/opt/aisp/sys/applications",
                                      "/opt/aisp/applications"],
    "RegistryDir": "/var/lib/apprun",
    "RegistryFile": "desktop-links.json",
    # Debounce time for inotify events (seconds)
    "InotifyDebounceSeconds": 1.0
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
            if isinstance(data, dict):
                return data
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Warning: could not read registry {path}: {e}")
    return {}


_last_saved_registry_cache = None


def save_registry(registry: dict) -> None:
    global _last_saved_registry_cache
    path = _registry_path()
    current_json = json.dumps(registry, indent=2, sort_keys=True)

    if _last_saved_registry_cache == current_json:
        return

    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".apprun-reg.", dir=os.path.dirname(path))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(current_json)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        _last_saved_registry_cache = current_json
    finally:
        if os.path.exists(tmp_path):
            try:
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
    try:
        with open(path, "r", encoding="utf-8") as f:
            if f.read() == content:
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
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# -------- Core AppRun Logic --------

def get_all_user_dirs(append_dir="") -> dict[str, str]:
    user_dirs = {}
    base_dir = CONFIG.get("BaseDirectory", "/home")
    if not os.path.exists(base_dir):
        return user_dirs

    for entry in os.scandir(base_dir):
        if entry.is_dir():
            user_dir = os.path.join(base_dir, entry.name, append_dir)
            if os.path.exists(user_dir):
                user_dirs[entry.name] = user_dir
            elif CONFIG.get("MakeDirectoryIfPossible", False):
                try:
                    os.makedirs(user_dir, exist_ok=True)
                    st = os.stat(os.path.join(base_dir, entry.name))
                    os.chown(user_dir, st.st_uid, st.st_gid)
                    user_dirs[entry.name] = user_dir
                except Exception as e:
                    print(f"Could not create directory {user_dir}: {e}")
    return user_dirs


def generate_desktop_entry(property_dict: dict[str, str]) -> str:
    desktop_entry_content = Template
    for key, value in property_dict.items():
        desktop_entry_content = desktop_entry_content.replace(f"${key}$", value)

    fallbacks = {
        "Version": "1.0", "Comment": "", "Args": "",
        "Icon.png": "/usr/share/AppRun/unknown-app-icon.png",
        "Terminal": "false", "Type": "Application", "Categories": "Utility"
    }
    for frag, default_value in fallbacks.items():
        desktop_entry_content = desktop_entry_content.replace(f"${frag}$", default_value)
    return desktop_entry_content


def build_property_dict(apprun_path: str) -> dict[str, str] | None:
    result = subprocess.run(
        ["/usr/local/sbin/apprunutil.sh", "BundleInfo", apprun_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return None

    props: dict[str, str] = {"BundlePath": apprun_path}
    desktop_link_path = os.path.join(apprun_path, "AppRunMeta", "DesktopLink")

    if os.path.isdir(desktop_link_path):
        for prop_entry in os.scandir(desktop_link_path):
            if prop_entry.is_file():
                try:
                    with open(prop_entry.path, "r", encoding="utf-8") as f:
                        props[prop_entry.name] = f.read().strip()
                except UnicodeDecodeError:
                    props[prop_entry.name] = prop_entry.path
    return props


def ensure_user_applications_dir(username: str) -> str | None:
    user_apps = os.path.join("/home", username, ".local", "share", "applications")
    try:
        os.makedirs(user_apps, exist_ok=True)
        st = os.stat(os.path.join("/home", username))
        os.chown(user_apps, st.st_uid, st.st_gid)
        return user_apps
    except Exception as e:
        print(f"Could not ensure applications dir for {username}: {e}")
        return None


# -------- Sync Logic --------

def perform_sync_cycle(registry: dict) -> tuple[dict, list[str]]:
    """
    Scans all targets, generates desktop files, and cleans up the registry.
    Returns the updated registry and a list of directories that were scanned.
    """
    current_links: dict[str, set[str]] = defaultdict(set)
    observed_bundles: set[str] = set()
    scanned_directories: list[str] = []

    # 1. Identify Global Targets
    for target in CONFIG.get("GlobalApplicationProbeTargets", []):
        if os.path.isdir(target):
            scanned_directories.append(target)
            for entry in os.scandir(target):
                if entry.is_dir():
                    process_bundle(entry.path, "/usr/share/applications", None, observed_bundles, current_links)

    # 2. Identify User Targets
    user_apprun_dirs = get_all_user_dirs(CONFIG.get("ApplicationsDirectory", "Applications"))
    for username, apprun_dir in user_apprun_dirs.items():
        scanned_directories.append(apprun_dir)
        for entry in os.scandir(apprun_dir):
            if entry.is_dir():
                user_apps_dir = ensure_user_applications_dir(username)
                if user_apps_dir:
                    process_bundle(entry.path, user_apps_dir, username, observed_bundles, current_links)

    # 3. Cleanup Stale Links (Bundle exists, but specific link is gone)
    for bundle, links_now in current_links.items():
        prev = set(registry.get(bundle, {}).get("desktop_files", []))
        stale = prev - links_now
        for path in stale:
            remove_file_safely(path)
        registry[bundle] = {"desktop_files": sorted(links_now)}

    # 4. Cleanup Removed Bundles (Bundle completely gone)
    previously_known_bundles = set(registry.keys())
    removed_bundles = previously_known_bundles - observed_bundles
    for bundle in removed_bundles:
        for path in registry.get(bundle, {}).get("desktop_files", []):
            remove_file_safely(path)
        registry.pop(bundle, None)

    save_registry(registry)
    return registry, scanned_directories


def process_bundle(apprun_path, dest_dir, username, observed_bundles, current_links):
    """Helper to process a single AppRun bundle."""
    props = build_property_dict(apprun_path)
    if props is None:
        return

    observed_bundles.add(apprun_path)

    if "Name" in props:
        content = generate_desktop_entry(props)
        desktop_file = os.path.join(dest_dir, f"{props.get('Name', 'App')}.desktop")

        write_if_changed(desktop_file, content, mode=0o644)

        if username:
            try:
                st = os.stat(os.path.join("/home", username))
                os.chown(desktop_file, st.st_uid, st.st_gid)
            except Exception as e:
                print(f"Warning: could not chown {desktop_file}: {e}")

        current_links[apprun_path].add(desktop_file)


# -------- Execution Loops --------

def run_polling_loop():
    print("Starting Polling Backend...")
    registry = load_registry()
    while True:
        registry, _ = perform_sync_cycle(registry)
        time.sleep(CONFIG.get("ProbingIntervalSeconds", 3))


def run_inotify_loop():
    print("Starting Inotify Backend (Experimental)...")

    try:
        import pyinotify
    except ImportError:
        print("Error: 'pyinotify' module is required for the inotify backend.")
        print("Please install it (e.g., 'pip install pyinotify' or 'apt install python3-pyinotify').")
        sys.exit(1)

    registry = load_registry()

    # Perform initial scan to populate registry and get directories to watch
    registry, watched_dirs = perform_sync_cycle(registry)

    wm = pyinotify.WatchManager()
    mask = pyinotify.IN_CREATE | pyinotify.IN_DELETE | pyinotify.IN_MOVED_TO | pyinotify.IN_MOVED_FROM | pyinotify.IN_MODIFY

    class EventHandler(pyinotify.ProcessEvent):
        def __init__(self):
            self.last_sync = 0
            self.debounce_interval = CONFIG.get("InotifyDebounceSeconds", 1.0)
            self._timer_running = False

        def process_default(self, event):
            # Exclude non-relevant files (optional optimization)
            if event.name.startswith("."): return

            # Simple Debounce Logic
            # Note: In a production threaded environment, use a Timer.
            # For this simple blocking loop, we flag and sync.
            now = time.time()
            if now - self.last_sync > self.debounce_interval:
                print(f"Event detected: {event.pathname}. Syncing...")
                # Re-run full sync (which is safe because it's idempotent)
                nonlocal registry
                registry, current_watched = perform_sync_cycle(registry)

                # Update watches if new directories appeared
                # (Note: pyinotify add_watch is smart enough not to duplicate existing watches)
                for d in current_watched:
                    wm.add_watch(d, mask, rec=False)

                self.last_sync = time.time()

    notifier = pyinotify.Notifier(wm, EventHandler())

    # Add initial watches
    for d in watched_dirs:
        print(f"Watching: {d}")
        wm.add_watch(d, mask, rec=False)

    # Also watch the base User directory to detect NEW users/application folders
    base_home = CONFIG.get("BaseDirectory", "/home")
    if os.path.exists(base_home):
        wm.add_watch(base_home, mask, rec=False)

    try:
        notifier.loop()
    except KeyboardInterrupt:
        pass


# -------- Main --------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AppRun Desktop Integration Daemon")
    parser.add_argument("--backend", choices=["polling", "inotify-experimental"],
                        default="polling", help="Choose the monitoring backend")

    args = parser.parse_args()

    if args.backend == "inotify-experimental":
        run_inotify_loop()
    else:
        run_polling_loop()