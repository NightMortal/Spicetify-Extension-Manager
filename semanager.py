import sys
import os
import configparser
import subprocess
import zipfile
import json
import requests
import re
import time
import base64
import hashlib
from cryptography.fernet import Fernet
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QMessageBox, QCheckBox, QScrollArea, QFileDialog, QLineEdit, QTabWidget,
    QToolButton, QMenuBar, QAction, QInputDialog, QListWidget, QListWidgetItem,
    QTextEdit, QDesktopWidget, QComboBox, QGridLayout, QMenu, QHeaderView, QTableWidget,
    QTableWidgetItem
)
from PyQt5.QtCore import Qt, QSettings, QUrl, QDateTime
from PyQt5.QtGui import QIcon, QDesktopServices
from PyQt5.Qsci import QsciScintilla, QsciLexerProperties

class RateLimiter:
    """
    Simple rate limiter to limit API requests.
    """
    def __init__(self, calls_per_interval, interval):
        self.calls_per_interval = calls_per_interval
        self.interval = interval
        self.calls = []
    
    def wait(self):
        now = time.time()
        self.calls = [t for t in self.calls if now - t < self.interval]
        if len(self.calls) >= self.calls_per_interval:
            sleep_time = self.interval - (now - self.calls[0])
            time.sleep(sleep_time)
        self.calls.append(time.time())

