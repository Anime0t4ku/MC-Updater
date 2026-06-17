import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
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
UPDATE_NOW_FILE = "updatenow.txt"

WINDOWS_TARGET_EXE = "MiSTer-Companion.exe"
LINUX_TARGET_EXE = "MiSTer-Companion"

WINDOWS_ZIP_KEYWORDS = ["Windows", "x86_64", ".zip"]
LINUX_TAR_KEYWORDS = ["Linux", "x86_64", ".tar.gz"]

INCLUDE_PRERELEASES = True



def apply_companion_style(app):
    app.setStyle("Fusion")
    app.setStyleSheet(
        """
        QWidget {
            background-color: #120f1c;
            color: #f2ecff;
            selection-background-color: #8b5cf6;
            selection-color: #ffffff;
        }

        QLabel {
            background: transparent;
            color: #f2ecff;
        }

        QPushButton {
            background-color: #2b2340;
            color: #f2ecff;
            border: 1px solid #5b4a7a;
            border-radius: 9px;
            padding: 7px 12px;
            font-weight: 600;
        }

        QPushButton:hover {
            background-color: #3a2f55;
            border-color: #8b5cf6;
        }

        QPushButton:pressed {
            background-color: #4c3b73;
            border-color: #a78bfa;
        }

        QPushButton:disabled {
            background-color: #211b30;
            color: #8d829e;
            border-color: #30283f;
        }

        QTextEdit {
            background-color: #1b1628;
            color: #f2ecff;
            border: 1px solid #3a2f55;
            border-radius: 9px;
            padding: 7px;
            selection-background-color: #8b5cf6;
            selection-color: #ffffff;
        }

        QProgressBar {
            background-color: #1b1628;
            color: #f2ecff;
            border: 1px solid #3a2f55;
            border-radius: 9px;
            text-align: center;
            height: 22px;
        }

        QProgressBar::chunk {
            background-color: #8b5cf6;
            border-radius: 8px;
        }
        """
    )

def app_folder():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent


def current_platform():
    system = platform.system().lower()

    if system == "windows":
        return {
            "name": "Windows",
            "target_exe": WINDOWS_TARGET_EXE,
            "asset_keywords": WINDOWS_ZIP_KEYWORDS,
            "archive_type": "zip",
        }

    if system == "linux":
        return {
            "name": "Linux",
            "target_exe": LINUX_TARGET_EXE,
            "asset_keywords": LINUX_TAR_KEYWORDS,
            "archive_type": "tar.gz",
        }

    raise RuntimeError(f"Unsupported operating system: {platform.system()}")


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


def asset_matches_platform(asset_name, platform_info):
    lowered = asset_name.lower()

    for keyword in platform_info["asset_keywords"]:
        if keyword.lower() not in lowered:
            return False

    return True


def find_latest_release(platform_info):
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
        matching_asset = None

        for asset in assets:
            asset_name = asset.get("name", "")
            if asset_matches_platform(asset_name, platform_info):
                matching_asset = asset
                break

        if not matching_asset:
            continue

        if best_version is None or version > best_version:
            best_release = release
            best_version = version
            best_asset = matching_asset

    if not best_release or not best_version or not best_asset:
        raise RuntimeError(
            f"Could not find a valid MiSTer Companion {platform_info['name']} release asset."
        )

    return best_release, best_version, best_asset


def make_executable(path):
    if not path.exists():
        raise FileNotFoundError(f"{path.name} was not found after extraction.")

    current_mode = os.stat(path).st_mode
    os.chmod(
        path,
        current_mode
        | stat.S_IXUSR
        | stat.S_IXGRP
        | stat.S_IXOTH,
    )


