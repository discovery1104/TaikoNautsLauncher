"""TaikoNauts unofficial launcher - PySide6 edition."""

from __future__ import annotations

import ctypes
import json
import math
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    from PySide6.QtCore import (
        QEasingCurve, QIODevice, QParallelAnimationGroup,
        QPauseAnimation, QPoint, QPropertyAnimation, QRectF, QSaveFile,
        QSequentialAnimationGroup, Qt, QThread, QTimer, QUrl,
        QVariantAnimation, Signal,
    )
    from PySide6.QtGui import (
        QColor, QDesktopServices, QDragEnterEvent, QDropEvent, QFont, QIcon,
        QFontDatabase, QKeySequence, QLinearGradient, QPainter, QPainterPath,
        QPixmap, QRegion, QShortcut,
    )
    from PySide6.QtWidgets import (
        QApplication, QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox,
        QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
        QGraphicsBlurEffect, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
        QHeaderView, QLabel,
        QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton,
        QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QTableWidget,
        QTableWidgetItem, QVBoxLayout, QWidget,
    )
except ImportError:
    ctypes.windll.user32.MessageBoxW(
        0,
        "PySide6 が必要です。\n\nコマンドプロンプトで次を実行してください:\n"
        "py -m pip install PySide6 msgpack",
        "TaikoNauts Launcher",
        0x10,
    )
    raise SystemExit(1)

try:
    import msgpack
except ImportError:
    msgpack = None


MODULE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", MODULE_DIR))
else:
    SCRIPT_DIR = MODULE_DIR
    RESOURCE_DIR = MODULE_DIR
LAUNCHER_ASSETS_DIR = RESOURCE_DIR / "launcher_assets"
BACKGROUND_IMAGE_PATH = LAUNCHER_ASSETS_DIR / "background.png"
BACKGROUND_LOGO_PATH = LAUNCHER_ASSETS_DIR / "background_logo.png"
LOGO_IMAGE_PATH = LAUNCHER_ASSETS_DIR / "logo.png"
LAUNCHER_SETTINGS_PATH = SCRIPT_DIR / "launcher_settings.json"


def _initial_base_dir() -> Path:
    try:
        saved = json.loads(LAUNCHER_SETTINGS_PATH.read_text(encoding="utf-8"))
        configured = Path(saved.get("gameDirectory", ""))
        if configured.is_dir():
            return configured.resolve()
    except (OSError, ValueError, TypeError):
        pass
    candidates = [SCRIPT_DIR, Path.cwd()]
    try:
        candidates.extend(path.parent for path in SCRIPT_DIR.glob("*/TaikoNauts.exe"))
    except OSError:
        pass
    return next((path.resolve() for path in candidates if (path / "TaikoNauts.exe").is_file()), SCRIPT_DIR)


def set_base_dir(path: Path) -> None:
    global BASE_DIR, GAME_EXE, SKINS_DIR, CONFIG_PATH, PLAYER_DATA_DIR, SONGS_DIR, AUTOSTART_DIR
    BASE_DIR = path.resolve()
    GAME_EXE = BASE_DIR / "TaikoNauts.exe"
    SKINS_DIR = BASE_DIR / "Skins"
    CONFIG_PATH = BASE_DIR / "Config" / "GameConfig.json"
    PLAYER_DATA_DIR = BASE_DIR / "PlayerData"
    SONGS_DIR = BASE_DIR / "Songs"
    AUTOSTART_DIR = BASE_DIR / "ランチャー起動時関連ファイル"


set_base_dir(_initial_base_dir())


@dataclass(frozen=True)
class DetectedGameVersion:
    display: str
    full: str


class _VSFixedFileInfo(ctypes.Structure):
    _fields_ = [
        ("signature", ctypes.c_uint32),
        ("structure_version", ctypes.c_uint32),
        ("file_version_ms", ctypes.c_uint32),
        ("file_version_ls", ctypes.c_uint32),
        ("product_version_ms", ctypes.c_uint32),
        ("product_version_ls", ctypes.c_uint32),
        ("file_flags_mask", ctypes.c_uint32),
        ("file_flags", ctypes.c_uint32),
        ("file_os", ctypes.c_uint32),
        ("file_type", ctypes.c_uint32),
        ("file_subtype", ctypes.c_uint32),
        ("file_date_ms", ctypes.c_uint32),
        ("file_date_ls", ctypes.c_uint32),
    ]


def _format_game_version(parts: tuple[int, int, int, int]) -> str:
    if parts[0] >= 2000 and 1 <= parts[1] <= 12:
        return f"{parts[0]:04d}.{parts[1]:02d}.{parts[2]:02d}.{parts[3]}"
    values = list(parts)
    while len(values) > 3 and values[-1] == 0:
        values.pop()
    return ".".join(map(str, values))


def detect_game_version(executable: Path) -> DetectedGameVersion | None:
    """Read the embedded Windows ProductVersion without optional dependencies."""
    if os.name != "nt" or not executable.is_file():
        return None
    try:
        version_dll = ctypes.WinDLL("version", use_last_error=True)
        get_size = version_dll.GetFileVersionInfoSizeW
        get_size.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
        get_size.restype = ctypes.c_uint32
        get_info = version_dll.GetFileVersionInfoW
        get_info.argtypes = [
            ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ]
        get_info.restype = ctypes.c_int
        query_value = version_dll.VerQueryValueW
        query_value.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32),
        ]
        query_value.restype = ctypes.c_int

        ignored_handle = ctypes.c_uint32(0)
        size = get_size(str(executable), ctypes.byref(ignored_handle))
        if size <= 0:
            return None
        data = ctypes.create_string_buffer(size)
        if not get_info(str(executable), 0, size, data):
            return None

        root_pointer = ctypes.c_void_p()
        root_length = ctypes.c_uint32(0)
        if not query_value(data, "\\", ctypes.byref(root_pointer), ctypes.byref(root_length)):
            return None
        fixed = ctypes.cast(root_pointer, ctypes.POINTER(_VSFixedFileInfo)).contents
        if fixed.signature != 0xFEEF04BD:
            return None

        product_parts = (
            fixed.product_version_ms >> 16,
            fixed.product_version_ms & 0xFFFF,
            fixed.product_version_ls >> 16,
            fixed.product_version_ls & 0xFFFF,
        )
        file_parts = (
            fixed.file_version_ms >> 16,
            fixed.file_version_ms & 0xFFFF,
            fixed.file_version_ls >> 16,
            fixed.file_version_ls & 0xFFFF,
        )
        parts = product_parts if any(product_parts) else file_parts
        if not any(parts):
            return None

        translations_pointer = ctypes.c_void_p()
        translations_length = ctypes.c_uint32(0)
        translations: list[tuple[int, int]] = []
        if query_value(
            data, "\\VarFileInfo\\Translation",
            ctypes.byref(translations_pointer), ctypes.byref(translations_length),
        ):
            words = ctypes.cast(translations_pointer, ctypes.POINTER(ctypes.c_uint16))
            translations = [
                (words[index], words[index + 1])
                for index in range(0, translations_length.value // 2 - 1, 2)
            ]
        translations.extend(((0x0411, 1200), (0x0409, 1200), (0x0409, 1252)))

        def query_string(field: str) -> str:
            for language, code_page in translations:
                string_pointer = ctypes.c_void_p()
                string_length = ctypes.c_uint32(0)
                key = f"\\StringFileInfo\\{language:04x}{code_page:04x}\\{field}"
                if query_value(
                    data, key, ctypes.byref(string_pointer), ctypes.byref(string_length),
                ) and string_pointer.value:
                    value = ctypes.wstring_at(string_pointer.value).strip()
                    if value:
                        return value
            return ""

        product_name = query_string("ProductName")
        if product_name and "taikonauts" not in product_name.casefold():
            return None
        product_text = query_string("ProductVersion") or query_string("FileVersion")

        numeric_full = ".".join(map(str, parts))
        return DetectedGameVersion(
            display=_format_game_version(parts),
            full=product_text or numeric_full,
        )
    except (AttributeError, OSError, ValueError):
        return None


def load_application_font(app: QApplication) -> str:
    candidates = (
        LAUNCHER_ASSETS_DIR / "NotoSansJP-VF.ttf",
        SCRIPT_DIR / "NotoSansJP-VF.ttf",
        SCRIPT_DIR / "assets" / "NotoSansJP-VF.ttf",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "NotoSansJP-VF.ttf",
    )

    def apply_font(family: str) -> str:
        font = QFont(family)
        font.setPointSizeF(10.0)
        font.setWeight(QFont.Weight.Normal)
        font.setStyleStrategy(
            QFont.StyleStrategy.PreferAntialias
            | QFont.StyleStrategy.PreferQuality
        )
        # Full horizontal hinting noticeably changes Noto's proportions around
        # 11 px. Vertical-only hinting keeps captions crisp and visually
        # consistent with the larger labels.
        font.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
        app.setFont(font)
        return family

    for path in candidates:
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                return apply_font(families[0])
    if "Noto Sans JP" in QFontDatabase.families():
        return apply_font("Noto Sans JP")
    return apply_font("Yu Gothic UI")
OFFICIAL_URL = "https://taikonauts-docs.pages.dev/"
TOOLS_URL = "https://taikonauts-tools.pages.dev/"
YOUTUBE_CHANNELS = [
    ("ランチャーの製作者", "https://youtube.com/channel/UCn3pPOq59V5_VLoqmdpU96w?si=4e0LoilMZZlONIcO"),
    ("TaikøNauts 製作者", "https://youtube.com/@touhou-renren?si=y_JZjP_qwGMbCk6-"),
    ("スペシャルサンクス", "https://youtube.com/@regu-youtube?si=oM1h0kShWgSRA79y"),
]



def read_json(path: Path, default=None):
    for encoding in ("utf-8-sig", "utf-8", "shift_jis", "cp932"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        except OSError:
            break
    return {} if default is None else default


def write_json(path: Path, value) -> None:
    write_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    output = QSaveFile(str(path))
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(output.errorString())
    if output.write(data) != len(data):
        message = output.errorString()
        output.cancelWriting()
        raise OSError(message)
    if not output.commit():
        raise OSError(output.errorString())


def open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))




class SkinDialog(QDialog):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("スキンを変更")
        self.resize(380, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("スキンを選択"))
        self.list = QListWidget()
        skins = sorted(p.name for p in SKINS_DIR.iterdir() if p.is_dir()) if SKINS_DIR.is_dir() else []
        self.list.addItems(skins)
        current = str(read_json(CONFIG_PATH).get("skinPath", "")).replace("\\", "/").split("/")[-1]
        self.current_skin = current
        matches = self.list.findItems(current, Qt.MatchFlag.MatchExactly)
        if matches:
            self.list.setCurrentItem(matches[0])
        layout.addWidget(self.list)
        if not skins:
            empty = QLabel("利用できるスキンがありません。Skinsフォルダーへスキンを追加してください。", objectName="muted")
            empty.setWordWrap(True)
            layout.addWidget(empty)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setObjectName("primary")
        self.apply_button.setEnabled(False)
        buttons.rejected.connect(self.reject)
        self.apply_button.clicked.connect(self.apply)
        self.list.currentItemChanged.connect(lambda _current, _previous: self.update_apply_state())
        self.list.itemDoubleClicked.connect(lambda _item: self.apply())
        layout.addWidget(buttons)

    def update_apply_state(self) -> None:
        item = self.list.currentItem()
        self.apply_button.setEnabled(bool(item and item.text() != self.current_skin))

    def apply(self) -> None:
        item = self.list.currentItem()
        if not item:
            QMessageBox.warning(self, "未選択", "スキンを選択してください。")
            return
        config = read_json(CONFIG_PATH)
        config["skinPath"] = f"Skins/{item.text()}"
        try:
            write_json(CONFIG_PATH, config)
        except OSError as exc:
            QMessageBox.critical(self, "保存エラー", str(exc))
            return
        self.accept()