class SpicetifyManager(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spicetify Extension Manager")
        self.resize(800, 600)
        self.center()

        self.settings = QSettings("SpicetifyManager", "Settings")
        self.theme = self.settings.value("theme", "Dark")
        self.first_launch = self.settings.value("first_launch", True, type=bool)
        self.visible_tabs = self.settings.value("visible_tabs", {
            "Extensions": True,
            "Themes": True,
            "Custom Apps": True,
            "Marketplace": True,
            "Advanced Settings": True,
            "Settings": False
        })
        self.custom_repos = self.settings.value("custom_repos", [])
        self.encrypted_token = self.settings.value("encrypted_token", "")
        self.encryption_key = None  # Will be derived from a user-provided password

        # Initialize rate limiter for GitHub API (60 requests per hour for unauthenticated requests)
        self.rate_limiter = RateLimiter(calls_per_interval=60, interval=3600)

        self.config_path = self.get_config_path()
        self.config = configparser.ConfigParser(strict=False)
        self.config.optionxform = str  # Preserve case sensitivity
        self.config_loaded = False  # Flag to check if config is loaded

        if self.config_path:
            self.load_config()
            self.config_loaded = True

        if self.first_launch:
            self.detect_system_theme()

        self.init_ui()
        self.apply_theme(self.theme)

        # Perform update check after GUI has loaded
        self.check_for_updates(startup=True)

    def center(self):
        frame = self.frameGeometry()
        center_point = QDesktopWidget().availableGeometry().center()
        frame.moveCenter(center_point)
        self.move(frame.topLeft())

    def detect_system_theme(self):
        # Detect system theme (Simplified detection)
        import platform

        if platform.system() == "Windows":
            # Windows theme detection requires registry access
            try:
                import winreg
                registry = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER)
                key = winreg.OpenKey(registry, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
                value, regtype = winreg.QueryValueEx(key, "AppsUseLightTheme")
                winreg.CloseKey(key)
                self.theme = "Light" if value == 1 else "Dark"
            except Exception:
                self.theme = "Light"
        else:
            # Default to Light theme for other systems
            self.theme = "Light"

        self.settings.setValue("theme", self.theme)
        self.settings.setValue("first_launch", False)

    def get_config_path(self):
        try:
            result = subprocess.run(['spicetify', '-c'], capture_output=True, text=True, check=True)
            config_path = result.stdout.strip()
            if os.path.isfile(config_path):
                return config_path
            else:
                return None
        except Exception as e:
            # If spicetify command is not found or an error occurs
            home = os.path.expanduser("~")
            possible_paths = [
                os.path.join(home, ".spicetify", "config.ini"),
                os.path.join(home, ".config", "spicetify", "config.ini"),
                os.path.join(home, "AppData", "Roaming", "spicetify", "config.ini"),
            ]
            for path in possible_paths:
                if os.path.isfile(path):
                    return path
            return None

    def load_config(self):
        self.config.read(self.config_path)
        extensions_line = self.config.get('AdditionalOptions', 'extensions', fallback='')
        self.extensions = [ext.strip() for ext in extensions_line.split('|') if ext.strip()]
        self.extensions_dir = self.config.get('AdditionalOptions', 'extensions_folder', fallback='')
        if not self.extensions_dir:
            # Default extensions directory
            self.extensions_dir = os.path.join(os.path.dirname(self.config_path), "Extensions")
        if not os.path.isdir(self.extensions_dir):
            os.makedirs(self.extensions_dir)

    def init_ui(self):
        main_layout = QVBoxLayout()

        # Menu Bar
        menu_bar = QMenuBar()
        menu_bar.setStyleSheet("""
            QMenuBar {
                background-color: #2d2d2d;
                color: #ffffff;
            }
            QMenuBar::item {
                background-color: #2d2d2d;
                color: #ffffff;
            }
            QMenuBar::item:selected {
                background-color: #3d3d3d;
            }
            QMenu {
                background-color: #2d2d2d;
                color: #ffffff;
            }
            QMenu::item:selected {
                background-color: #3d3d3d;
            }
        """)
        settings_menu = menu_bar.addMenu('Settings')
        preferences_action = QAction('Preferences', self)
        preferences_action.triggered.connect(self.open_settings_tab)
        settings_menu.addAction(preferences_action)

        # Toggle Tabs
        toggle_tabs_menu = settings_menu.addMenu('Toggle Tabs')
        self.tab_actions = {}
        for tab_name in ["Extensions", "Themes", "Custom Apps", "Marketplace", "Advanced Settings", "Settings"]:
            action = QAction(tab_name, self, checkable=True)
            action.setChecked(self.visible_tabs.get(tab_name, True))
            action.triggered.connect(self.update_tab_visibility)
            toggle_tabs_menu.addAction(action)
            self.tab_actions[tab_name] = action

        backup_menu = menu_bar.addMenu('Backup')
        backup_action = QAction('Backup Configuration', self)
        backup_action.triggered.connect(self.backup_configuration)
        restore_action = QAction('Restore Configuration', self)
        restore_action.triggered.connect(self.restore_configuration)
        backup_menu.addAction(backup_action)
        backup_menu.addAction(restore_action)

        update_menu = menu_bar.addMenu('Help')
        check_update_action = QAction('Check for Updates', self)
        check_update_action.triggered.connect(self.check_for_updates)
        update_menu.addAction(check_update_action)

        main_layout.setMenuBar(menu_bar)

        # Top Bar Layout
        top_bar_layout = QHBoxLayout()
        self.directory_input = QLineEdit()
        if self.config_loaded:
            self.directory_input.setText(self.config_path)
        else:
            self.directory_input.setPlaceholderText("Enter path to config.ini")

        # Add '...' button inside the QLineEdit
        browse_button = QToolButton()
        browse_button.setIcon(self.style().standardIcon(QApplication.style().SP_DirIcon))
        browse_button.setCursor(Qt.ArrowCursor)
        browse_button.setStyleSheet("QToolButton { border: none; padding: 0px; }")
        browse_button.clicked.connect(self.browse_config)
        self.directory_input.setTextMargins(0, 0, browse_button.sizeHint().width(), 0)
        browse_button.setParent(self.directory_input)
        browse_button.move(self.directory_input.rect().right() - browse_button.sizeHint().width(), 0)

        self.directory_input.textChanged.connect(self.adjust_browse_button_position)
        self.directory_input.resizeEvent = self.on_directory_input_resize

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_all)
        top_bar_layout.addWidget(self.directory_input)
        top_bar_layout.addWidget(refresh_button)
        main_layout.addLayout(top_bar_layout)

        # Tabs for functionalities
        self.tabs = DraggableTabWidget()
        self.extensions_tab = QWidget()
        self.themes_tab = QWidget()
        self.custom_apps_tab = QWidget()
        self.marketplace_tab = QWidget()
        self.advanced_settings_tab = QWidget()
        self.settings_tab = QWidget()

        self.tab_widgets = {
            "Extensions": self.extensions_tab,
            "Themes": self.themes_tab,
            "Custom Apps": self.custom_apps_tab,
            "Marketplace": self.marketplace_tab,
            "Advanced Settings": self.advanced_settings_tab,
            "Settings": self.settings_tab
        }

        self.init_extensions_tab()
        self.init_themes_tab()
        self.init_custom_apps_tab()
        self.init_marketplace_tab()
        self.init_advanced_settings_tab()
        self.init_settings_tab()

        for tab_name, widget in self.tab_widgets.items():
            if self.visible_tabs.get(tab_name, True):
                self.tabs.addTab(widget, tab_name)

        main_layout.addWidget(self.tabs)

        # Log Window Label
        log_label = QLabel("Log Output:")
        main_layout.addWidget(log_label)

        # Log Window
        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        main_layout.addWidget(self.log_text_edit)

        self.setLayout(main_layout)

    def adjust_browse_button_position(self):
        browse_button = self.directory_input.findChild(QToolButton)
        if browse_button:
            browse_button.move(self.directory_input.rect().right() - browse_button.sizeHint().width(), 0)

    def on_directory_input_resize(self, event):
        QLineEdit.resizeEvent(self.directory_input, event)
        self.adjust_browse_button_position()

    def open_settings_tab(self):
        if "Settings" not in [self.tabs.tabText(i) for i in range(self.tabs.count())]:
            self.tabs.addTab(self.settings_tab, "Settings")
            index = self.tabs.indexOf(self.settings_tab)
            self.tabs.setCurrentIndex(index)
            self.visible_tabs["Settings"] = True
            self.tab_actions["Settings"].setChecked(True)
            self.settings.setValue("visible_tabs", self.visible_tabs)
        else:
            index = self.tabs.indexOf(self.settings_tab)
            self.tabs.setCurrentIndex(index)

    def update_tab_visibility(self):
        sender = self.sender()
        tab_name = sender.text()
        if sender.isChecked():
            if tab_name not in [self.tabs.tabText(i) for i in range(self.tabs.count())]:
                self.tabs.addTab(self.tab_widgets[tab_name], tab_name)
        else:
            index = self.tabs.indexOf(self.tab_widgets[tab_name])
            if index != -1:
                self.tabs.removeTab(index)
            # Uncheck the action
            self.tab_actions[tab_name].setChecked(False)
        self.visible_tabs[tab_name] = sender.isChecked()
        self.settings.setValue("visible_tabs", self.visible_tabs)

    def apply_theme(self, theme):
        if theme.lower() == "dark":
            self.apply_dark_theme()
        else:
            self.apply_light_theme()

    def apply_dark_theme(self):
        dark_stylesheet = """
            QWidget {
                background-color: #121212;
                color: #ffffff;
            }
            QLineEdit, QPushButton, QScrollArea, QComboBox, QListWidget, QTextEdit, QTableWidget {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #3a3a3a;
            }
            QPushButton:hover {
                background-color: #2e2e2e;
            }
            QPushButton:pressed {
                background-color: #3e3e3e;
            }
            QTabBar::tab {
                background-color: #1e1e1e;
                color: #ffffff;
                padding: 10px;
            }
            QTabBar::tab:selected {
                background-color: #323232;
            }
            QCheckBox {
                background-color: transparent;
                padding: 5px;
            }
            QCheckBox::indicator {
                border: 1px solid #3a3a3a;
                width: 15px;
                height: 15px;
            }
            QCheckBox::indicator:checked {
                background-color: #009688;
                border: 1px solid #009688;
            }
            QMessageBox {
                background-color: #1e1e1e;
                color: #ffffff;
            }
            QToolButton {
                background-color: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background-color: #1e1e1e;
                width: 15px;
                margin: 15px 3px 15px 3px;
                border: 1px solid #3a3a3a;
            }
            QScrollBar::handle:vertical {
                background-color: #757575;
                min-height: 5px;
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QTextEdit {
                background-color: #1e1e1e;
                color: #ffffff;
            }
            QMenuBar {
                background-color: #2d2d2d;
                color: #ffffff;
            }
            QMenuBar::item {
                background-color: #2d2d2d;
                color: #ffffff;
            }
            QMenuBar::item:selected {
                background-color: #3d3d3d;
            }
            QMenu {
                background-color: #2d2d2d;
                color: #ffffff;
            }
            QMenu::item:selected {
                background-color: #3d3d3d;
            }
            QHeaderView::section {
                background-color: #1e1e1e;
                color: #ffffff;
            }
        """
        self.setStyleSheet(dark_stylesheet)

    def apply_light_theme(self):
        light_stylesheet = """
            QWidget {
                background-color: #f0f0f0;
                color: #000000;
            }
            QLineEdit, QPushButton, QScrollArea, QComboBox, QListWidget, QTextEdit, QTableWidget {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #cccccc;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:pressed {
                background-color: #d0d0d0;
            }
            QTabBar::tab {
                background-color: #e0e0e0;
                color: #000000;
                padding: 10px;
            }
            QTabBar::tab:selected {
                background-color: #d0d0d0;
            }
            QCheckBox {
                background-color: transparent;
                padding: 5px;
            }
            QCheckBox::indicator {
                border: 1px solid #000000;
                width: 15px;
                height: 15px;
            }
            QCheckBox::indicator:checked {
                background-color: #009688;
                border: 1px solid #009688;
            }
            QMessageBox {
                background-color: #ffffff;
                color: #000000;
            }
            QToolButton {
                background-color: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background-color: #f0f0f0;
                width: 15px;
                margin: 15px 3px 15px 3px;
                border: 1px solid #cccccc;
            }
            QScrollBar::handle:vertical {
                background-color: #cccccc;
                min-height: 5px;
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QTextEdit {
                background-color: #ffffff;
                color: #000000;
            }
            QMenuBar {
                background-color: #e0e0e0;
                color: #000000;
            }
            QMenuBar::item {
                background-color: #e0e0e0;
                color: #000000;
            }
            QMenuBar::item:selected {
                background-color: #d0d0d0;
            }
            QMenu {
                background-color: #ffffff;
                color: #000000;
            }
            QMenu::item:selected {
                background-color: #d0d0d0;
            }
            QHeaderView::section {
                background-color: #e0e0e0;
                color: #000000;
            }
        """
        self.setStyleSheet(light_stylesheet)

    def browse_config(self):
        options = QFileDialog.Options()
        options |= QFileDialog.ReadOnly
        file_path, _ = QFileDialog.getOpenFileName(self, "Select config.ini", "", "Config Files (config.ini);;All Files (*)", options=options)
        if file_path:
            self.directory_input.setText(file_path)
            self.refresh_all()

    def refresh_all(self):
        config_path = self.directory_input.text().strip()
        if config_path and os.path.isfile(config_path):
            self.config_path = config_path
            self.load_config()
            self.config_loaded = True
            self.apply_button.setEnabled(True)
            self.populate_extensions()
            self.load_themes()
            self.load_custom_apps()
            self.load_config_into_editor()
        else:
            QMessageBox.warning(self, "Invalid Path", "Please enter a valid path to config.ini.")
            return
        QMessageBox.information(self, "Refreshed", "Configuration reloaded.")

    def log(self, message):
        # Strip ANSI escape codes
        ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
        clean_message = ansi_escape.sub('', message)
        self.log_text_edit.append(clean_message)

    # Extension Tab
    def init_extensions_tab(self):
        ext_layout = QVBoxLayout()

        # Search Bar
        search_layout = QHBoxLayout()
        self.extension_search_input = QLineEdit()
        self.extension_search_input.setPlaceholderText("Search Extensions")
        self.extension_search_input.textChanged.connect(self.filter_extensions)
        sort_label = QLabel("Sort by:")
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Name", "Date Modified"])
        self.sort_combo.currentTextChanged.connect(self.populate_extensions)
        search_layout.addWidget(self.extension_search_input)
        search_layout.addStretch()
        search_layout.addWidget(sort_label)
        search_layout.addWidget(self.sort_combo)
        ext_layout.addLayout(search_layout)

        # Table for extensions
        self.extensions_table = QTableWidget()
        self.extensions_table.setColumnCount(4)
        self.extensions_table.setHorizontalHeaderLabels(["Select", "Name", "Size (KB)", "Last Modified"])
        header = self.extensions_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)

        if self.config_loaded:
            self.populate_extensions()
        else:
            # Display message prompting to set config path
            message_label = QLabel("Config.ini not found. Please set the config path using the top bar.")
            message_label.setAlignment(Qt.AlignCenter)
            ext_layout.addWidget(message_label)

        ext_layout.addWidget(self.extensions_table)

        # Buttons
        button_layout = QHBoxLayout()
        self.apply_button = QPushButton("Apply Changes")
        self.apply_button.clicked.connect(self.apply_changes)
        self.apply_button.setEnabled(self.config_loaded)
        button_layout.addStretch()
        button_layout.addWidget(self.apply_button)
        ext_layout.addLayout(button_layout)

        self.extensions_tab.setLayout(ext_layout)

    def populate_extensions(self):
        if not os.path.isdir(self.extensions_dir):
            return

        all_extensions = self.get_all_extensions()

        # Filter extensions based on search input
        search_text = self.extension_search_input.text().lower()
        if search_text:
            all_extensions = [ext for ext in all_extensions if search_text in ext['name'].lower()]

        # Sort extensions
        sort_by = self.sort_combo.currentText()
        if sort_by == "Name":
            all_extensions.sort(key=lambda x: x['name'].lower())
        elif sort_by == "Date Modified":
            all_extensions.sort(key=lambda x: x['last_modified'], reverse=True)

        self.extensions_table.setRowCount(len(all_extensions))

        for row, ext in enumerate(all_extensions):
            # Checkbox
            checkbox_item = QTableWidgetItem()
            checkbox_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            if ext['name'] in self.extensions:
                checkbox_item.setCheckState(Qt.Checked)
            else:
                checkbox_item.setCheckState(Qt.Unchecked)
            self.extensions_table.setItem(row, 0, checkbox_item)

            # Name
            name_item = QTableWidgetItem(ext['name'])
            name_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.extensions_table.setItem(row, 1, name_item)

            # Size
            size_item = QTableWidgetItem(f"{ext['size']:.2f}")
            size_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.extensions_table.setItem(row, 2, size_item)

            # Last Modified
            last_modified_item = QTableWidgetItem(ext['last_modified'].toString("yyyy-MM-dd HH:mm:ss"))
            last_modified_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.extensions_table.setItem(row, 3, last_modified_item)

    def get_all_extensions(self):
        files = os.listdir(self.extensions_dir)
        extensions = []
        for file in files:
            if file.endswith('.js'):
                file_path = os.path.join(self.extensions_dir, file)
                stat = os.stat(file_path)
                size = stat.st_size / 1024  # Size in KB
                last_modified = QDateTime.fromSecsSinceEpoch(int(stat.st_mtime))
                extensions.append({
                    'name': file,
                    'size': size,
                    'last_modified': last_modified
                })
        return extensions

    def filter_extensions(self):
        self.populate_extensions()

    def apply_changes(self):
        if not self.config_loaded:
            QMessageBox.warning(self, "Config Not Loaded", "Please set the config path first.")
            return

        selected_extensions = []
        for row in range(self.extensions_table.rowCount()):
            item = self.extensions_table.item(row, 0)
            name_item = self.extensions_table.item(row, 1)
            if item.checkState() == Qt.Checked:
                selected_extensions.append(name_item.text())

        extensions_line = '|'.join(selected_extensions)
        self.config.set('AdditionalOptions', 'extensions', extensions_line)

        with open(self.config_path, 'w') as configfile:
            self.config.write(configfile)

        # Run spicetify apply
        self.apply_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.BusyCursor)
        try:
            process = subprocess.run(['spicetify', 'apply'], capture_output=True, text=True, check=True)
            QApplication.restoreOverrideCursor()
            self.log(process.stdout)
            QMessageBox.information(self, "Success", "Changes applied and Spicetify refreshed.")
        except subprocess.CalledProcessError as e:
            QApplication.restoreOverrideCursor()
            self.log(e.stdout)
            self.log(e.stderr)
            QMessageBox.critical(self, "Error", f"Failed to apply changes: {e}")
        except FileNotFoundError:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Error", "Spicetify command not found. Please ensure Spicetify is installed and in your system PATH.")
        finally:
            self.apply_button.setEnabled(True)
            QApplication.processEvents()

    # Themes Tab
    def init_themes_tab(self):
        themes_layout = QVBoxLayout()

        # List of themes
        self.themes_list_widget = QListWidget()
        self.load_themes()
        themes_layout.addWidget(self.themes_list_widget)

        # Buttons
        button_layout = QHBoxLayout()
        self.apply_theme_button = QPushButton("Apply Theme")
        self.apply_theme_button.clicked.connect(self.apply_selected_theme)
        self.install_theme_button = QPushButton("Install Theme")
        self.install_theme_button.clicked.connect(self.install_theme)
        button_layout.addStretch()
        button_layout.addWidget(self.apply_theme_button)
        button_layout.addWidget(self.install_theme_button)
        themes_layout.addLayout(button_layout)

        # Theme Preview (Placeholder)
        self.theme_preview = QLabel("Theme Preview (Not Implemented)")
        self.theme_preview.setAlignment(Qt.AlignCenter)
        themes_layout.addWidget(self.theme_preview)

        self.themes_tab.setLayout(themes_layout)

    def load_themes(self):
        # Clear existing items
        self.themes_list_widget.clear()
        # Get list of themes
        themes_dir = os.path.join(os.path.dirname(self.config_path), 'Themes')
        if not os.path.isdir(themes_dir):
            os.makedirs(themes_dir)
        themes = [d for d in os.listdir(themes_dir) if os.path.isdir(os.path.join(themes_dir, d))]
        for theme in themes:
            self.themes_list_widget.addItem(theme)

    def apply_selected_theme(self):
        selected_items = self.themes_list_widget.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Theme Selected", "Please select a theme to apply.")
            return
        theme_name = selected_items[0].text()
        self.config.set('Settings', 'current_theme', theme_name)
        with open(self.config_path, 'w') as configfile:
            self.config.write(configfile)
        # Run spicetify apply
        self.apply_theme_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.BusyCursor)
        try:
            subprocess.run(['spicetify', 'config', 'current_theme', theme_name], check=True)
            process = subprocess.run(['spicetify', 'apply'], capture_output=True, text=True, check=True)
            QApplication.restoreOverrideCursor()
            self.log(process.stdout)
            QMessageBox.information(self, "Success", f"Theme '{theme_name}' applied successfully.")
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self.log(str(e))
            QMessageBox.critical(self, "Error", f"Failed to apply theme: {e}")
        finally:
            self.apply_theme_button.setEnabled(True)
            QApplication.processEvents()

    def install_theme(self):
        url, ok = QInputDialog.getText(self, 'Install Theme', 'Enter GitHub repository URL:')
        if ok and url:
            try:
                # Clone the repository into the Themes directory
                themes_dir = os.path.join(os.path.dirname(self.config_path), 'Themes')
                subprocess.run(['git', 'clone', url], cwd=themes_dir, check=True)
                self.load_themes()
                QMessageBox.information(self, "Success", "Theme installed successfully.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to install theme: {e}")

    # Custom Apps Tab
    def init_custom_apps_tab(self):
        apps_layout = QVBoxLayout()

        # List of custom apps
        self.apps_list_widget = QListWidget()
        self.load_custom_apps()
        apps_layout.addWidget(self.apps_list_widget)

        # Buttons
        button_layout = QHBoxLayout()
        self.apply_apps_button = QPushButton("Apply Changes")
        self.apply_apps_button.clicked.connect(self.apply_custom_apps)
        self.install_app_button = QPushButton("Install App")
        self.install_app_button.clicked.connect(self.install_custom_app)
        button_layout.addStretch()
        button_layout.addWidget(self.apply_apps_button)
        button_layout.addWidget(self.install_app_button)
        apps_layout.addLayout(button_layout)

        self.custom_apps_tab.setLayout(apps_layout)

    def load_custom_apps(self):
        # Clear existing items
        self.apps_list_widget.clear()
        # Get list of custom apps
        apps_dir = os.path.join(os.path.dirname(self.config_path), 'CustomApps')
        if not os.path.isdir(apps_dir):
            os.makedirs(apps_dir)
        apps = [d for d in os.listdir(apps_dir) if os.path.isdir(os.path.join(apps_dir, d))]
        for app in apps:
            item = QListWidgetItem(app)
            item.setCheckState(Qt.Checked if app in self.get_enabled_custom_apps() else Qt.Unchecked)
            self.apps_list_widget.addItem(item)

    def get_enabled_custom_apps(self):
        apps_line = self.config.get('AdditionalOptions', 'custom_apps', fallback='')
        enabled_apps = [app.strip() for app in apps_line.split(',') if app.strip()]
        return enabled_apps

    def apply_custom_apps(self):
        enabled_apps = []
        for index in range(self.apps_list_widget.count()):
            item = self.apps_list_widget.item(index)
            if item.checkState() == Qt.Checked:
                enabled_apps.append(item.text())
        apps_line = ','.join(enabled_apps)
        self.config.set('AdditionalOptions', 'custom_apps', apps_line)
        with open(self.config_path, 'w') as configfile:
            self.config.write(configfile)
        # Run spicetify apply
        self.apply_apps_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.BusyCursor)
        try:
            subprocess.run(['spicetify', 'config', 'custom_apps', apps_line], check=True)
            process = subprocess.run(['spicetify', 'apply'], capture_output=True, text=True, check=True)
            QApplication.restoreOverrideCursor()
            self.log(process.stdout)
            QMessageBox.information(self, "Success", "Custom apps updated successfully.")
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self.log(str(e))
            QMessageBox.critical(self, "Error", f"Failed to apply custom apps: {e}")
        finally:
            self.apply_apps_button.setEnabled(True)
            QApplication.processEvents()

    def install_custom_app(self):
        url, ok = QInputDialog.getText(self, 'Install Custom App', 'Enter GitHub repository URL:')
        if ok and url:
            try:
                # Clone the repository into the CustomApps directory
                apps_dir = os.path.join(os.path.dirname(self.config_path), 'CustomApps')
                subprocess.run(['git', 'clone', url], cwd=apps_dir, check=True)
                self.load_custom_apps()
                QMessageBox.information(self, "Success", "Custom app installed successfully.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to install custom app: {e}")

    # Marketplace Tab
    def init_marketplace_tab(self):
        layout = QVBoxLayout()

        # Search Bar
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search Extensions")
        search_button = QPushButton("Search")
        search_button.clicked.connect(self.search_marketplace)
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(search_button)
        layout.addLayout(search_layout)

        # List of Extensions
        self.marketplace_list_widget = QListWidget()
        layout.addWidget(self.marketplace_list_widget)

        # Install Button
        install_button = QPushButton("Install Selected Extension")
        install_button.clicked.connect(self.install_marketplace_extension)
        layout.addWidget(install_button)

        self.marketplace_tab.setLayout(layout)

    def search_marketplace(self):
        query = self.search_input.text().lower()
        self.marketplace_list_widget.clear()

        repositories = [
            'https://api.github.com/repos/spicetify/spicetify-extensions/contents/Extensions',
            # Add custom repositories
        ] + self.custom_repos

        headers = {}
        token = self.get_github_token()
        if token:
            headers['Authorization'] = f'token {token}'

        try:
            for repo in repositories:
                # Rate limiting
                self.rate_limiter.wait()

                response = requests.get(repo, headers=headers)
                if response.status_code == 200:
                    items = response.json()
                    for item in items:
                        if item['name'].endswith('.js') and query in item['name'].lower():
                            list_item = QListWidgetItem(item['name'])
                            list_item.setData(Qt.UserRole, item['download_url'])
                            self.marketplace_list_widget.addItem(list_item)
                else:
                    raise Exception(f"Failed to fetch extensions: {response.status_code} {response.reason}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to fetch extensions: {e}")

    def install_marketplace_extension(self):
        selected_items = self.marketplace_list_widget.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Extension Selected", "Please select an extension to install.")
            return
        extension_name = selected_items[0].text()
        download_url = selected_items[0].data(Qt.UserRole)

        headers = {}
        token = self.get_github_token()
        if token:
            headers['Authorization'] = f'token {token}'

        try:
            response = requests.get(download_url, headers=headers)
            if response.status_code == 200:
                extensions_dir = os.path.join(os.path.dirname(self.config_path), 'Extensions')
                with open(os.path.join(extensions_dir, extension_name), 'w', encoding='utf-8') as file:
                    file.write(response.text)
                self.populate_extensions()
                QMessageBox.information(self, "Success", f"Extension '{extension_name}' installed successfully.")
            else:
                raise Exception(f"Failed to download extension: {response.status_code} {response.reason}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to install extension: {e}")

    # Advanced Settings Tab
    def init_advanced_settings_tab(self):
        layout = QVBoxLayout()
        self.config_editor = QsciScintilla()
        lexer = QsciLexerProperties()
        self.config_editor.setLexer(lexer)
        self.load_config_into_editor()
        layout.addWidget(self.config_editor)

        # Save Button
        save_button_layout = QHBoxLayout()
        save_button = QPushButton("Save Config")
        save_button.clicked.connect(self.save_config_from_editor)
        save_button_layout.addStretch()
        save_button_layout.addWidget(save_button)
        layout.addLayout(save_button_layout)

        self.advanced_settings_tab.setLayout(layout)

    def load_config_into_editor(self):
        with open(self.config_path, 'r') as file:
            config_content = file.read()
        self.config_editor.setText(config_content)

    def save_config_from_editor(self):
        config_content = self.config_editor.text()
        with open(self.config_path, 'w') as file:
            file.write(config_content)
        QMessageBox.information(self, "Success", "Config saved successfully.")

    # Settings Tab
    def init_settings_tab(self):
        layout = QVBoxLayout()

        # Remove the "What's This" button by setting the appropriate window flags
        self.settings_tab.setWindowFlags(self.settings_tab.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        # Theme Selection
        theme_layout = QHBoxLayout()
        theme_label = QLabel("Select Theme:")
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Dark", "Light"])
        current_index = self.theme_combo.findText(self.theme, Qt.MatchFixedString)
        if current_index >= 0:
            self.theme_combo.setCurrentIndex(current_index)
        self.theme_combo.currentTextChanged.connect(self.change_theme)
        theme_layout.addWidget(theme_label)
        theme_layout.addWidget(self.theme_combo)
        layout.addLayout(theme_layout)

        # GitHub Authentication
        auth_layout = QVBoxLayout()
        auth_label = QLabel("GitHub Authentication (Optional):")
        auth_layout.addWidget(auth_label)
        token_layout = QHBoxLayout()
        token_label = QLabel("Personal Access Token:")
        self.token_input = QLineEdit()
        self.token_input.setEchoMode(QLineEdit.Password)
        if self.encrypted_token:
            self.token_input.setPlaceholderText("Token is set")
        token_layout.addWidget(token_label)
        token_layout.addWidget(self.token_input)
        auth_layout.addLayout(token_layout)
        save_token_button = QPushButton("Save Token")
        save_token_button.clicked.connect(self.save_github_token)
        auth_layout.addWidget(save_token_button)
        layout.addLayout(auth_layout)

        # Custom Repositories
        repos_layout = QVBoxLayout()
        repos_label = QLabel("Custom Repositories:")
        repos_layout.addWidget(repos_label)
        self.repos_list_widget = QListWidget()
        self.load_custom_repos()
        repos_layout.addWidget(self.repos_list_widget)
        repos_button_layout = QHBoxLayout()
        add_repo_button = QPushButton("Add Repository")
        add_repo_button.clicked.connect(self.add_custom_repo)
        remove_repo_button = QPushButton("Remove Selected")
        remove_repo_button.clicked.connect(self.remove_custom_repo)
        repos_button_layout.addWidget(add_repo_button)
        repos_button_layout.addWidget(remove_repo_button)
        repos_layout.addLayout(repos_button_layout)
        layout.addLayout(repos_layout)

        layout.addStretch()
        self.settings_tab.setLayout(layout)

    def save_github_token(self):
        token = self.token_input.text()
        if token:
            # Ask for a password to encrypt the token
            password, ok = QInputDialog.getText(self, 'Set Encryption Password', 'Enter a password to encrypt the token:', QLineEdit.Password)
            if ok and password:
                self.encryption_key = self.derive_key_from_password(password)
                encrypted_token = self.encrypt_token(token)
                self.encrypted_token = encrypted_token.decode('utf-8')
                self.settings.setValue("encrypted_token", self.encrypted_token)
                self.token_input.clear()
                self.token_input.setPlaceholderText("Token is set")
                QMessageBox.information(self, "Success", "GitHub token saved and encrypted successfully.")
            else:
                QMessageBox.warning(self, "Password Required", "You must enter a password to encrypt the token.")
        else:
            QMessageBox.warning(self, "No Token", "Please enter a GitHub Personal Access Token.")

    def get_github_token(self):
        if self.encrypted_token:
            if self.encryption_key is None:
                # Prompt for password
                password, ok = QInputDialog.getText(self, 'Enter Encryption Password', 'Enter the password to decrypt the token:', QLineEdit.Password)
                if ok and password:
                    self.encryption_key = self.derive_key_from_password(password)
                else:
                    QMessageBox.warning(self, "Password Required", "Cannot decrypt token without password.")
                    return None
            try:
                token = self.decrypt_token(self.encrypted_token)
                return token
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to decrypt token: {e}")
                return None
        return None

    def derive_key_from_password(self, password):
        # Derive a key from the password
        password_bytes = password.encode('utf-8')
        salt = b'spicetify_salt'  # You can make this more secure by generating and storing a random salt
        kdf = hashlib.pbkdf2_hmac('sha256', password_bytes, salt, 100000)
        key = base64.urlsafe_b64encode(kdf)
        return key

    def encrypt_token(self, token):
        fernet = Fernet(self.encryption_key)
        encrypted_token = fernet.encrypt(token.encode('utf-8'))
        return encrypted_token

    def decrypt_token(self, encrypted_token):
        fernet = Fernet(self.encryption_key)
        decrypted_token = fernet.decrypt(encrypted_token.encode('utf-8'))
        return decrypted_token.decode('utf-8')

    def load_custom_repos(self):
        self.repos_list_widget.clear()
        for repo in self.custom_repos:
            self.repos_list_widget.addItem(repo)

    def add_custom_repo(self):
        url, ok = QInputDialog.getText(self, 'Add Custom Repository', 'Enter repository API URL:')
        if ok and url:
            self.custom_repos.append(url)
            self.settings.setValue("custom_repos", self.custom_repos)
            self.load_custom_repos()

    def remove_custom_repo(self):
        selected_items = self.repos_list_widget.selectedItems()
        if selected_items:
            for item in selected_items:
                self.custom_repos.remove(item.text())
            self.settings.setValue("custom_repos", self.custom_repos)
            self.load_custom_repos()

    def change_theme(self, theme):
        self.theme = theme
        self.settings.setValue("theme", self.theme)
        self.apply_theme(self.theme)

    # Backup and Restore
    def backup_configuration(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Backup", "", "Backup Files (*.zip);;All Files (*)", options=options)
        if file_path:
            try:
                backup_zip = zipfile.ZipFile(file_path, 'w', zipfile.ZIP_DEFLATED)
                backup_zip.write(self.config_path, arcname='config.ini')
                # Add Extensions
                extensions_dir = os.path.join(os.path.dirname(self.config_path), 'Extensions')
                for root, dirs, files in os.walk(extensions_dir):
                    for file in files:
                        backup_zip.write(os.path.join(root, file), arcname=os.path.join('Extensions', file))
                # Add Themes
                themes_dir = os.path.join(os.path.dirname(self.config_path), 'Themes')
                for root, dirs, files in os.walk(themes_dir):
                    for file in files:
                        backup_zip.write(os.path.join(root, file), arcname=os.path.join('Themes', os.path.relpath(os.path.join(root, file), themes_dir)))
                # Add Custom Apps
                apps_dir = os.path.join(os.path.dirname(self.config_path), 'CustomApps')
                for root, dirs, files in os.walk(apps_dir):
                    for file in files:
                        backup_zip.write(os.path.join(root, file), arcname=os.path.join('CustomApps', os.path.relpath(os.path.join(root, file), apps_dir)))
                backup_zip.close()
                QMessageBox.information(self, "Success", "Backup created successfully.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to create backup: {e}")

    def restore_configuration(self):
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        file_path, _ = QFileDialog.getOpenFileName(self, "Open Backup", "", "Backup Files (*.zip);;All Files (*)", options=options)
        if file_path:
            try:
                with zipfile.ZipFile(file_path, 'r') as backup_zip:
                    backup_zip.extractall(os.path.dirname(self.config_path))
                QMessageBox.information(self, "Success", "Configuration restored successfully.")
                self.load_config()
                self.populate_extensions()
                self.load_themes()
                self.load_custom_apps()
                self.load_config_into_editor()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to restore configuration: {e}")

    # Update Notifications
    def check_for_updates(self, startup=False):
        try:
            # Rate limiting
            self.rate_limiter.wait()

            headers = {}
            token = self.get_github_token()
            if token:
                headers['Authorization'] = f'token {token}'

            # Check for Spicetify updates
            response = requests.get('https://api.github.com/repos/spicetify/spicetify-cli/releases/latest', headers=headers)
            latest_release = response.json()
            latest_version = latest_release.get('tag_name', '').strip('v')

            # Get current Spicetify version
            result = subprocess.run(['spicetify', '-v'], capture_output=True, text=True, check=True)
            current_version_output = result.stdout.strip()
            # Parse the version number from the output
            current_version = self.parse_spicetify_version(current_version_output)

            if current_version != latest_version:
                message = f"A new version of Spicetify is available ({latest_version})."
                self.update_notification(message, latest_release['html_url'])
            else:
                if not startup:
                    QMessageBox.information(self, 'No Updates', 'You have the latest version of Spicetify.')

            # Check for application updates (assuming hosted on GitHub)
            app_version = '1.0.0'  # Current version of the application
            response = requests.get('https://api.github.com/repos/YourUsername/SpicetifyManager/releases/latest', headers=headers)
            latest_release = response.json()
            latest_app_version = latest_release.get('tag_name', '1.0.0').strip('v')
            if app_version != latest_app_version:
                message = f"A new version of SpicetifyManager is available ({latest_app_version})."
                self.update_notification(message, latest_release['html_url'])
            else:
                if not startup:
                    QMessageBox.information(self, 'No Updates', 'You have the latest version of SpicetifyManager.')
        except Exception as e:
            if not startup:
                QMessageBox.warning(self, 'Update Check Failed', f"Could not check for updates: {e}")
            self.log(f"Update check failed: {e}")

    def update_notification(self, message, url):
        notification_widget = QWidget()
        layout = QHBoxLayout()
        label = QLabel(message)
        layout.addWidget(label)
        button = QPushButton("Download")
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        layout.addWidget(button)
        close_button = QPushButton("Dismiss")
        close_button.clicked.connect(notification_widget.close)
        layout.addWidget(close_button)
        notification_widget.setLayout(layout)
        self.layout().insertWidget(0, notification_widget)

    def parse_spicetify_version(self, output):
        """
        Parses the version number from the output of 'spicetify -v'
        """
        try:
            # Try to match the version using regular expressions
            match = re.search(r'v?(\d+\.\d+\.\d+)', output)
            if match:
                return match.group(1)
            else:
                # If no match, return the entire output stripped of 'v' and spaces
                return output.strip().strip('v')
        except Exception as e:
            self.log(f"Failed to parse Spicetify version: {e}")
            return '0.0.0'  # Default to a very old version if parsing fails

    def closeEvent(self, event):
        # Save settings on close
        self.settings.setValue("theme", self.theme)
        self.settings.setValue("visible_tabs", self.visible_tabs)
        self.settings.setValue("custom_repos", self.custom_repos)
        super().closeEvent(event)

class DraggableTabWidget(QTabWidget):
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.tabBar().setMovable(True)
        self.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabBar().customContextMenuRequested.connect(self.show_tab_context_menu)

    def show_tab_context_menu(self, position):
        index = self.tabBar().tabAt(position)
        if index != -1:
            menu = QMenu()
            close_action = QAction("Close Tab", self)
            close_action.triggered.connect(lambda: self.close_tab(index))
            menu.addAction(close_action)
            menu.exec_(self.tabBar().mapToGlobal(position))

    def close_tab(self, index):
        tab_name = self.tabText(index)
        self.removeTab(index)
        # Uncheck the corresponding action
        if tab_name in self.parent().tab_actions:
            self.parent().tab_actions[tab_name].setChecked(False)
            self.parent().visible_tabs[tab_name] = False
            self.parent().settings.setValue("visible_tabs", self.parent().visible_tabs)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = SpicetifyManager()
    window.show()
    sys.exit(app.exec_())