class UpdateWorker(QThread):
    status_changed = pyqtSignal(str)
    progress_changed = pyqtSignal(int)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.base_path = app_folder()
        self.platform_info = current_platform()

    def log(self, message):
        self.status_changed.emit(message)

    def run(self):
        try:
            self.progress_changed.emit(0)

            self.log(f"Detected platform: {self.platform_info['name']}")

            self.log("Reading installed version...")
            current_version_text, current_version = read_current_version(self.base_path)
            self.log(f"Installed version: {current_version_text}")

            self.log("Checking GitHub releases...")
            release, latest_version, asset = find_latest_release(self.platform_info)
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

            archive_path = self.base_path / asset_name
            target_path = self.base_path / self.platform_info["target_exe"]

            self.log(f"Downloading {asset_name}...")
            self.download_file(download_url, archive_path)
            self.progress_changed.emit(45)

            if target_path.exists():
                self.log(f"Removing old {self.platform_info['target_exe']}...")
                try:
                    target_path.unlink()
                except PermissionError:
                    raise PermissionError(
                        f"Could not remove {self.platform_info['target_exe']}. "
                        "Please make sure MiSTer Companion is closed and try again."
                    )

            self.log("Extracting update...")

            if self.platform_info["archive_type"] == "zip":
                self.extract_zip(archive_path, self.base_path)
            elif self.platform_info["archive_type"] == "tar.gz":
                self.extract_tar_gz(archive_path, self.base_path)
            else:
                raise RuntimeError(
                    f"Unsupported archive type: {self.platform_info['archive_type']}"
                )

            self.progress_changed.emit(85)

            if self.platform_info["name"] == "Linux":
                self.log("Making Linux executable runnable...")
                make_executable(target_path)

            self.log("Removing downloaded archive file...")
            try:
                archive_path.unlink()
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

                if not self.is_safe_extract_path(destination, extracted_path):
                    raise RuntimeError(f"Unsafe path found in archive: {member.filename}")

                if member.is_dir():
                    extracted_path.mkdir(parents=True, exist_ok=True)
                    continue

                extracted_path.parent.mkdir(parents=True, exist_ok=True)

                with zip_file.open(member, "r") as source:
                    with open(extracted_path, "wb") as target:
                        shutil.copyfileobj(source, target)

    def extract_tar_gz(self, tar_path, destination):
        with tarfile.open(tar_path, "r:gz") as tar_file:
            for member in tar_file.getmembers():
                extracted_path = destination / member.name

                if not self.is_safe_extract_path(destination, extracted_path):
                    raise RuntimeError(f"Unsafe path found in archive: {member.name}")

                if member.isdir():
                    extracted_path.mkdir(parents=True, exist_ok=True)
                    continue

                if member.isfile():
                    extracted_path.parent.mkdir(parents=True, exist_ok=True)

                    source = tar_file.extractfile(member)
                    if source is None:
                        continue

                    with source:
                        with open(extracted_path, "wb") as target:
                            shutil.copyfileobj(source, target)

    def is_safe_extract_path(self, destination, target_path):
        destination = destination.resolve()
        target_path = target_path.resolve()

        try:
            target_path.relative_to(destination)
            return True
        except ValueError:
            return False


class UpdaterWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.worker = None
        self.base_path = app_folder()
        self.update_now_path = self.base_path / UPDATE_NOW_FILE
        self.auto_update_mode = self.update_now_path.exists()

        try:
            self.platform_info = current_platform()
            platform_name = self.platform_info["name"]
        except Exception:
            platform_name = platform.system() or "Unknown"

        self.setWindowTitle(APP_NAME)
        self.setMinimumWidth(520)
        self.setMinimumHeight(360)

        self.title_label = QLabel("MiSTer Companion Updater")
        self.title_label.setStyleSheet("font-size: 20px; font-weight: bold;")

        self.info_label = QLabel(
            f"This tool checks your installed MiSTer Companion version and downloads the latest {platform_name} build if needed."
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
        self.close_button.setVisible(True)

        if self.auto_update_mode:
            self.remove_update_now_file()

        self.show_update_finished_dialog(message)

    def show_update_finished_dialog(self, message):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Update Complete")
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setText(message)
        dialog.setInformativeText("The update process has finished.")

        open_button = dialog.addButton(
            "Open MiSTer Companion",
            QMessageBox.ButtonRole.AcceptRole,
        )
        close_button = dialog.addButton(
            "Close",
            QMessageBox.ButtonRole.RejectRole,
        )
        dialog.setDefaultButton(open_button)

        dialog.exec()

        if dialog.clickedButton() == open_button:
            self.open_mister_companion()
            return

        QApplication.quit()

    def open_mister_companion(self):
        try:
            target_path = self.base_path / self.platform_info["target_exe"]
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Could not detect platform: {e}")
            return

        if not target_path.exists():
            QMessageBox.critical(
                self,
                APP_NAME,
                f"Could not find {target_path.name} next to MC-Updater.",
            )
            return

        try:
            if self.platform_info["name"] == "Linux":
                make_executable(target_path)

            if self.platform_info["name"] == "Windows":
                creationflags = 0
                if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                    creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
                if hasattr(subprocess, "DETACHED_PROCESS"):
                    creationflags |= subprocess.DETACHED_PROCESS

                subprocess.Popen(
                    [str(target_path)],
                    cwd=str(self.base_path),
                    creationflags=creationflags,
                )
            else:
                subprocess.Popen(
                    [str(target_path)],
                    cwd=str(self.base_path),
                    start_new_session=True,
                )

            QApplication.quit()
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Could not open MiSTer Companion:\n\n{e}")

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
    apply_companion_style(app)
    window = UpdaterWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()