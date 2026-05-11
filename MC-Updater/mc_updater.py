import json
import re
import shutil
import sys
import traceback
import urllib.request
import zipfile
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "MC-updater"
GITHUB_OWNER = "Anime0t4ku"
GITHUB_REPO = "mister-companion"

CONFIG_FILE = "config.json"
TARGET_EXE = "MiSTer-Companion.exe"
UPDATE_NOW_FILE = "updatenow.txt"

WINDOWS_ZIP_KEYWORDS = ["Windows", "x86_64", ".zip"]

INCLUDE_PRERELEASES = True


def app_folder():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent


def normalize_version(value):
    text = str(value or "").strip()
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text, re.IGNORECASE)

    if not match:
        return None

    return tuple(int(part) for part in match.groups())


def version_to_text(version):
    if not version:
        return "Unknown"

    return f"v{version[0]}.{version[1]}.{version[2]}"


def read_current_version(base_path):
    config_path = base_path / CONFIG_FILE

    if not config_path.exists():
        raise FileNotFoundError(f"{CONFIG_FILE} was not found next to MC-updater.")

    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    version_text = data.get("app_version")
    version = normalize_version(version_text)

    if not version:
        raise ValueError(f"Could not read a valid app_version from {CONFIG_FILE}.")

    return version_text, version


def github_api_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MC-updater",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def asset_is_windows_zip(asset_name):
    lowered = asset_name.lower()

    for keyword in WINDOWS_ZIP_KEYWORDS:
        if keyword.lower() not in lowered:
            return False

    return True


def find_latest_release():
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
    releases = github_api_json(url)

    if not isinstance(releases, list):
        raise RuntimeError("GitHub did not return a release list.")

    best_release = None
    best_version = None
    best_asset = None

    for release in releases:
        if release.get("draft"):
            continue

        if release.get("prerelease") and not INCLUDE_PRERELEASES:
            continue

        tag_name = release.get("tag_name", "")
        release_name = release.get("name", "")
        version = normalize_version(tag_name) or normalize_version(release_name)

        if not version:
            continue

        assets = release.get("assets", [])
        windows_asset = None

        for asset in assets:
            asset_name = asset.get("name", "")
            if asset_is_windows_zip(asset_name):
                windows_asset = asset
                break

        if not windows_asset:
            continue

        if best_version is None or version > best_version:
            best_release = release
            best_version = version
            best_asset = windows_asset

    if not best_release or not best_version or not best_asset:
        raise RuntimeError("Could not find a valid MiSTer Companion Windows release asset.")

    return best_release, best_version, best_asset


class UpdateWorker(QThread):
    status_changed = pyqtSignal(str)
    progress_changed = pyqtSignal(int)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.base_path = app_folder()

    def log(self, message):
        self.status_changed.emit(message)

    def run(self):
        try:
            self.progress_changed.emit(0)

            self.log("Reading installed version...")
            current_version_text, current_version = read_current_version(self.base_path)
            self.log(f"Installed version: {current_version_text}")

            self.log("Checking GitHub releases...")
            release, latest_version, asset = find_latest_release()
            latest_version_text = version_to_text(latest_version)
            self.log(f"Latest version: {latest_version_text}")

            if latest_version <= current_version:
                self.progress_changed.emit(100)
                self.finished_ok.emit("MiSTer Companion is already up to date.")
                return

            asset_name = asset.get("name")
            download_url = asset.get("browser_download_url")

            if not download_url:
                raise RuntimeError("The release asset does not have a download URL.")

            zip_path = self.base_path / asset_name
            exe_path = self.base_path / TARGET_EXE

            self.log(f"Downloading {asset_name}...")
            self.download_file(download_url, zip_path)
            self.progress_changed.emit(45)

            if exe_path.exists():
                self.log(f"Removing old {TARGET_EXE}...")
                try:
                    exe_path.unlink()
                except PermissionError:
                    raise PermissionError(
                        f"Could not remove {TARGET_EXE}. "
                        "Please make sure MiSTer Companion is closed and try again."
                    )

            self.log("Extracting update...")
            self.extract_zip(zip_path, self.base_path)
            self.progress_changed.emit(85)

            self.log("Removing downloaded zip file...")
            try:
                zip_path.unlink()
            except Exception:
                pass

            self.progress_changed.emit(100)
            self.finished_ok.emit(f"MiSTer Companion was updated to {latest_version_text}.")

        except Exception as e:
            error = f"{e}\n\n{traceback.format_exc()}"
            self.failed.emit(error)

    def download_file(self, url, destination):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "MC-updater",
            },
        )

        with urllib.request.urlopen(request, timeout=60) as response:
            total_size = int(response.headers.get("Content-Length", 0))
            downloaded = 0

            with open(destination, "wb") as f:
                while True:
                    chunk = response.read(1024 * 256)

                    if not chunk:
                        break

                    f.write(chunk)
                    downloaded += len(chunk)

                    if total_size > 0:
                        percent = int((downloaded / total_size) * 40)
                        self.progress_changed.emit(max(1, min(40, percent)))

    def extract_zip(self, zip_path, destination):
        with zipfile.ZipFile(zip_path, "r") as zip_file:
            for member in zip_file.infolist():
                extracted_path = destination / member.filename

                if member.is_dir():
                    extracted_path.mkdir(parents=True, exist_ok=True)
                    continue

                extracted_path.parent.mkdir(parents=True, exist_ok=True)

                with zip_file.open(member, "r") as source:
                    with open(extracted_path, "wb") as target:
                        shutil.copyfileobj(source, target)


class UpdaterWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.worker = None
        self.base_path = app_folder()
        self.update_now_path = self.base_path / UPDATE_NOW_FILE
        self.auto_update_mode = self.update_now_path.exists()

        self.setWindowTitle(APP_NAME)
        self.setMinimumWidth(520)
        self.setMinimumHeight(360)

        self.title_label = QLabel("MiSTer Companion Updater")
        self.title_label.setStyleSheet("font-size: 20px; font-weight: bold;")

        self.info_label = QLabel(
            "This tool checks your installed MiSTer Companion version and downloads the latest Windows build if needed."
        )
        self.info_label.setWordWrap(True)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)

        self.update_button = QPushButton("Check and Update")
        self.update_button.clicked.connect(self.start_update)

        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.info_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.log_box)
        layout.addWidget(self.update_button)
        layout.addWidget(self.close_button)

        if self.auto_update_mode:
            self.update_button.setVisible(False)
            self.close_button.setVisible(False)
            self.append_log(f"{UPDATE_NOW_FILE} found. Starting automatic update check...")
            QTimer.singleShot(250, self.start_update)

    def append_log(self, message):
        self.log_box.append(message)

    def start_update(self):
        self.update_button.setEnabled(False)
        self.progress.setValue(0)

        if not self.auto_update_mode:
            self.log_box.clear()

        self.worker = UpdateWorker()
        self.worker.status_changed.connect(self.append_log)
        self.worker.progress_changed.connect(self.progress.setValue)
        self.worker.finished_ok.connect(self.update_finished)
        self.worker.failed.connect(self.update_failed)
        self.worker.start()

    def remove_update_now_file(self):
        if self.update_now_path.exists():
            try:
                self.update_now_path.unlink()
                self.append_log(f"Removed {UPDATE_NOW_FILE}.")
            except Exception as e:
                self.append_log(f"Could not remove {UPDATE_NOW_FILE}: {e}")

    def update_finished(self, message):
        self.append_log(message)
        self.update_button.setEnabled(True)

        if self.auto_update_mode:
            self.remove_update_now_file()

            QMessageBox.information(
                self,
                "Update Complete",
                (
                    f"{message}\n\n"
                    "The update has finished successfully.\n\n"
                    "Press OK to close the updater. You can then start MiSTer Companion again."
                ),
            )

            QApplication.quit()
            return

        QMessageBox.information(self, APP_NAME, message)

    def update_failed(self, error):
        self.append_log("Update failed.")
        self.append_log(error)
        self.update_button.setEnabled(True)

        if self.auto_update_mode:
            self.append_log(f"{UPDATE_NOW_FILE} was not removed because the update failed.")
            self.close_button.setVisible(True)
            return

        QMessageBox.critical(self, APP_NAME, "Update failed. Check the log for details.")


def main():
    app = QApplication(sys.argv)
    window = UpdaterWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()