class NamePlateDialog(QDialog):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("ネームプレート設定")
        self.resize(580, 650)
        self.stack = QStackedWidget()
        root = QVBoxLayout(self)
        root.addWidget(self.stack)
        self.build_player_page()

    def build_player_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("🪪  ネームプレート設定"))
        layout.addWidget(QLabel("プレイヤーを選択してください"))
        self.players = QListWidget()
        if PLAYER_DATA_DIR.is_dir():
            self.players.addItems(sorted(p.name for p in PLAYER_DATA_DIR.iterdir() if p.is_dir()))
        self.players.itemDoubleClicked.connect(lambda _: self.open_editor())
        layout.addWidget(self.players)
        row = QHBoxLayout()
        cancel = QPushButton("キャンセル")
        cancel.clicked.connect(self.reject)
        next_button = QPushButton("次へ →")
        next_button.setObjectName("primary")
        next_button.clicked.connect(self.open_editor)
        row.addWidget(cancel)
        row.addWidget(next_button)
        layout.addLayout(row)
        self.stack.addWidget(page)

    def open_editor(self) -> None:
        item = self.players.currentItem()
        if not item:
            QMessageBox.warning(self, "未選択", "プレイヤーを選択してください。")
            return
        self.player_name = item.text()
        self.config_file = PLAYER_DATA_DIR / self.player_name / "NamePlateConfig.json"
        cfg = read_json(self.config_file)
        page = QWidget()
        outer = QVBoxLayout(page)
        top = QHBoxLayout()
        back = QPushButton("← 戻る")
        back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        top.addWidget(back)
        top.addWidget(QLabel(f"🪪  {self.player_name}"), 1)
        outer.addLayout(top)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        self.name = QLineEdit(str(cfg.get("name", "")))
        self.title_edit = QLineEdit(str(cfg.get("title", "")))
        self.rank = QLineEdit(str(cfg.get("rank", "")))
        self.rank_type = QSpinBox()
        self.rank_type.setRange(-9999, 9999)
        self.rank_type.setValue(int(cfg.get("rankType", 2)))
        self.gold = QCheckBox("金色にする")
        self.gold.setChecked(bool(cfg.get("isRankGold", False)))
        self.plate_type = QSpinBox()
        self.plate_type.setRange(0, 99999)
        self.plate_type.setValue(int(cfg.get("namePlateType", 0)))
        self.plate_type.valueChanged.connect(self.update_preview)
        for label, widget in (
            ("名前 (name)", self.name), ("称号 (title)", self.title_edit),
            ("段位 (rank)", self.rank), ("段位の種類", self.rank_type),
            ("金色の段位枠", self.gold), ("プレート番号", self.plate_type),
        ):
            form.addRow(label, widget)
        preview_button = QPushButton("🔍 プレビュー更新")
        preview_button.clicked.connect(self.update_preview)
        form.addRow("", preview_button)
        self.preview = QLabel("プレビュー")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(100)
        form.addRow(self.preview)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.save)
        outer.addWidget(buttons)
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self.update_preview()

    def update_preview(self) -> None:
        skin = str(read_json(CONFIG_PATH).get("skinPath", "")).replace("\\", "/").split("/")[-1]
        path = SKINS_DIR / skin / "Image" / "99.Common" / "NamePlate" / "Plates" / str(self.plate_type.value()) / "Base.png"
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.preview.setPixmap(QPixmap())
            self.preview.setText(f"画像が見つかりません\n{path}")
        else:
            self.preview.setText("")
            self.preview.setPixmap(pixmap.scaled(480, 140, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def save(self) -> None:
        value = {
            "name": self.name.text(), "title": self.title_edit.text(), "rank": self.rank.text(),
            "isRankGold": self.gold.isChecked(), "namePlateType": self.plate_type.value(),
            "rankType": self.rank_type.value(),
        }
        try:
            write_json(self.config_file, value)
        except OSError as exc:
            QMessageBox.critical(self, "保存エラー", str(exc))
            return
        self.accept()


COURSE_NAMES = {
    "easy": ("かんたん", "#3cb371"), "normal": ("ふつう", "#e8a020"),
    "hard": ("むずかしい", "#e05030"), "oni": ("おに", "#9b30ff"),
    "edit": ("うら", "#c0a000"), "tower": ("タワー", "#4080ff"), "dan": ("段位", "#888888"),
}


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "shift_jis", "cp932"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            pass
    return ""


def tja_header(path: Path) -> dict[str, str]:
    meta = {}
    for line in read_text(path).splitlines():
        line = line.strip()
        for key in ("TITLE", "SUBTITLE", "BPM", "MAKER"):
            if line.upper().startswith(key + ":"):
                meta[key] = line[len(key) + 1:].strip()
    return meta


def tja_courses(path: Path) -> list[dict]:
    lines = read_text(path).splitlines()
    result = []
    header_bpm = None
    for line in lines:
        if line.strip().upper().startswith("BPM:"):
            try:
                header_bpm = float(line.split(":", 1)[1])
            except ValueError:
                pass
            break
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.upper().startswith("COURSE:"):
            i += 1
            continue
        course = line.split(":", 1)[1].strip().lower()
        level, combo, rolls = "", 0, 0
        bpms = [header_bpm] if header_bpm else []
        i += 1
        while i < len(lines) and lines[i].strip() != "#START":
            if lines[i].strip().upper().startswith("LEVEL:"):
                level = lines[i].split(":", 1)[1].strip()
            i += 1
        i += 1
        while i < len(lines) and lines[i].strip() != "#END":
            note = lines[i].strip()
            if note.upper().startswith("#BPMCHANGE"):
                try:
                    bpms.append(float(note.split()[-1]))
                except ValueError:
                    pass
            elif not note.startswith("#"):
                note = note.split("//", 1)[0].rstrip(",")
                combo += sum(c in "1234" for c in note)
                rolls += sum(c in "56" for c in note)
            i += 1
        result.append({"course": course, "level": level, "combo": combo, "rolls": rolls,
                       "min_bpm": min(bpms) if bpms else None, "max_bpm": max(bpms) if bpms else None})
    return result


def box_title(path: Path) -> str:
    for line in read_text(path).splitlines():
        if line.strip().upper().startswith("#TITLE:"):
            return line.strip()[7:].strip()
    return path.parent.name


class SongScanThread(QThread):
    scan_progress = Signal(int, int, int)
    scan_ready = Signal(int, object, str)

    def __init__(self, token: int, folder: Path, parent=None):
        super().__init__(parent)
        self.token = token
        self.folder = folder

    def run(self) -> None:
        try:
            paths = sorted(self.folder.rglob("*.tja"))
            rows = []
            total = len(paths)
            for index, path in enumerate(paths, 1):
                if self.isInterruptionRequested():
                    return
                meta, courses = tja_header(path), tja_courses(path)
                rows.append((path, meta, courses))
                if index == 1 or index == total or index % 10 == 0:
                    self.scan_progress.emit(self.token, index, total)
            self.scan_ready.emit(self.token, rows, "")
        except Exception as exc:
            self.scan_ready.emit(self.token, [], str(exc))


class SongCountThread(QThread):
    counted = Signal(str, int)

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self.root = root

    def run(self) -> None:
        count = 0
        try:
            if self.root.is_dir():
                for _path in self.root.rglob("*.tja"):
                    if self.isInterruptionRequested():
                        return
                    count += 1
        except OSError:
            count = 0
        self.counted.emit(str(self.root.resolve()), count)


class DropTable(QTableWidget):
    files_dropped = Signal(list)

    def __init__(self, *args):
        super().__init__(*args)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        self.files_dropped.emit([Path(url.toLocalFile()) for url in event.mimeData().urls()])
        event.acceptProposedAction()


class SongBrowserDialog(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("入ってる曲を見る")
        self.resize(850, 650)
        self.current_box: Path | None = None
        self.scan_token = 0
        self.scan_thread: SongScanThread | None = None
        self.root = QVBoxLayout(self)
        self.show_boxes()

    def clear(self) -> None:
        while self.root.count():
            item = self.root.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            elif item.layout(): self._clear_layout(item.layout())

    def show_boxes(self) -> None:
        self.cancel_scan()
        self.scan_token += 1
        self.clear()
        self.root.addWidget(QLabel("曲フォルダー", objectName="sectionTitle"))
        boxes = []
        if SONGS_DIR.is_dir():
            for definition in SONGS_DIR.rglob("box.def"):
                boxes.append((box_title(definition), definition.parent))
        self.boxes = QListWidget()
        for title, path in sorted(boxes):
            item_text = f"{title}   ·   {path.name}"
            self.boxes.addItem(item_text)
            self.boxes.item(self.boxes.count() - 1).setData(Qt.ItemDataRole.UserRole, path)
        self.boxes.itemDoubleClicked.connect(lambda item: self.show_songs(item.data(Qt.ItemDataRole.UserRole)))
        if not boxes:
            self.root.addStretch()
            empty_title = QLabel("曲セットがありません", objectName="sectionTitle")
            empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.root.addWidget(empty_title)
            empty = QLabel("Songsフォルダーに、box.defを含む曲セットを追加してください。", objectName="muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.root.addWidget(empty)
            open_folder = QPushButton("ゲームフォルダーを開く")
            open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(BASE_DIR))))
            self.root.addWidget(open_folder, alignment=Qt.AlignmentFlag.AlignHCenter)
            self.root.addStretch()
            return
        self.boxes.setCurrentRow(0)
        self.root.addWidget(self.boxes)
        open_button = QPushButton("開く")
        open_button.setObjectName("primary")
        open_button.clicked.connect(lambda: self.show_songs(self.boxes.currentItem().data(Qt.ItemDataRole.UserRole)) if self.boxes.currentItem() else None)
        self.root.addWidget(open_button)

    def show_songs(self, box_path: Path) -> None:
        self.cancel_scan()
        self.scan_token += 1
        token = self.scan_token
        self.current_box = Path(box_path)
        self.clear()
        top = QHBoxLayout()
        back = QPushButton("← フォルダ一覧")
        back.clicked.connect(self.show_boxes)
        top.addWidget(back)
        top.addWidget(QLabel(box_title(self.current_box / "box.def")), 1)
        add = QPushButton("＋ 曲/ZIPを追加")
        add.clicked.connect(self.pick_files)
        top.addWidget(add)
        self.root.addLayout(top)
        self.song_search = QLineEdit()
        self.song_search.setPlaceholderText("曲名・サブタイトル・BPMで検索")
        self.song_search.setAccessibleName("曲を検索")
        self.song_search.setClearButtonEnabled(True)
        self.song_search.textChanged.connect(self.filter_songs)
        self.root.addWidget(self.song_search)
        self.loading_label = QLabel("曲を読み込んでいます…", objectName="muted")
        self.root.addWidget(self.loading_label)
        self.table = DropTable(0, 5)
        self.table.setAccessibleName("曲の一覧")
        self.table.setHorizontalHeaderLabels(["曲名", "サブタイトル", "BPM", "最大コンボ", "ファイル"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setColumnHidden(4, True)
        self.table.currentCellChanged.connect(
            lambda row, column, _old_row, _old_column: self.show_detail(row, column))
        self.table.files_dropped.connect(self.import_files)
        content = QHBoxLayout()
        content.setSpacing(12)
        content.addWidget(self.table, 1)
        detail = QFrame(objectName="card")
        detail.setFixedWidth(285)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(18, 17, 18, 17)
        detail_layout.addWidget(QLabel("曲の詳細", objectName="eyebrow"))
        self.detail_title = QLabel("曲を選択してください", objectName="sectionTitle")
        self.detail_title.setWordWrap(True)
        detail_layout.addWidget(self.detail_title)
        self.detail_body = QLabel("一覧から曲を選ぶと、難易度やコンボ数を確認できます。", objectName="muted")
        self.detail_body.setWordWrap(True)
        self.detail_body.setAlignment(Qt.AlignmentFlag.AlignTop)
        detail_layout.addWidget(self.detail_body, 1)
        content.addWidget(detail)
        self.root.addLayout(content, 1)
        hint = QLabel("曲を選択すると右側に詳細を表示 / .tja・フォルダー・ZIPをドロップして追加")
        hint.setObjectName("muted")
        self.root.addWidget(hint)
        self.scan_thread = SongScanThread(token, self.current_box, self)
        self.scan_thread.scan_progress.connect(self.update_scan_progress)
        self.scan_thread.scan_ready.connect(self.populate_songs)
        thread = self.scan_thread
        thread.finished.connect(lambda: self.finish_scan_thread(thread))
        thread.start()

    def update_scan_progress(self, token: int, current: int, total: int) -> None:
        if token == self.scan_token and hasattr(self, "loading_label"):
            self.loading_label.setText(f"曲を読み込んでいます…  {current} / {total}")

    def populate_songs(self, token: int, rows: list, error: str) -> None:
        if token != self.scan_token or not hasattr(self, "table"):
            return
        if error:
            self.loading_label.setText(f"読み込みに失敗しました: {error}")
            return
        self.table.setSortingEnabled(False)
        for tja, meta, courses in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            combo = max((course["combo"] for course in courses), default=0)
            values = [meta.get("TITLE", tja.stem), meta.get("SUBTITLE", ""),
                      meta.get("BPM", ""), combo, str(tja)]
            for column, value in enumerate(values):
                item = QTableWidgetItem()
                item.setData(Qt.ItemDataRole.DisplayRole, value)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.loading_label.setText(
            f"{len(rows)}曲を表示" if rows else "このフォルダーには曲がありません。右上の追加ボタンから追加できます。")
        if rows:
            self.table.selectRow(0)
            self.show_detail(0, 0)

    def finish_scan_thread(self, thread: SongScanThread) -> None:
        if self.scan_thread is thread:
            self.scan_thread = None
        thread.deleteLater()

    def cancel_scan(self) -> None:
        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.requestInterruption()

    def shutdown(self) -> None:
        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.requestInterruption()
            self.scan_thread.wait(2000)

    def show_detail(self, row: int, _column: int) -> None:
        if row < 0 or not self.table.item(row, 4):
            return
        path = Path(self.table.item(row, 4).text())
        meta = tja_header(path)
        courses = tja_courses(path)
        self.detail_title.setText(meta.get("TITLE", path.stem))
        details = [meta.get("SUBTITLE", ""),
                   f"BPM  {meta.get('BPM', '-')}\n譜面作成  {meta.get('MAKER', '-')}", ""]
        for course in courses:
            name = COURSE_NAMES.get(course["course"], (course["course"], ""))[0]
            bpm = course["max_bpm"]
            bpm_text = "-" if bpm is None else (str(bpm) if bpm == course["min_bpm"] else f"{course['min_bpm']}～{bpm}")
            details.append(f"{name}  ★{course['level']}\nコンボ {course['combo']}  ·  連打 {course['rolls']}\nBPM {bpm_text}")
        self.detail_body.setText("\n\n".join(part for part in details if part))

    def filter_songs(self, text: str) -> None:
        query = text.strip().lower()
        for row in range(self.table.rowCount()):
            haystack = " ".join(self.table.item(row, column).text() for column in range(4)).lower()
            self.table.setRowHidden(row, bool(query and query not in haystack))

    def pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "曲またはZIPを選択", str(BASE_DIR), "対応ファイル (*.tja *.zip);;すべて (*.*)")
        if paths:
            self.import_files([Path(p) for p in paths])

    def import_files(self, paths: list[Path]) -> None:
        if not self.current_box:
            return
        errors = []
        for path in paths:
            try:
                if path.is_dir():
                    target = self.current_box / path.name
                    if target.exists():
                        raise FileExistsError(f"{target.name} は既に存在します")
                    shutil.copytree(path, target)
                elif path.suffix.lower() == ".zip":
                    with zipfile.ZipFile(path) as archive:
                        target = (self.current_box / path.stem).resolve()
                        if target.exists():
                            raise FileExistsError(f"{target.name} は既に存在します")
                        for member in archive.infolist():
                            member_path = (target / member.filename).resolve()
                            if target not in member_path.parents and member_path != target:
                                raise ValueError(f"ZIP内に不正なパスがあります: {member.filename}")
                        archive.extractall(target)
                elif path.suffix.lower() == ".tja":
                    target = self.current_box / path.name
                    if target.exists():
                        raise FileExistsError(f"{target.name} は既に存在します")
                    shutil.copy2(path, target)
                else:
                    raise ValueError(f"{path.name} は対応していない形式です")
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                errors.append(f"{path.name}: {exc}")
        self.show_songs(self.current_box)
        if errors:
            QMessageBox.warning(self, "追加できなかった項目", "\n".join(errors))


FAV_META_TITLE = 1
FAV_META_SUBTITLE = 2
FAV_META_GENRE = 9
FAV_META_HASH64 = 15
FAV_INDEX = 1


@dataclass
class FavSong:
    hash64: object
    title: str
    subtitle: str
    genres: set[str] = field(default_factory=set)

    @property
    def genre_text(self) -> str:
        return " / ".join(sorted(self.genres))


class FavoritesWindow(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setWindowTitle("お気に入り曲を管理")
        self.resize(1050, 720)
        self.songs: list[FavSong] = []
        self.song_by_hash = {}
        self.favorite_order = []
        self.songdata_obj = None
        self.songdata_path: Path | None = None
        self.dirty = False
        self.config_path = BASE_DIR / "favorites_editor.json"
        self.backup_dir = BASE_DIR / "FavoritesBackup"
        self.build_ui()
        self.autoload()
        self.update_status()

    def build_ui(self) -> None:
        root = QVBoxLayout(self)
        tools = QHBoxLayout()
        cache = QPushButton("曲情報を読み込む")
        cache.setToolTip("SongListCache.dat を選択")
        cache.clicked.connect(self.open_cache)
        data = QPushButton("プレイヤーデータを読み込む")
        data.setToolTip("SongData.dat を選択")
        data.clicked.connect(self.open_songdata)
        tools.addWidget(cache)
        tools.addWidget(data)
        self.status = QLabel("未読み込み")
        self.status.setObjectName("muted")
        tools.addWidget(self.status, 1)
        root.addLayout(tools)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("曲名・サブタイトル・ジャンルを検索")
        self.search.setAccessibleName("お気に入り候補の曲を検索")
        self.search.textChanged.connect(self.refresh)
        self.fav_only = QCheckBox("お気に入りのみ")
        self.fav_only.toggled.connect(self.refresh)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.fav_only)
        root.addLayout(filters)
        self.genre_area = QScrollArea()
        self.genre_area.setWidgetResizable(True)
        self.genre_area.setMaximumHeight(125)
        self.genre_area.setVisible(False)
        self.genre_body = QWidget()
        self.genre_grid = QGridLayout(self.genre_body)
        self.genre_area.setWidget(self.genre_body)
        root.addWidget(self.genre_area)
        self.empty_hint = QLabel(
            "お気に入りを編集するには、最初に「曲情報を読み込む」と「プレイヤーデータを読み込む」を選択してください。",
            objectName="muted")
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setMinimumHeight(54)
        root.addWidget(self.empty_hint)
        self.table = QTableWidget(0, 5)
        self.table.setAccessibleName("お気に入り曲の一覧")
        self.table.setHorizontalHeaderLabels(["順番", "お気に入り", "曲名", "サブタイトル", "ジャンル"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.table.cellClicked.connect(self.handle_favorite_click)
        root.addWidget(self.table)
        hint = QLabel("「お気に入り」列をクリックして追加・解除できます。順番はお気に入りだけを表示して変更します。", objectName="muted")
        root.addWidget(hint)
        actions = QHBoxLayout()
        self.add_all_button = QPushButton("検索結果を追加")
        self.add_all_button.clicked.connect(self.bulk_add)
        self.remove_all_button = QPushButton("検索結果から解除")
        self.remove_all_button.clicked.connect(self.bulk_remove)
        up = QPushButton("↑ 上へ")
        up.clicked.connect(lambda: self.move_selected(-1))
        down = QPushButton("↓ 下へ")
        down.clicked.connect(lambda: self.move_selected(1))
        clear = QPushButton("すべて外す…")
        clear.setObjectName("danger")
        clear.clicked.connect(self.clear_favorites)
        save_as = QPushButton("名前を付けて保存")
        save_as.clicked.connect(lambda: self.save(True))
        self.save_button = QPushButton("変更を保存")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        for widget in (self.add_all_button, self.remove_all_button, up, down, clear): actions.addWidget(widget)
        actions.addStretch()
        actions.addWidget(save_as)
        actions.addWidget(self.save_button)
        root.addLayout(actions)

    def require_msgpack(self) -> bool:
        if msgpack is None:
            QMessageBox.critical(self, "依存パッケージ", "msgpack が必要です。\npy -m pip install msgpack")
            return False
        return True

    def open_cache(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "SongListCache.dat を選択", str(BASE_DIR), "DAT (*.dat);;すべて (*.*)")
        if path: self.load_cache(Path(path))

    def open_songdata(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "SongData.dat を選択", str(BASE_DIR), "DAT (*.dat);;すべて (*.*)")
        if path: self.load_songdata(Path(path))

    def load_cache(self, path: Path, silent=False) -> None:
        if not self.require_msgpack(): return
        try:
            raw = msgpack.unpackb(path.read_bytes(), raw=False, strict_map_key=False)
            by_hash = {}
            for entry in raw:
                try:
                    meta = entry[0][0]
                    h, title = meta[FAV_META_HASH64], meta[FAV_META_TITLE]
                    subtitle, genre = meta[FAV_META_SUBTITLE] or "", meta[FAV_META_GENRE] or ""
                except (IndexError, TypeError):
                    continue
                if h not in by_hash:
                    by_hash[h] = FavSong(h, title, subtitle, {genre} if genre else set())
                elif genre:
                    by_hash[h].genres.add(genre)
            self.songs, self.song_by_hash = list(by_hash.values()), by_hash
            self.cache_path = path
            self.build_genres()
            self.update_status()
            self.save_config()
            self.refresh()
        except Exception as exc:
            if not silent: QMessageBox.critical(self, "読み込みエラー", str(exc))

    def load_songdata(self, path: Path, silent=False) -> None:
        if not self.require_msgpack(): return
        try:
            obj = msgpack.unpackb(path.read_bytes(), raw=False, strict_map_key=False)
            if not isinstance(obj, list) or len(obj) <= FAV_INDEX:
                raise ValueError("想定した SongData.dat の形式ではありません")
            self.songdata_obj, self.songdata_path = obj, path
            self.favorite_order = list(dict.fromkeys(obj[FAV_INDEX]))
            self.dirty = False
            self.update_status()
            self.save_config()
            self.refresh()
        except Exception as exc:
            if not silent: QMessageBox.critical(self, "読み込みエラー", str(exc))

    def build_genres(self) -> None:
        while self.genre_grid.count():
            item = self.genre_grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.genre_checks = {}
        for i, genre in enumerate(sorted({g for song in self.songs for g in song.genres})):
            check = QCheckBox(genre)
            check.toggled.connect(self.refresh)
            self.genre_grid.addWidget(check, i // 5, i % 5)
            self.genre_checks[genre] = check
        self.genre_area.setVisible(bool(self.genre_checks))

    def filtered_songs(self) -> list[FavSong]:
        query = self.search.text().strip().lower()
        selected = {g for g, check in getattr(self, "genre_checks", {}).items() if check.isChecked()}
        favorites = set(self.favorite_order)
        return [song for song in self.songs
                if (not self.fav_only.isChecked() or song.hash64 in favorites)
                and (not selected or bool(song.genres & selected))
                and (not query or query in song.title.lower() or query in song.subtitle.lower() or query in song.genre_text.lower())]

    def refresh(self) -> None:
        filtered = self.filtered_songs()
        self.empty_hint.setVisible(not self.songs)
        if self.fav_only.isChecked() and not self.search.text() and not any(c.isChecked() for c in getattr(self, "genre_checks", {}).values()):
            order = {h: i for i, h in enumerate(self.favorite_order)}
            filtered.sort(key=lambda song: order.get(song.hash64, 10**9))
        self.table.setRowCount(0)
        favorites = set(self.favorite_order)
        positions = {hash_value: index + 1 for index, hash_value in enumerate(self.favorite_order)}
        self.add_all_button.setText(f"検索結果 {len(filtered)}曲を追加")
        self.remove_all_button.setText(f"検索結果 {len(filtered)}曲から解除")
        for song in filtered:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [positions.get(song.hash64, ""),
                      "★" if song.hash64 in favorites else "", song.title, song.subtitle, song.genre_text]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, song.hash64)
                self.table.setItem(row, col, item)

    def toggle_favorite(self, row: int, _column: int) -> None:
        h = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if h in self.favorite_order: self.favorite_order.remove(h)
        else: self.favorite_order.append(h)
        self.mark_dirty()
        self.refresh()

    def handle_favorite_click(self, row: int, column: int) -> None:
        if column == 1:
            self.toggle_favorite(row, column)

    def bulk_add(self) -> None:
        before = list(self.favorite_order)
        for song in self.filtered_songs():
            if song.hash64 not in self.favorite_order: self.favorite_order.append(song.hash64)
        if before != self.favorite_order:
            self.mark_dirty()
        self.refresh()

    def bulk_remove(self) -> None:
        before = list(self.favorite_order)
        targets = {song.hash64 for song in self.filtered_songs()}
        self.favorite_order = [h for h in self.favorite_order if h not in targets]
        if before != self.favorite_order:
            self.mark_dirty()
        self.refresh()

    def clear_favorites(self) -> None:
        if not self.favorite_order:
            return
        if QMessageBox.question(self, "確認", "お気に入りをすべて解除しますか？") == QMessageBox.StandardButton.Yes:
            self.favorite_order.clear()
            self.mark_dirty()
            self.refresh()

    def move_selected(self, delta: int) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        h = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if h not in self.favorite_order:
            return
        old_index = self.favorite_order.index(h)
        new_index = max(0, min(len(self.favorite_order) - 1, old_index + delta))
        if new_index == old_index:
            return
        self.favorite_order.pop(old_index)
        self.favorite_order.insert(new_index, h)
        self.fav_only.setChecked(True)
        self.mark_dirty()
        self.refresh()
        for target_row in range(self.table.rowCount()):
            if self.table.item(target_row, 0).data(Qt.ItemDataRole.UserRole) == h:
                self.table.selectRow(target_row)
                break

    def mark_dirty(self) -> None:
        self.dirty = True
        self.update_status()

    def discard_changes(self) -> None:
        if self.songdata_obj is not None:
            self.favorite_order = list(dict.fromkeys(self.songdata_obj[FAV_INDEX]))
        self.dirty = False
        self.update_status()
        self.refresh()

    def update_status(self) -> None:
        dirty = "  ·  未保存の変更あり" if self.dirty else ""
        self.status.setText(f"曲情報 {len(self.songs)}曲  ·  お気に入り {len(self.favorite_order)}件{dirty}")
        if hasattr(self, "save_button"):
            self.save_button.setEnabled(self.songdata_obj is not None and self.dirty)
            self.add_all_button.setEnabled(bool(self.songs) and self.songdata_obj is not None)
            self.remove_all_button.setEnabled(bool(self.songs) and self.songdata_obj is not None)

    def save(self, as_new=False) -> None:
        if self.songdata_obj is None or not self.require_msgpack():
            QMessageBox.information(self, "情報", "先に SongData.dat を読み込んでください。")
            return
        if self.fav_only.isChecked() and not self.search.text() and not any(c.isChecked() for c in getattr(self, "genre_checks", {}).values()):
            visible = [self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(self.table.rowCount())]
            if set(visible) == set(self.favorite_order): self.favorite_order = visible
        path = self.songdata_path
        if as_new or path is None:
            selected, _ = QFileDialog.getSaveFileName(self, "保存", str(BASE_DIR / "SongData.dat"), "DAT (*.dat)")
            if not selected: return
            path = Path(selected)
        try:
            if path.exists():
                self.backup_dir.mkdir(exist_ok=True)
                shutil.copy2(path, self.backup_dir / f"{path.stem}_{datetime.now():%Y%m%d_%H%M%S}{path.suffix}")
            self.songdata_obj[FAV_INDEX] = list(self.favorite_order)
            write_bytes(path, msgpack.packb(self.songdata_obj, use_bin_type=True))
            self.songdata_path = path
            self.dirty = False
            self.save_config()
            self.update_status()
            QMessageBox.information(self, "保存完了", f"保存しました。\n{path}")
        except OSError as exc:
            QMessageBox.critical(self, "保存エラー", str(exc))

    def save_config(self) -> None:
        value = {"cache": str(getattr(self, "cache_path", "")), "songdata": str(self.songdata_path or "")}
        try: write_json(self.config_path, value)
        except OSError: pass

    def autoload(self) -> None:
        config = read_json(self.config_path)
        cache, songdata = Path(config.get("cache", "")), Path(config.get("songdata", ""))
        if str(cache) != "." and cache.is_file(): self.load_cache(cache, True)
        if str(songdata) != "." and songdata.is_file(): self.load_songdata(songdata, True)





class LauncherBackdrop(QWidget):
    """Animated composite of the supplied TaikoNauts background artwork."""

    CACHE_MARGIN = 10.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._background = QPixmap(str(BACKGROUND_IMAGE_PATH))
        self._background_logo = QPixmap(str(BACKGROUND_LOGO_PATH))
        self._art_cache = QPixmap()
        self._art_cache_key: tuple[int, int, int] | None = None
        self._phase = 0.0
        self._ambient_paused = False
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._advance)

    def showEvent(self, event) -> None:
        if not self._ambient_paused:
            self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def setAmbientPaused(self, paused: bool) -> None:
        self._ambient_paused = paused
        if paused:
            self._timer.stop()
        elif self.isVisible():
            self._timer.start()

    def _advance(self) -> None:
        # Keep the phase continuous: the parallax layers use different
        # frequencies, so a short modulus would make the artwork jump.
        self._phase += 0.018
        self.update()

    def _rebuild_art_cache(self) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return
        dpr = max(1.0, self.devicePixelRatioF())
        margin = self.CACHE_MARGIN
        logical_width = self.width() + margin * 2.0
        logical_height = self.height() + margin * 2.0
        cache = QPixmap(
            max(1, math.ceil(logical_width * dpr)),
            max(1, math.ceil(logical_height * dpr)),
        )
        cache.setDevicePixelRatio(dpr)
        cache.fill(QColor("#06030C"))

        painter = QPainter(cache)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        cache_bounds = QRectF(0.0, 0.0, logical_width, logical_height)
        if not self._background.isNull():
            painter.drawPixmap(
                cache_bounds,
                self._background,
                self._cover_source(self._background, cache_bounds, 1.031),
            )
        else:
            fallback = QLinearGradient(cache_bounds.topLeft(), cache_bounds.bottomRight())
            fallback.setColorAt(0.0, QColor("#12052B"))
            fallback.setColorAt(0.5, QColor("#25104B"))
            fallback.setColorAt(1.0, QColor("#05020D"))
            painter.fillRect(cache_bounds, fallback)

        if not self._background_logo.isNull():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
            painter.setOpacity(0.71)
            painter.drawPixmap(
                cache_bounds,
                self._background_logo,
                self._cover_source(
                    self._background_logo, cache_bounds, 1.019, 0.0, -0.12,
                ),
            )
        painter.end()
        self._art_cache = cache
        self._art_cache_key = (
            self.width(), self.height(), round(self.devicePixelRatioF() * 100),
        )

    @staticmethod
    def _cover_source(
        pixmap: QPixmap,
        target: QRectF,
        zoom: float = 1.0,
        pan_x: float = 0.0,
        pan_y: float = 0.0,
    ) -> QRectF:
        """Return a source crop that cover-fills target while preserving aspect ratio."""
        pixmap_width = float(pixmap.width())
        pixmap_height = float(pixmap.height())
        if pixmap_width <= 0.0 or pixmap_height <= 0.0 or target.height() <= 0.0:
            return QRectF()

        target_ratio = target.width() / target.height()
        pixmap_ratio = pixmap_width / pixmap_height
        zoom = max(1.0, zoom)
        if pixmap_ratio > target_ratio:
            source_height = pixmap_height / zoom
            source_width = source_height * target_ratio
        else:
            source_width = pixmap_width / zoom
            source_height = source_width / target_ratio

        horizontal_room = max(0.0, (pixmap_width - source_width) * 0.5)
        vertical_room = max(0.0, (pixmap_height - source_height) * 0.5)
        pan_x = max(-1.0, min(1.0, pan_x))
        pan_y = max(-1.0, min(1.0, pan_y))
        center_x = pixmap_width * 0.5 + horizontal_room * pan_x
        center_y = pixmap_height * 0.5 + vertical_room * pan_y
        return QRectF(
            center_x - source_width * 0.5,
            center_y - source_height * 0.5,
            source_width,
            source_height,
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bounds = QRectF(self.rect())
        painter.fillRect(bounds, QColor("#06030C"))

        cache_key = (
            self.width(), self.height(), round(self.devicePixelRatioF() * 100),
        )
        if self._art_cache.isNull() or cache_key != self._art_cache_key:
            self._rebuild_art_cache()
        if not self._art_cache.isNull():
            drift_x = math.sin(self._phase * 0.235) * 4.0
            drift_y = math.cos(self._phase * 0.196) * 2.0
            painter.save()
            painter.translate(
                -self.CACHE_MARGIN + drift_x,
                -self.CACHE_MARGIN + drift_y,
            )
            painter.drawPixmap(0, 0, self._art_cache)
            painter.restore()

        # Keep controls legible without flattening the supplied artwork.
        painter.fillRect(bounds, QColor(3, 2, 10, 40))
        top_scrim = QLinearGradient(0.0, 0.0, 0.0, min(185.0, bounds.height()))
        top_scrim.setColorAt(0.0, QColor(7, 4, 13, 220))
        top_scrim.setColorAt(0.52, QColor(7, 4, 13, 108))
        top_scrim.setColorAt(1.0, QColor(7, 4, 13, 0))
        painter.fillRect(QRectF(0.0, 0.0, bounds.width(), min(185.0, bounds.height())), top_scrim)

        footer_height = min(180.0, bounds.height())
        footer_scrim = QLinearGradient(
            0.0, bounds.height() - footer_height, 0.0, bounds.height()
        )
        footer_scrim.setColorAt(0.0, QColor(5, 3, 11, 0))
        footer_scrim.setColorAt(0.52, QColor(5, 3, 11, 115))
        footer_scrim.setColorAt(1.0, QColor(5, 3, 11, 232))
        painter.fillRect(
            QRectF(0.0, bounds.height() - footer_height, bounds.width(), footer_height),
            footer_scrim,
        )

        vignette = QLinearGradient(0.0, 0.0, bounds.width(), 0.0)
        vignette.setColorAt(0.0, QColor(3, 2, 9, 112))
        vignette.setColorAt(0.16, QColor(3, 2, 9, 0))
        vignette.setColorAt(0.78, QColor(3, 2, 9, 0))
        vignette.setColorAt(1.0, QColor(3, 2, 9, 90))
        painter.fillRect(bounds, vignette)


class TaikoMark(QWidget):
    def __init__(self, size: int = 40, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._pixmap = QPixmap(str(LOGO_IMAGE_PATH))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        inset = max(1.0, self.width() * 0.025)
        area = QRectF(inset, inset, self.width() - inset * 2, self.height() - inset * 2)
        if not self._pixmap.isNull():
            painter.drawPixmap(area, self._pixmap, QRectF(self._pixmap.rect()))
            return

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2FA8F3"))
        painter.drawRoundedRect(area, area.width() * 0.28, area.height() * 0.28)


class HoverGlowButton(QPushButton):
    """Button with a lightweight eased glow for hover and keyboard focus."""

    def __init__(self, *args, glow_color: QColor | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._glow_color = QColor(glow_color or QColor("#B486FF"))
        self._glow_intensity = 0.0
        self._glow_target = 0.0
        self._hovered = False
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0.0, 1.0)
        self._glow.setBlurRadius(1.0)
        transparent = QColor(self._glow_color)
        transparent.setAlpha(0)
        self._glow.setColor(transparent)
        self._glow.setEnabled(False)
        self.setGraphicsEffect(self._glow)

        self._glow_animation = QVariantAnimation(self)
        self._glow_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._glow_animation.valueChanged.connect(self._apply_glow)
        self._glow_animation.finished.connect(self._finish_glow)
        self.toggled.connect(lambda _checked: self._animate_glow())

    def _target_glow(self) -> float:
        if self._hovered:
            return 1.0
        if self.hasFocus():
            return 0.58
        if self.isCheckable() and self.isChecked():
            return 0.28
        return 0.0

    def _animate_glow(self) -> None:
        target = self._target_glow()
        self._glow_target = target
        self._glow_animation.stop()
        if target > 0.0:
            self._glow.setEnabled(True)
        self._glow_animation.setDuration(165 if target > self._glow_intensity else 220)
        self._glow_animation.setStartValue(self._glow_intensity)
        self._glow_animation.setEndValue(target)
        self._glow_animation.start()

    def _apply_glow(self, value) -> None:
        intensity = max(0.0, min(1.0, float(value)))
        self._glow_intensity = intensity
        color = QColor(self._glow_color)
        color.setAlpha(round(105 * intensity))
        self._glow.setColor(color)
        self._glow.setBlurRadius(1.0 + 17.0 * intensity)

    def _finish_glow(self) -> None:
        if self._glow_target <= 0.001:
            self._glow.setEnabled(False)

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._animate_glow()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self._animate_glow()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._animate_glow()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._animate_glow()


class AnimatedNavButton(HoverGlowButton):
    """Sidebar item whose hover and selected fills interpolate smoothly."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._nav_hovered = False
        self._nav_focused = False
        self._current_background = QColor(146, 92, 255, 0)
        self._current_border = QColor(180, 134, 255, 0)
        self._current_text = QColor("#C1B7CB")
        self._palette_start = (
            QColor(self._current_background),
            QColor(self._current_border),
            QColor(self._current_text),
        )
        self._palette_end = self._palette_start
        self._palette_animation = QVariantAnimation(self)
        self._palette_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._palette_animation.valueChanged.connect(self._apply_palette_progress)
        self.toggled.connect(lambda _checked: self._animate_palette())
        self._apply_nav_style()

    @staticmethod
    def _blend_color(start: QColor, end: QColor, progress: float) -> QColor:
        return QColor(
            round(start.red() + (end.red() - start.red()) * progress),
            round(start.green() + (end.green() - start.green()) * progress),
            round(start.blue() + (end.blue() - start.blue()) * progress),
            round(start.alpha() + (end.alpha() - start.alpha()) * progress),
        )

    def _target_palette(self) -> tuple[QColor, QColor, QColor]:
        if self.isChecked():
            return QColor("#925CFF"), QColor("#C29AFF"), QColor("#FFFFFF")
        if self._nav_hovered or self._nav_focused:
            return QColor("#2E203A"), QColor("#674A7B"), QColor("#FFFFFF")
        return QColor(146, 92, 255, 0), QColor(180, 134, 255, 0), QColor("#C1B7CB")

    def _animate_palette(self) -> None:
        self._palette_animation.stop()
        self._palette_start = (
            QColor(self._current_background),
            QColor(self._current_border),
            QColor(self._current_text),
        )
        self._palette_end = self._target_palette()
        self._palette_animation.setDuration(230 if self.isChecked() else 180)
        self._palette_animation.setStartValue(0.0)
        self._palette_animation.setEndValue(1.0)
        self._palette_animation.start()

    def _apply_palette_progress(self, value) -> None:
        progress = max(0.0, min(1.0, float(value)))
        self._current_background = self._blend_color(
            self._palette_start[0], self._palette_end[0], progress,
        )
        self._current_border = self._blend_color(
            self._palette_start[1], self._palette_end[1], progress,
        )
        self._current_text = self._blend_color(
            self._palette_start[2], self._palette_end[2], progress,
        )
        self._apply_nav_style()

    @staticmethod
    def _css_color(color: QColor) -> str:
        return f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"

    def _apply_nav_style(self) -> None:
        self.setStyleSheet(
            "background-color: " + self._css_color(self._current_background) + ";"
            "border: 1px solid " + self._css_color(self._current_border) + ";"
            "border-radius: 15px;"
            "color: " + self._css_color(self._current_text) + ";"
        )

    def enterEvent(self, event) -> None:
        self._nav_hovered = True
        self._animate_palette()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._nav_hovered = False
        self._animate_palette()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:
        self._nav_focused = True
        self._animate_palette()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self._nav_focused = False
        self._animate_palette()
        super().focusOutEvent(event)


class SettingsOverlayHost(QWidget):
    """Floating settings surface that dismisses when its empty halo is clicked."""

    dismissRequested = Signal()

    def mousePressEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.childAt(event.position().toPoint()) is None
        ):
            self.dismissRequested.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class RevealViewport(QWidget):
    """Clipping slot whose direct child can be animated without layout interference."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._widget: QWidget | None = None
        self._external_motion = False

    def setWidget(self, widget: QWidget) -> None:
        if self._widget is not None:
            self._widget.setParent(None)
        self._widget = widget
        widget.setParent(self)
        self.updateGeometry()
        self.syncGeometry()
        widget.show()

    def widget(self) -> QWidget | None:
        return self._widget

    def sizeHint(self):
        return self._widget.sizeHint() if self._widget is not None else super().sizeHint()

    def minimumSizeHint(self):
        if self._widget is not None:
            return self._widget.minimumSizeHint()
        return super().minimumSizeHint()

    def beginMotion(self) -> None:
        self._external_motion = True
        if self._widget is not None:
            self._widget.resize(self.size())

    def endMotion(self) -> None:
        self._external_motion = False
        self.syncGeometry()

    def syncGeometry(self) -> None:
        if self._widget is None:
            return
        self._widget.resize(self.size())
        if not self._external_motion:
            self._widget.move(0, 0)

    def resizeEvent(self, event) -> None:
        self.syncGeometry()
        super().resizeEvent(event)


class SlidingStack(QWidget):
    """A clipped stack that keeps old and new panels visible during a slide."""

    transitionFinished = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._widgets: list[QWidget] = []
        self._current = -1
        self._slide_group: QParallelAnimationGroup | None = None
        self._target = -1
        self._external_motion = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def count(self) -> int:
        return len(self._widgets)

    def widget(self, index: int) -> QWidget | None:
        if 0 <= index < len(self._widgets):
            return self._widgets[index]
        return None

    def currentIndex(self) -> int:
        return self._current

    def currentWidget(self) -> QWidget | None:
        return self.widget(self._current)

    def addWidget(self, widget: QWidget) -> int:
        widget.setParent(self)
        widget.setGeometry(self.rect())
        self._widgets.append(widget)
        index = len(self._widgets) - 1
        if self._current < 0:
            self._current = index
            widget.show()
        else:
            widget.hide()
        return index

    def replaceWidget(self, index: int, widget: QWidget) -> QWidget:
        if not 0 <= index < len(self._widgets):
            raise IndexError(index)
        if self._slide_group is not None:
            self._slide_group.stop()
            self._slide_group.deleteLater()
            self._slide_group = None
        previous = self._widgets[index]
        was_current = index == self._current
        previous.hide()
        previous.setParent(None)
        widget.setParent(self)
        widget.setGeometry(self.rect())
        self._widgets[index] = widget
        widget.setVisible(was_current)
        return previous

    def setCurrentIndex(self, index: int) -> None:
        if not 0 <= index < len(self._widgets):
            return
        if self._slide_group is not None:
            self._slide_group.stop()
            self._slide_group.deleteLater()
            self._slide_group = None
        for current, widget in enumerate(self._widgets):
            widget.move(0, 0)
            widget.setVisible(current == index)
        self._current = index

    @staticmethod
    def _position_animation(
        target: QWidget, start: QPoint, end: QPoint, duration: int,
    ) -> QPropertyAnimation:
        animation = QPropertyAnimation(target, b"pos")
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        return animation

    def slideTo(self, index: int, direction: int) -> bool:
        if not 0 <= index < len(self._widgets) or index == self._current:
            return False
        old = self.currentWidget()
        new = self.widget(index)
        if old is None or new is None:
            return False
        distance = max(1, self.height())
        sign = 1 if direction >= 0 else -1
        old_start = QPoint(0, 0)
        old_end = QPoint(0, -sign * distance)
        new_start = QPoint(0, sign * distance)
        new_end = QPoint(0, 0)
        old.move(old_start)
        new.resize(self.size())
        new.move(new_start)
        new.show()
        new.raise_()
        underlines = new.findChildren(QFrame, "sectionUnderline")

        group = QParallelAnimationGroup(self)
        group.addAnimation(self._position_animation(old, old_start, old_end, 310))
        group.addAnimation(self._position_animation(new, new_start, new_end, 310))
        for line in underlines:
            line.setMaximumWidth(0)
            reveal = QPropertyAnimation(line, b"maximumWidth")
            reveal.setDuration(310)
            reveal.setStartValue(0)
            reveal.setEndValue(max(1, self.width()))
            reveal.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(reveal)
        self._slide_group = group
        self._target = index

        def finish() -> None:
            if self._slide_group is not group:
                return
            old.hide()
            old.move(0, 0)
            new.move(0, 0)
            for line in underlines:
                line.setMaximumWidth(16777215)
            self._current = index
            self._slide_group = None
            group.deleteLater()
            self.transitionFinished.emit(index)

        group.finished.connect(finish)
        group.start()
        return True

    def beginMotion(self) -> None:
        self._external_motion = True
        current = self.currentWidget()
        if current is not None:
            current.resize(self.size())

    def endMotion(self) -> None:
        self._external_motion = False
        self.syncGeometry()

    def syncGeometry(self) -> None:
        for index, widget in enumerate(self._widgets):
            widget.resize(self.size())
            if not self._external_motion and index == self._current:
                widget.move(0, 0)

    def resizeEvent(self, event) -> None:
        self.syncGeometry()
        super().resizeEvent(event)




FLARIAL_STYLE = """
* {
    color: #FFFFFF;
}
QMainWindow, QWidget#appRoot { background: transparent; }
QDialog, QMessageBox { background: #17101F; color: #FFFFFF; }
QLabel { background: transparent; }
QFrame#windowSurface {
    background: #0B0712;
    border: 1px solid #352442;
    border-radius: 25px;
}
QWidget#launcherStage, QWidget#homePage, QWidget#homeOverlay,
QWidget#settingsOverlay, QWidget#settingsShell,
QStackedWidget#mainPages, QScrollArea#contentScroll,
QScrollArea#contentScroll > QWidget > QWidget { background: transparent; border: 0; }
QFrame#settingsScrim {
    background: rgba(5, 2, 12, 158); border: 0; border-radius: 25px;
}

QLabel#brandName { font-size: 22px; font-weight: 600; color: #FFFFFF; }
QLabel#profileName { font-size: 16px; font-weight: 500; }
QLabel#profileRole {
    color: #D6B7FF; background: #2D1D3B; border: 1px solid #5D3C79;
    border-radius: 10px; padding: 3px 8px; font-size: 12px; font-weight: 500;
}
QLabel#successPill, QLabel#errorPill {
    border-radius: 10px; padding: 3px 8px; font-size: 12px; font-weight: 600;
}
QLabel#successPill { color: #DCC4FF; background: #2D1D3B; border: 1px solid #69478A; }
QLabel#errorPill { color: #FFD2E5; background: #4A1D37; border: 1px solid #82345F; }
QFrame#profileDot { background: #985DFF; border: 2px solid #D0A8FF; border-radius: 17px; }
QLabel#versionPill {
    background: #2D1D3B; border: 1px solid #69478A; border-radius: 13px;
    padding: 5px 11px; color: #FFFFFF; font-size: 13px; font-weight: 600;
}
QLabel#greeting { font-size: 29px; font-weight: 500; color: #FFFFFF; }
QLabel#launcherStatus { font-size: 16px; color: #B9AEC6; }
QLabel#pathLabel { color: #988BA5; font-size: 12px; font-weight: 500; }

QPushButton {
    background: #291B35; color: #FFFFFF; border: 1px solid #49315D;
    border-radius: 8px; padding: 8px 13px; font-size: 14px; font-weight: 500;
}
QPushButton:hover { background: #362347; border-color: #674486; }
QPushButton:pressed { background: #21162B; }
QPushButton:disabled { background: #241E29; border-color: #362E3E; color: #7D7485; }
QPushButton:focus { border-color: #B98AFF; }
QPushButton#launchButton {
    min-width: 300px; max-width: 300px; min-height: 55px; max-height: 55px;
    background: #925CFF; border: 1px solid #B486FF; border-radius: 10px;
    padding: 0; font-size: 20px; font-weight: 650;
}
QPushButton#launchButton:hover { background: #A66FFF; border-color: #CFABFF; }
QPushButton#launchButton:pressed { background: #7542DC; }
QPushButton#launchButton:disabled { background: #49345F; border-color: #5A4172; color: #A99AB9; }
QPushButton#homeSettings {
    min-width: 300px; max-width: 300px; min-height: 45px; max-height: 45px;
    background: #291B35; border: 1px solid #49315D; border-radius: 10px;
    padding: 0; font-size: 17px; font-weight: 500;
}
QPushButton#homeSettings:hover { background: #38254A; border-color: #6A4888; }
QPushButton#windowControl, QPushButton#closeButton, QPushButton#overlayCloseButton {
    min-width: 35px; max-width: 35px; min-height: 35px; max-height: 35px;
    background: transparent; border: 0; border-radius: 10px;
    padding: 0; color: #D8D0D2; font-size: 18px; font-weight: 500;
}
QPushButton#windowControl:hover, QPushButton#overlayCloseButton:hover {
    background: rgba(255, 255, 255, 18); color: #FFFFFF;
}
QPushButton#closeButton:hover { background: #C6457B; color: #FFFFFF; }
QPushButton#textAction, QPushButton#linkButton {
    background: transparent; color: #C587FF; border: 0; padding: 8px 10px;
    font-size: 14px; font-weight: 600;
}
QPushButton#textAction:hover, QPushButton#linkButton:hover { background: #2D1D3B; color: #DFC3FF; }
QPushButton#primary, QDialogButtonBox QPushButton#primary {
    background: #925CFF; border-color: #B486FF; color: #FFFFFF; font-weight: 650;
}
QPushButton#primary:hover { background: #A66FFF; border-color: #CFABFF; }
QPushButton#danger { background: #421F38; color: #FFB8D6; border-color: #6F3158; }
QPushButton#danger:hover { background: #572749; border-color: #8F3E70; }

QFrame#homeStats {
    background: rgba(11, 7, 18, 215); border: 1px solid #49315D; border-radius: 14px;
}
QLabel#homeStatTitle { color: #A296AD; font-size: 12px; font-weight: 500; }
QLabel#homeStatValue { color: #FFFFFF; font-size: 14px; font-weight: 600; }
QFrame#homeStatDivider, QFrame#divider { background: #49315D; border: 0; }

QFrame#settingsSidebar, QFrame#settingsPanel {
    background: #17101F; border: 1px solid #352442; border-radius: 20px;
}
QLabel#sidebarTitle, QLabel#settingsTitle { font-size: 22px; font-weight: 600; color: #FFFFFF; }
QLabel#settingsSubtitle, QLabel#pageLead, QLabel#muted,
QLabel#cardDescription, QLabel#pageSubtitle { color: #A99DB5; font-size: 13px; font-weight: 500; }
QPushButton#settingsNav, QPushButton#returnButton {
    min-height: 50px; max-height: 50px; border-radius: 15px; border: 0;
    background: transparent; color: #C1B7CB; padding: 0 18px;
    text-align: left; font-size: 16px; font-weight: 500;
}
QPushButton#returnButton:hover { background: #281C32; color: #FFFFFF; }
QLabel#sectionTitle { color: #FFFFFF; font-size: 19px; font-weight: 650; }
QFrame#sectionUnderline { background: #A66FFF; border: 0; border-radius: 1px; }
QLabel#rowTitle, QLabel#cardTitle { color: #FFFFFF; font-size: 15px; font-weight: 600; }
QLabel#statValue { color: #FFFFFF; font-size: 15px; font-weight: 600; }
QLabel#pageHeading { color: #FFFFFF; font-size: 22px; font-weight: 650; }
QFrame#settingsList, QFrame#card, QFrame#actionCard, QFrame#statCard {
    background: #2B1B38; border: 1px solid #49305C; border-radius: 15px;
}
QFrame#settingsRow { background: transparent; border: 0; }
QFrame#settingsRow:hover { background: rgba(255, 255, 255, 8); }

QLineEdit, QSpinBox, QListWidget, QTableWidget {
    background: #21172A; color: #FFFFFF; border: 1px solid #443252;
    border-radius: 8px; padding: 7px;
    selection-background-color: #925CFF; selection-color: #FFFFFF;
}
QLineEdit:focus, QSpinBox:focus, QListWidget:focus, QTableWidget:focus { border-color: #B486FF; }
QLineEdit::placeholder { color: #807288; }
QListWidget::item { padding: 8px; border-radius: 6px; }
QListWidget::item:hover { background: #32233E; }
QListWidget::item:selected { background: #925CFF; color: #FFFFFF; }
QHeaderView::section {
    background: #281C32; color: #CBBFD5; border: 0; border-bottom: 1px solid #49315D;
    padding: 8px; font-size: 12px; font-weight: 600;
}
QTableWidget { gridline-color: #49315D; alternate-background-color: #1D1425; }
QTableWidget::item { padding: 6px; }
QTableCornerButton::section { background: #281C32; border: 0; }
QCheckBox { color: #D8CFDF; spacing: 7px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QCheckBox::indicator:unchecked { background: #21172A; border: 1px solid #594368; border-radius: 4px; }
QCheckBox::indicator:checked { background: #925CFF; border: 1px solid #C195FF; border-radius: 4px; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #513A61; border-radius: 4px; min-height: 26px; }
QScrollBar::handle:vertical:hover { background: #735189; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #513A61; border-radius: 4px; min-width: 26px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QDialogButtonBox QPushButton { min-width: 90px; }
QToolTip { background: #281C32; color: #FFFFFF; border: 1px solid #5B426B; padding: 6px; }
"""


class Launcher(QMainWindow):
    SETTINGS_BLUR_RADIUS = 12.0
    PAGE_META = [
        ("ホーム", "ゲームの状態と、よく使う操作をまとめています"),
        ("カスタマイズ", "見た目とプレイヤープロフィールを変更します"),
        ("曲ライブラリ", "導入済みの曲を探す・確認する・追加する"),
        ("お気に入り", "お気に入りの追加、解除、並べ替えを管理します"),
        ("リンク", "公式ドキュメントや関連サイトを開きます"),
    ]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("TaikøNauts Launcher")
        if LOGO_IMAGE_PATH.is_file():
            self.setWindowIcon(QIcon(str(LOGO_IMAGE_PATH)))
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(1040, 650)
        self._song_count_root = ""
        self._song_count_generation = 0
        self._song_count_threads: list[SongCountThread] = []
        self._game_version_signature: tuple[str, int, int] | None = None
        self._logical_page = 0
        self._pending_logical_page: int | None = None
        self._queued_page: int | None = None
        self._transitioning = False
        self._shell_animation: QParallelAnimationGroup | None = None
        self._home_intro_group: QParallelAnimationGroup | None = None
        self._initial_home_intro_started = False
        self._home_intro_resets: list[tuple[QWidget, QPoint]] = []
        self._home_intro_widgets: list[QWidget] = []
        self._home_intro_slots: list[RevealViewport] = []
        self._last_mask_geometry: tuple[int, int, int, int] | None = None
        self._mask_update_pending = False
        self.game_process = None
        self.game_timer = QTimer(self)
        self.game_timer.setInterval(1000)
        self.game_timer.timeout.connect(self.check_game_process)

        central = QWidget(objectName="appRoot")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(0)

        self.window_surface = QFrame(objectName="windowSurface")
        outer.addWidget(self.window_surface)

        shell = QVBoxLayout(self.window_surface)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self.stage = QWidget(objectName="launcherStage")
        stage_layers = QGridLayout(self.stage)
        stage_layers.setContentsMargins(0, 0, 0, 0)
        stage_layers.setSpacing(0)

        self.pages = QStackedWidget(objectName="mainPages")
        self.home_page = self._build_home_page()
        self.pages.addWidget(self.home_page)
        stage_layers.addWidget(self.pages, 0, 0)

        self.home_blur = QGraphicsBlurEffect(self.home_page)
        self.home_blur.setBlurHints(QGraphicsBlurEffect.BlurHint.AnimationHint)
        self.home_blur.setBlurRadius(0.0)
        self.home_blur.setEnabled(False)
        self.home_page.setGraphicsEffect(self.home_blur)

        self.song_page: SongBrowserDialog | None = None
        self.favorite_page: FavoritesWindow | None = None

        self.settings_overlay = QWidget(objectName="settingsOverlay")
        self.settings_overlay.setAccessibleName("設定オーバーレイ")
        overlay_layers = QGridLayout(self.settings_overlay)
        overlay_layers.setContentsMargins(0, 0, 0, 0)
        overlay_layers.setSpacing(0)

        self.settings_scrim = QFrame(objectName="settingsScrim")
        self.settings_scrim_effect = QGraphicsOpacityEffect(self.settings_scrim)
        self.settings_scrim_effect.setOpacity(0.0)
        self.settings_scrim.setGraphicsEffect(self.settings_scrim_effect)
        overlay_layers.addWidget(self.settings_scrim, 0, 0)

        self.settings_host = self._build_settings_host()
        overlay_layers.addWidget(self.settings_host, 0, 0)
        self.settings_overlay.hide()
        stage_layers.addWidget(self.settings_overlay, 0, 0)
        shell.addWidget(self.stage, 1)

        self._setup_shortcuts()
        self.pages.setCurrentIndex(0)
        self.refresh_dashboard()

    def _setup_shortcuts(self) -> None:
        self.shortcuts = []
        page_order = [0, 2, 3, 1, 4]
        for position, page_index in enumerate(page_order, 1):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{position}"), self)
            shortcut.activated.connect(lambda i=page_index: self.switch_page(i))
            self.shortcuts.append(shortcut)
        launch = QShortcut(QKeySequence("Ctrl+Return"), self)
        launch.activated.connect(
            lambda: (
                self.launch_game() if GAME_EXE.is_file() else self.select_game_location()
            )
            if not self.settings_overlay.isVisible()
            else None
        )
        self.shortcuts.append(launch)
        find = QShortcut(QKeySequence.StandardKey.Find, self)
        find.activated.connect(self.focus_page_search)
        self.shortcuts.append(find)
        save = QShortcut(QKeySequence.StandardKey.Save, self)
        save.activated.connect(self.save_current_page)
        self.shortcuts.append(save)
        back = QShortcut(QKeySequence("Escape"), self)
        back.activated.connect(
            lambda: self.switch_page(0)
            if self._logical_page or self.settings_overlay.isVisible()
            else None
        )
        self.shortcuts.append(back)

    def mousePressEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.position().y() <= 92
            and self.windowHandle() is not None
        ):
            self.windowHandle().startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def _update_window_mask(self) -> None:
        surface = getattr(self, "window_surface", None)
        if surface is None or surface.width() <= 0 or surface.height() <= 0:
            return
        geometry = surface.geometry()
        geometry_key = (geometry.x(), geometry.y(), geometry.width(), geometry.height())
        if geometry_key == self._last_mask_geometry:
            return
        bounds = QRectF(geometry).adjusted(0.0, 0.0, -1.0, -1.0)
        path = QPainterPath()
        path.addRoundedRect(bounds, 25.0, 25.0)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        self._last_mask_geometry = geometry_key

    def _queue_window_mask_update(self) -> None:
        if self._mask_update_pending:
            return
        self._mask_update_pending = True

        def apply_mask() -> None:
            self._mask_update_pending = False
            self._update_window_mask()

        QTimer.singleShot(0, apply_mask)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        central = self.centralWidget()
        if central is not None and central.layout() is not None:
            central.layout().activate()
        self._update_window_mask()
        if not self._initial_home_intro_started:
            self._initial_home_intro_started = True
            self._animate_home_intro()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.isVisible():
            self._queue_window_mask_update()

    def focus_page_search(self) -> None:
        index = self._logical_page
        if index == 2 and self.song_page is not None and hasattr(self.song_page, "song_search"):
            self.song_page.song_search.setFocus()
            self.song_page.song_search.selectAll()
        elif index == 3 and self.favorite_page is not None:
            self.favorite_page.search.setFocus()
            self.favorite_page.search.selectAll()

    def save_current_page(self) -> None:
        if self._logical_page == 3 and self.favorite_page is not None and self.favorite_page.dirty:
            self.favorite_page.save()

    def _window_controls(self) -> QWidget:
        controls = QWidget()
        layout = QHBoxLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        minimize = HoverGlowButton("−", objectName="windowControl")
        minimize.setToolTip("最小化")
        minimize.setAccessibleName("ウィンドウを最小化")
        minimize.setCursor(Qt.CursorShape.PointingHandCursor)
        minimize.clicked.connect(self.showMinimized)
        close = HoverGlowButton(
            "×", objectName="closeButton", glow_color=QColor("#D75A8D"),
        )
        close.setToolTip("閉じる")
        close.setAccessibleName("ランチャーを閉じる")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.close)
        layout.addWidget(minimize)
        layout.addWidget(close)
        return controls

    def _overlay_close_button(self) -> QPushButton:
        close = HoverGlowButton("×", objectName="overlayCloseButton")
        close.setToolTip("設定を閉じる")
        close.setAccessibleName("設定を閉じる")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(lambda: self.switch_page(0))
        return close

    def _brand_lockup(self, title: str, compact: bool = False) -> QWidget:
        brand = QWidget()
        layout = QHBoxLayout(brand)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(TaikoMark(34 if compact else 40))
        label = QLabel(title, objectName="sidebarTitle" if compact else "brandName")
        layout.addWidget(label)
        layout.addStretch()
        return brand

    def _build_settings_sidebar(self) -> QWidget:
        sidebar = QFrame(objectName="settingsSidebar")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(16, 18, 16, 16)
        side_layout.setSpacing(5)
        side_layout.addWidget(self._brand_lockup("Launcher", compact=True))
        side_layout.addSpacing(16)

        self.settings_nav_buttons: dict[int, QPushButton] = {}
        nav_items = [
            ("◆   カスタマイズ", 1),
            ("♪   曲ライブラリ", 2),
            ("★   お気に入り", 3),
            ("↗   リンク", 4),
        ]
        for label, page_index in nav_items:
            button = AnimatedNavButton(label, objectName="settingsNav")
            button.setCheckable(True)
            button.setAccessibleName(self.PAGE_META[page_index][0])
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, i=page_index: self.switch_page(i))
            side_layout.addWidget(button)
            self.settings_nav_buttons[page_index] = button

        side_layout.addStretch()
        back = HoverGlowButton("←   ホームへ戻る", objectName="returnButton")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(lambda: self.switch_page(0))
        side_layout.addWidget(back)
        return sidebar

    def _settings_panel(self, content: QWidget, title: str, subtitle: str) -> QWidget:
        panel = QFrame(objectName="settingsPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(22, 18, 22, 22)
        panel_layout.setSpacing(14)
        header = QHBoxLayout()
        header.setSpacing(14)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(QLabel(title, objectName="settingsTitle"))
        subtitle_label = QLabel(subtitle, objectName="settingsSubtitle")
        subtitle_label.setWordWrap(True)
        heading.addWidget(subtitle_label)
        header.addLayout(heading, 1)
        header.addWidget(self._overlay_close_button(), 0, Qt.AlignmentFlag.AlignTop)
        panel_layout.addLayout(header)
        underline = QFrame(objectName="sectionUnderline")
        underline.setFixedHeight(3)
        panel_layout.addWidget(underline)
        panel_layout.addWidget(content, 1)
        return panel

    def _build_settings_host(self) -> QWidget:
        host = SettingsOverlayHost(objectName="settingsShell")
        host.dismissRequested.connect(lambda: self.switch_page(0))
        layout = QHBoxLayout(host)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(15)

        self.sidebar_viewport = RevealViewport()
        self.sidebar_viewport.setFixedWidth(220)
        self.settings_sidebar = self._build_settings_sidebar()
        self.sidebar_viewport.setWidget(self.settings_sidebar)
        layout.addWidget(self.sidebar_viewport)

        self.settings_stack = SlidingStack()
        self.settings_stack.addWidget(self._build_custom_page())
        self.settings_stack.addWidget(self._settings_panel(
            self._placeholder_page("曲ライブラリを準備しています…"),
            "曲ライブラリ", "導入済みの曲を検索・確認・追加します",
        ))
        self.settings_stack.addWidget(self._settings_panel(
            self._placeholder_page("お気に入りエディターを準備しています…"),
            "お気に入り", "お気に入り曲と並び順を管理します",
        ))
        self.settings_stack.addWidget(self._build_links_page())
        self.settings_stack.transitionFinished.connect(self._finish_panel_transition)
        layout.addWidget(self.settings_stack, 1)
        self._update_settings_nav(1)
        return host

    def _update_settings_nav(self, logical_index: int) -> None:
        for index, button in self.settings_nav_buttons.items():
            button.setChecked(index == logical_index)

    def _wrap_embedded(
        self, widget: QWidget, title: str, subtitle: str, page_index: int,
    ) -> QWidget:
        return self._settings_panel(widget, title, subtitle)

    def _placeholder_page(self, message: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel(message, objectName="muted")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        return page

    def _build_home_page(self) -> QWidget:
        page = QWidget(objectName="homePage")
        layers = QGridLayout(page)
        layers.setContentsMargins(0, 0, 0, 0)
        layers.setSpacing(0)
        self.home_backdrop = LauncherBackdrop()
        layers.addWidget(self.home_backdrop, 0, 0)

        overlay = QWidget(objectName="homeOverlay")
        layout = QVBoxLayout(overlay)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(0)

        top_block = QWidget()
        top_block_layout = QVBoxLayout(top_block)
        top_block_layout.setContentsMargins(0, 0, 0, 0)
        top_block_layout.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self._brand_lockup("TaikøNauts"))
        top.addStretch()
        profile = QHBoxLayout()
        profile.setSpacing(9)
        avatar = QFrame(objectName="profileDot")
        avatar.setFixedSize(35, 35)
        profile.addWidget(avatar)
        profile.addWidget(QLabel("LOCAL", objectName="profileName"))
        self.top_status = QLabel("準備完了", objectName="successPill")
        profile.addWidget(self.top_status)
        top.addLayout(profile)
        top.addSpacing(4)
        top.addWidget(self._window_controls())
        top_block_layout.addLayout(top)

        meta = QHBoxLayout()
        meta.setContentsMargins(0, 9, 0, 0)
        self.game_version_label = QLabel("TAIKONAUTS", objectName="versionPill")
        meta.addWidget(self.game_version_label)
        meta.addStretch()
        top_block_layout.addLayout(meta)
        top_slot = RevealViewport()
        top_slot.setWidget(top_block)
        layout.addWidget(top_slot)
        layout.addStretch(1)

        center_block = QWidget()
        center = QVBoxLayout(center_block)
        center.setContentsMargins(0, 0, 0, 0)
        center.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        center.setSpacing(6)
        greeting = QLabel("おかえりなさい", objectName="greeting")
        greeting.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(greeting)
        self.home_lead = QLabel("ゲームを始める準備ができています。", objectName="launcherStatus")
        self.home_lead.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.addWidget(self.home_lead)
        center.addSpacing(8)
        self.launch_button = HoverGlowButton("▶   起動する", objectName="launchButton")
        self.launch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.launch_button.clicked.connect(self.launch_game)
        center.addWidget(self.launch_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.home_settings_button = HoverGlowButton("設定", objectName="homeSettings")
        self.home_settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.home_settings_button.clicked.connect(lambda: self.switch_page(1))
        center.addWidget(self.home_settings_button, 0, Qt.AlignmentFlag.AlignHCenter)
        center_slot = RevealViewport()
        center_slot.setWidget(center_block)
        layout.addWidget(center_slot)
        layout.addStretch(1)

        bottom_block = QWidget()
        bottom = QHBoxLayout(bottom_block)
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(14)
        stats = QFrame(objectName="homeStats")
        stats_layout = QHBoxLayout(stats)
        stats_layout.setContentsMargins(14, 7, 14, 7)
        stats_layout.setSpacing(12)
        self.skin_value = self._home_stat(stats_layout, "SKIN", "未設定")
        stats_layout.addWidget(self._vertical_divider("homeStatDivider"))
        self.player_value = self._home_stat(stats_layout, "PLAYERS", "0")
        stats_layout.addWidget(self._vertical_divider("homeStatDivider"))
        self.song_value = self._home_stat(stats_layout, "SONGS", "0")
        bottom.addWidget(stats)
        bottom.addStretch()
        location = QVBoxLayout()
        location.setSpacing(0)
        self.base_path_label = QLabel(str(BASE_DIR), objectName="pathLabel")
        self.base_path_label.setMaximumWidth(310)
        self.base_path_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.base_path_label.setToolTip(str(BASE_DIR))
        location.addWidget(self.base_path_label)
        self.location_button = QPushButton("フォルダーを開く", objectName="textAction")
        self.location_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.location_button.clicked.connect(self.handle_location_action)
        location.addWidget(self.location_button, 0, Qt.AlignmentFlag.AlignRight)
        bottom.addLayout(location)
        bottom_slot = RevealViewport()
        bottom_slot.setWidget(bottom_block)
        layout.addWidget(bottom_slot)

        layers.addWidget(overlay, 0, 0)
        self._home_intro_widgets = [top_block, center_block, bottom_block]
        self._home_intro_slots = [top_slot, center_slot, bottom_slot]
        return page

    def _home_stat(self, row: QHBoxLayout, title: str, value: str) -> QLabel:
        item = QWidget()
        layout = QVBoxLayout(item)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)
        title_label = QLabel(title, objectName="homeStatTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        value_label = QLabel(value, objectName="homeStatValue")
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_label.setMinimumWidth(60)
        layout.addWidget(value_label)
        row.addWidget(item)
        return value_label

    def _settings_row(self, title: str, description: str, button_text: str, callback) -> QWidget:
        row = QFrame(objectName="settingsRow")
        row.setMinimumHeight(68)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(20, 10, 16, 10)
        copy = QVBoxLayout()
        copy.setSpacing(2)
        copy.addWidget(QLabel(title, objectName="rowTitle"))
        copy.addWidget(QLabel(description, objectName="muted"))
        layout.addLayout(copy, 1)
        button = QPushButton(f"{button_text}  →", objectName="textAction")
        button.clicked.connect(callback)
        layout.addWidget(button)
        return row

    def _divider(self) -> QWidget:
        divider = QFrame(objectName="divider")
        divider.setFixedHeight(1)
        return divider

    def _vertical_divider(self, object_name: str = "divider") -> QWidget:
        divider = QFrame(objectName=object_name)
        divider.setFixedWidth(1)
        return divider

    def _section_heading(self, title: str) -> QWidget:
        heading = QWidget()
        layout = QVBoxLayout(heading)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        layout.addWidget(QLabel(title, objectName="sectionTitle"))
        underline = QFrame(objectName="sectionUnderline")
        underline.setFixedHeight(3)
        layout.addWidget(underline)
        return heading

    def _build_custom_page(self) -> QWidget:
        scroll = QScrollArea(objectName="contentScroll")
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 4, 8, 4)
        layout.setSpacing(16)

        layout.addWidget(self._section_heading("ゲーム内の表示"))
        settings = QFrame(objectName="settingsList")
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(0)
        self.custom_skin_value = QLabel("未設定", objectName="statValue")
        settings_layout.addWidget(self._value_settings_row(
            "スキン", "ゲーム全体の外観", self.custom_skin_value,
            "変更", self.open_skin_dialog))
        settings_layout.addWidget(self._divider())
        settings_layout.addWidget(self._settings_row(
            "プレイヤー表示", "名前、称号、段位、ネームプレート",
            "編集", self.open_nameplate_dialog))
        layout.addWidget(settings)

        layout.addWidget(self._section_heading("フォルダー"))
        locations = QFrame(objectName="settingsList")
        loc_layout = QVBoxLayout(locations)
        loc_layout.setContentsMargins(0, 0, 0, 0)
        loc_layout.setSpacing(0)
        for label, path in (("Skins", SKINS_DIR), ("PlayerData", PLAYER_DATA_DIR)):
            loc_layout.addWidget(self._settings_row(
                label, str(path), "開く",
                lambda _=False, p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))))
            if label == "Skins":
                loc_layout.addWidget(self._divider())
        layout.addWidget(locations)
        layout.addStretch()
        scroll.setWidget(page)
        return self._settings_panel(
            scroll, "カスタマイズ", "見た目とプレイヤープロフィールを変更します",
        )

    def _value_settings_row(self, title: str, description: str, value: QLabel,
                            button_text: str, callback) -> QWidget:
        row = QFrame(objectName="settingsRow")
        row.setMinimumHeight(72)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(20, 10, 16, 10)
        copy = QVBoxLayout()
        copy.setSpacing(2)
        copy.addWidget(QLabel(title, objectName="rowTitle"))
        copy.addWidget(QLabel(description, objectName="muted"))
        layout.addLayout(copy, 1)
        layout.addWidget(value)
        button = QPushButton(f"{button_text}  →", objectName="textAction")
        button.clicked.connect(callback)
        layout.addWidget(button)
        return row


    def _build_links_page(self) -> QWidget:
        scroll = QScrollArea(objectName="contentScroll")
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 4, 8, 4)
        layout.setSpacing(16)

        layout.addWidget(self._section_heading("公式"))
        official = QFrame(objectName="settingsList")
        official_layout = QVBoxLayout(official)
        official_layout.setContentsMargins(0, 0, 0, 0)
        official_layout.setSpacing(0)
        official_layout.addWidget(self._settings_row(
            "公式ドキュメント", "taikonauts-docs.pages.dev", "開く", lambda: open_url(OFFICIAL_URL)))
        official_layout.addWidget(self._divider())
        official_layout.addWidget(self._settings_row(
            "TaikoNauts Tools", "taikonauts-tools.pages.dev", "開く", lambda: open_url(TOOLS_URL)))
        layout.addWidget(official)

        layout.addWidget(self._section_heading("YouTube"))
        youtube = QFrame(objectName="settingsList")
        youtube_layout = QVBoxLayout(youtube)
        youtube_layout.setContentsMargins(0, 0, 0, 0)
        youtube_layout.setSpacing(0)
        for label, url in YOUTUBE_CHANNELS:
            youtube_layout.addWidget(self._settings_row(
                label, "YouTubeチャンネル", "開く", lambda _=False, u=url: open_url(u)))
            if label != YOUTUBE_CHANNELS[-1][0]:
                youtube_layout.addWidget(self._divider())
        layout.addWidget(youtube)
        layout.addStretch()
        scroll.setWidget(page)
        return self._settings_panel(
            scroll, "リンク", "ドキュメントと関連サイトを開きます",
        )

    @staticmethod
    def _move_animation(
        target: QWidget, start: QPoint, end: QPoint, duration: int,
        easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
    ) -> QPropertyAnimation:
        animation = QPropertyAnimation(target, b"pos")
        animation.setDuration(duration)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(easing)
        return animation

    def _stop_home_intro(self) -> None:
        group = self._home_intro_group
        self._home_intro_group = None
        if group is not None:
            group.stop()
            group.deleteLater()
        for widget, position in self._home_intro_resets:
            widget.move(position)
        for slot in self._home_intro_slots:
            slot.endMotion()
        self._home_intro_resets.clear()

    def _animate_home_intro(self) -> None:
        if (
            self._logical_page != 0
            or self.pages.currentIndex() != 0
            or self.settings_overlay.isVisible()
        ):
            return
        self._stop_home_intro()
        self.home_backdrop.setAmbientPaused(True)
        home = self.home_page
        if home.layout() is not None:
            home.layout().activate()
        overlay = home.findChild(QWidget, "homeOverlay")
        if overlay is not None and overlay.layout() is not None:
            overlay.layout().activate()
        for slot in self._home_intro_slots:
            slot.syncGeometry()
            slot.beginMotion()
        offsets = (-18, 30, 20)
        group = QParallelAnimationGroup(self)
        resets: list[tuple[QWidget, QPoint]] = []
        for index, widget in enumerate(self._home_intro_widgets):
            final_position = widget.pos()
            start_position = final_position + QPoint(0, offsets[index])
            widget.move(start_position)
            resets.append((widget, final_position))

            sequence = QSequentialAnimationGroup()
            sequence.addAnimation(QPauseAnimation(index * 75))
            pair = QParallelAnimationGroup()
            pair.addAnimation(self._move_animation(
                widget, start_position, final_position, 430,
                QEasingCurve.Type.OutQuart,
            ))
            sequence.addAnimation(pair)
            group.addAnimation(sequence)

        self._home_intro_group = group
        self._home_intro_resets = resets

        def finish() -> None:
            if self._home_intro_group is not group:
                return
            for widget, position in resets:
                widget.move(position)
            for slot in self._home_intro_slots:
                slot.endMotion()
            self._home_intro_resets.clear()
            self._home_intro_group = None
            group.deleteLater()
            if self._logical_page == 0 and not self.settings_overlay.isVisible():
                self.home_backdrop.setAmbientPaused(False)

        group.finished.connect(finish)
        group.start()

    def _drain_page_queue(self) -> None:
        queued = self._queued_page
        self._queued_page = None
        if queued is not None and queued != self._logical_page:
            QTimer.singleShot(0, lambda i=queued: self.switch_page(i))

    def _start_settings_entry(self, logical_index: int) -> None:
        if (
            not self._transitioning
            or self._pending_logical_page != logical_index
            or not self.settings_overlay.isVisible()
        ):
            return
        host = self.settings_host
        if self.settings_overlay.layout() is not None:
            self.settings_overlay.layout().activate()
        if host.layout() is not None:
            host.layout().activate()
        self.sidebar_viewport.syncGeometry()
        self.settings_stack.syncGeometry()
        sidebar = self.settings_sidebar
        panel = self.settings_stack.currentWidget()
        if panel is None:
            return

        self.sidebar_viewport.beginMotion()
        self.settings_stack.beginMotion()
        sidebar_end = QPoint(0, 0)
        sidebar_start = QPoint(-max(1, self.sidebar_viewport.width()), 0)
        panel_end = QPoint(0, 0)
        panel_start = QPoint(0, max(1, self.settings_stack.height()))
        sidebar.move(sidebar_start)
        panel.move(panel_start)
        sidebar.show()
        panel.show()
        underlines = panel.findChildren(QFrame, "sectionUnderline")

        group = QParallelAnimationGroup(self)
        group.addAnimation(self._move_animation(
            sidebar, sidebar_start, sidebar_end, 400, QEasingCurve.Type.OutQuart,
        ))
        group.addAnimation(self._move_animation(
            panel, panel_start, panel_end, 450, QEasingCurve.Type.OutQuart,
        ))
        blur = QPropertyAnimation(self.home_blur, b"blurRadius")
        blur.setDuration(440)
        blur.setStartValue(0.0)
        blur.setEndValue(self.SETTINGS_BLUR_RADIUS)
        blur.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(blur)
        scrim_fade = QPropertyAnimation(self.settings_scrim_effect, b"opacity")
        scrim_fade.setDuration(360)
        scrim_fade.setStartValue(0.0)
        scrim_fade.setEndValue(1.0)
        scrim_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(scrim_fade)
        for line in underlines:
            line.setMaximumWidth(0)
            reveal = QPropertyAnimation(line, b"maximumWidth")
            reveal.setDuration(430)
            reveal.setStartValue(0)
            reveal.setEndValue(max(1, self.settings_stack.width()))
            reveal.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(reveal)
        self._shell_animation = group

        def finish() -> None:
            if self._shell_animation is not group:
                return
            sidebar.move(sidebar_end)
            panel.move(panel_end)
            for line in underlines:
                line.setMaximumWidth(16777215)
            self.sidebar_viewport.endMotion()
            self.settings_stack.endMotion()
            self.home_blur.setBlurRadius(self.SETTINGS_BLUR_RADIUS)
            self.settings_scrim_effect.setOpacity(1.0)
            self._shell_animation = None
            self._logical_page = logical_index
            self._pending_logical_page = None
            self._transitioning = False
            group.deleteLater()
            active_nav = self.settings_nav_buttons.get(logical_index)
            if active_nav is not None:
                active_nav.setFocus(Qt.FocusReason.OtherFocusReason)
            if logical_index == 1:
                self.refresh_dashboard()
            self._drain_page_queue()

        group.finished.connect(finish)
        group.start()

    def _enter_settings(self, logical_index: int) -> None:
        self._stop_home_intro()
        self._transitioning = True
        self._pending_logical_page = logical_index
        self.settings_stack.setCurrentIndex(logical_index - 1)
        self._update_settings_nav(logical_index)
        self.settings_sidebar.hide()
        panel = self.settings_stack.currentWidget()
        if panel is not None:
            panel.hide()
        self.home_blur.setBlurRadius(0.0)
        self.home_blur.setEnabled(True)
        self.home_backdrop.setAmbientPaused(True)
        self.pages.setEnabled(False)
        self.settings_scrim_effect.setOpacity(0.0)
        self.settings_overlay.show()
        self.settings_overlay.raise_()
        QTimer.singleShot(0, lambda i=logical_index: self._start_settings_entry(i))

    def _exit_settings(self) -> None:
        sidebar = self.settings_sidebar
        panel = self.settings_stack.currentWidget()
        if panel is None:
            self.settings_overlay.hide()
            self.home_blur.setBlurRadius(0.0)
            self.home_blur.setEnabled(False)
            self.home_backdrop.setAmbientPaused(False)
            self.pages.setEnabled(True)
            self._logical_page = 0
            return
        self._transitioning = True
        self._pending_logical_page = 0
        self.sidebar_viewport.beginMotion()
        self.settings_stack.beginMotion()
        sidebar_start = QPoint(0, 0)
        sidebar_end = QPoint(-max(1, self.sidebar_viewport.width()), 0)
        panel_start = QPoint(0, 0)
        panel_end = QPoint(0, max(1, self.settings_stack.height()))

        group = QParallelAnimationGroup(self)
        group.addAnimation(self._move_animation(
            sidebar, sidebar_start, sidebar_end, 330, QEasingCurve.Type.InCubic,
        ))
        group.addAnimation(self._move_animation(
            panel, panel_start, panel_end, 370, QEasingCurve.Type.InCubic,
        ))
        blur = QPropertyAnimation(self.home_blur, b"blurRadius")
        blur.setDuration(390)
        blur.setStartValue(self.home_blur.blurRadius())
        blur.setEndValue(0.0)
        blur.setEasingCurve(QEasingCurve.Type.InOutCubic)
        group.addAnimation(blur)
        scrim_fade = QPropertyAnimation(self.settings_scrim_effect, b"opacity")
        scrim_fade.setDuration(330)
        scrim_fade.setStartValue(self.settings_scrim_effect.opacity())
        scrim_fade.setEndValue(0.0)
        scrim_fade.setEasingCurve(QEasingCurve.Type.InCubic)
        group.addAnimation(scrim_fade)
        self._shell_animation = group

        def finish() -> None:
            if self._shell_animation is not group:
                return
            self.settings_overlay.hide()
            sidebar.move(0, 0)
            panel.move(0, 0)
            self.sidebar_viewport.endMotion()
            self.settings_stack.endMotion()
            self.home_blur.setBlurRadius(0.0)
            self.home_blur.setEnabled(False)
            self.home_backdrop.setAmbientPaused(False)
            self.pages.setEnabled(True)
            self.settings_scrim_effect.setOpacity(0.0)
            self._shell_animation = None
            self._logical_page = 0
            self._pending_logical_page = None
            self._transitioning = False
            group.deleteLater()
            self.refresh_dashboard()
            self.home_settings_button.setFocus(Qt.FocusReason.OtherFocusReason)
            self._drain_page_queue()

        group.finished.connect(finish)
        group.start()

    def _finish_panel_transition(self, stack_index: int) -> None:
        logical_index = stack_index + 1
        if self._pending_logical_page != logical_index:
            return
        self._logical_page = logical_index
        self._pending_logical_page = None
        self._transitioning = False
        if logical_index == 1:
            self.refresh_dashboard()
        self._drain_page_queue()

    def _confirm_page_change(self, index: int) -> bool:
        if not (
            self.favorite_page is not None
            and self._logical_page == 3
            and index != 3
            and self.favorite_page.dirty
        ):
            return True
        answer = QMessageBox.question(
            self, "未保存の変更",
            "お気に入りに未保存の変更があります。保存してから移動しますか？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            self.favorite_page.save()
            return not self.favorite_page.dirty
        self.favorite_page.discard_changes()
        return True

    def switch_page(self, index: int) -> None:
        if not 0 <= index < len(self.PAGE_META):
            return
        if self._transitioning:
            if index != self._pending_logical_page:
                self._queued_page = index
            visible_index = self._pending_logical_page or self._logical_page
            if visible_index > 0:
                self._update_settings_nav(visible_index)
            return
        source = self._logical_page
        if source == index:
            if source > 0:
                self._update_settings_nav(source)
            if index == 0:
                self.refresh_dashboard()
            return
        if not self._confirm_page_change(index):
            if source > 0:
                self._update_settings_nav(source)
            return
        self._ensure_dynamic_page(index)
        if source == 0:
            self._enter_settings(index)
            return
        if index == 0:
            self._exit_settings()
            return

        self._transitioning = True
        self._pending_logical_page = index
        self._update_settings_nav(index)
        direction = 1 if index > source else -1
        if not self.settings_stack.slideTo(index - 1, direction):
            self._logical_page = index
            self._pending_logical_page = None
            self._transitioning = False
            self._drain_page_queue()

    def _ensure_dynamic_page(self, index: int) -> None:
        if index == 2 and self.song_page is None:
            self.song_page = SongBrowserDialog(self)
            self.song_page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            previous = self.settings_stack.replaceWidget(1, self._wrap_embedded(
                self.song_page, "曲ライブラリ", "導入済みの曲を検索・確認・追加します", 2))
            previous.deleteLater()
        elif index == 3 and self.favorite_page is None:
            self.favorite_page = FavoritesWindow(self)
            self.favorite_page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            previous = self.settings_stack.replaceWidget(2, self._wrap_embedded(
                self.favorite_page, "お気に入り", "お気に入り曲と並び順を管理します", 3))
            previous.deleteLater()

    def _refresh_game_version(self) -> None:
        try:
            executable_stat = GAME_EXE.stat()
            signature = (
                str(GAME_EXE), executable_stat.st_size, executable_stat.st_mtime_ns,
            )
        except OSError:
            signature = (str(GAME_EXE), -1, -1)
        if signature == self._game_version_signature:
            return
        self._game_version_signature = signature

        detected = detect_game_version(GAME_EXE)
        if detected is None:
            if GAME_EXE.is_file():
                self.game_version_label.setText("TAIKONAUTS  •  VERSION 不明")
                self.game_version_label.setToolTip(
                    "TaikoNauts.exe からバージョン情報を取得できませんでした。"
                )
            else:
                self.game_version_label.setText("TAIKONAUTS")
                self.game_version_label.setToolTip("TaikoNauts.exe が見つかっていません。")
            self.game_version_label.setAccessibleName("TaikoNauts バージョン不明")
            return

        self.game_version_label.setText(f"TAIKONAUTS  •  {detected.display}")
        self.game_version_label.setToolTip(
            f"TaikoNauts.exe\n製品バージョン: {detected.full}"
        )
        self.game_version_label.setAccessibleName(
            f"TaikoNauts バージョン {detected.display}"
        )

    def refresh_dashboard(self) -> None:
        self._refresh_game_version()
        config = read_json(CONFIG_PATH)
        skin = str(config.get("skinPath", "")).replace("\\", "/").split("/")[-1] or "未設定"
        player_count = sum(1 for path in PLAYER_DATA_DIR.iterdir() if path.is_dir()) if PLAYER_DATA_DIR.is_dir() else 0
        self.skin_value.setText(skin)
        self.skin_value.setToolTip(skin)
        self.custom_skin_value.setText(skin)
        self.player_value.setText(str(player_count))
        song_root = str(SONGS_DIR.resolve())
        if song_root != self._song_count_root:
            self._song_count_root = song_root
            self._song_count_generation += 1
            self.song_value.setText("…")
            generation = self._song_count_generation
            songs_directory = SONGS_DIR
            QTimer.singleShot(
                750,
                lambda g=generation, root=songs_directory: self._start_song_counter(g, root),
            )
        if GAME_EXE.is_file():
            running = self.game_process is not None and self.game_process.poll() is None
            self.top_status.setText("実行中" if running else "準備完了")
            self.top_status.setObjectName("successPill")
            self.location_button.setText("フォルダーを開く")
            self.home_lead.setText("ゲームを実行しています。" if running else "ゲームを始める準備ができています。")
            self.launch_button.setText("実行中…" if running else "▶   起動する")
            self.launch_button.setEnabled(not running)
        else:
            self.top_status.setText("要設定")
            self.top_status.setObjectName("errorPill")
            self.location_button.setText("ゲームの場所を設定")
            self.home_lead.setText("最初に TaikoNauts.exe の場所を設定してください。")
            self.launch_button.setText("▶   起動する")
            self.launch_button.setEnabled(False)
        self.top_status.style().unpolish(self.top_status)
        self.top_status.style().polish(self.top_status)

    def _start_song_counter(self, generation: int, root: Path) -> None:
        if generation != self._song_count_generation:
            return
        try:
            root_key = str(root.resolve())
        except OSError:
            root_key = str(root)
        if root_key != self._song_count_root:
            return
        if self._transitioning:
            QTimer.singleShot(
                300,
                lambda g=generation, song_root=root: self._start_song_counter(g, song_root),
            )
            return
        counter = SongCountThread(root, self)
        counter.counted.connect(self._set_song_count)
        counter.finished.connect(lambda: self._finish_song_counter(counter))
        self._song_count_threads.append(counter)
        counter.start(QThread.Priority.LowPriority)

    def _set_song_count(self, root: str, count: int) -> None:
        if root == self._song_count_root:
            self.song_value.setText(str(count))

    def _finish_song_counter(self, counter: SongCountThread) -> None:
        if counter in self._song_count_threads:
            self._song_count_threads.remove(counter)
        counter.deleteLater()

    def check_game_process(self) -> None:
        if self.game_process is not None and self.game_process.poll() is not None:
            self.game_timer.stop()
            self.game_process = None
            self.refresh_dashboard()

    def open_skin_dialog(self) -> None:
        dialog = SkinDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh_dashboard()

    def open_nameplate_dialog(self) -> None:
        NamePlateDialog(self).exec()
        self.refresh_dashboard()

    def open_base_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(BASE_DIR)))

    def handle_location_action(self) -> None:
        if GAME_EXE.is_file():
            self.open_base_folder()
        else:
            self.select_game_location()

    def select_game_location(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "TaikoNauts.exe の場所を選択", str(BASE_DIR),
            "TaikoNauts (TaikoNauts.exe);;実行ファイル (*.exe)")
        if not selected:
            return
        executable = Path(selected)
        if executable.name.lower() != "taikonauts.exe":
            QMessageBox.warning(
                self, "ファイルが違います",
                "TaikoNauts.exe を選択してください。\n選んだファイルは変更されていません。")
            return
        set_base_dir(executable.parent)
        try:
            write_json(LAUNCHER_SETTINGS_PATH, {"gameDirectory": str(BASE_DIR)})
        except OSError as exc:
            QMessageBox.warning(self, "設定を保存できませんでした", str(exc))
        self._rebuild_data_pages()
        self.base_path_label.setText(str(BASE_DIR))
        self.base_path_label.setToolTip(str(BASE_DIR))
        self.switch_page(0)

    def _rebuild_data_pages(self) -> None:
        if self.song_page is not None:
            self.song_page.shutdown()
        replacements = [
            self._build_custom_page(),
            self._settings_panel(
                self._placeholder_page("曲ライブラリを準備しています…"),
                "曲ライブラリ", "導入済みの曲を検索・確認・追加します",
            ),
            self._settings_panel(
                self._placeholder_page("お気に入りエディターを準備しています…"),
                "お気に入り", "お気に入り曲と並び順を管理します",
            ),
        ]
        for index, replacement in enumerate(replacements):
            previous = self.settings_stack.replaceWidget(index, replacement)
            previous.deleteLater()
        self.song_page = None
        self.favorite_page = None

    def closeEvent(self, event) -> None:
        if self.favorite_page is not None and self.favorite_page.dirty:
            answer = QMessageBox.question(
                self, "未保存の変更",
                "お気に入りの変更を保存してから終了しますか？",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                self.favorite_page.save()
                if self.favorite_page.dirty:
                    event.ignore()
                    return
        if self.song_page is not None:
            self.song_page.shutdown()
        for counter in self._song_count_threads:
            if counter.isRunning():
                counter.requestInterruption()
                counter.wait(1500)
        super().closeEvent(event)

    def launch_game(self) -> None:
        if self.game_process is not None and self.game_process.poll() is None:
            return
        if not GAME_EXE.is_file():
            QMessageBox.critical(
                self, "ゲームが見つかりません",
                f"TaikoNauts.exe をランチャーと同じフォルダーに置いてください。\n\n参照先:\n{GAME_EXE}")
            return
        try:
            self.game_process = subprocess.Popen([str(GAME_EXE)], cwd=BASE_DIR)
        except OSError as exc:
            QMessageBox.critical(self, "起動できませんでした", str(exc))
            return
        self.game_timer.start()
        self.refresh_dashboard()
        autostart_errors = []
        if AUTOSTART_DIR.is_dir():
            for path in sorted(AUTOSTART_DIR.iterdir()):
                if not path.is_file():
                    continue
                try:
                    if path.suffix.lower() == ".exe":
                        subprocess.Popen([str(path)], cwd=AUTOSTART_DIR)
                    elif path.suffix.lower() == ".pyw":
                        subprocess.Popen(["pythonw", str(path)], cwd=AUTOSTART_DIR)
                    elif path.suffix.lower() == ".py":
                        subprocess.Popen(["python", str(path)], cwd=AUTOSTART_DIR)
                except OSError as exc:
                    autostart_errors.append(f"{path.name}: {exc}")
        if autostart_errors:
            QMessageBox.warning(
                self, "関連ファイルの一部を起動できませんでした",
                "ゲームは起動しましたが、次のファイルは起動できませんでした。\n\n"
                + "\n".join(autostart_errors))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("TaikoNauts Launcher")
    if LOGO_IMAGE_PATH.is_file():
        app.setWindowIcon(QIcon(str(LOGO_IMAGE_PATH)))
    app.setStyle("Fusion")
    load_application_font(app)
    app.setStyleSheet(FLARIAL_STYLE)
    window = Launcher()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
