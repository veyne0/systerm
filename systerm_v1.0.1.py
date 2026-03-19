#!/usr/bin/env python3
import sys
import os
import pty
import fcntl
import select
import signal
import termios
import struct
import json
import paramiko
import threading
import time
import subprocess
import psutil
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWebEngineWidgets import *
from PyQt6.QtWebEngineCore import *
from PyQt6.QtWebChannel import *

# 屏蔽 Wayland 调试信息
os.environ["QT_LOGGING_RULES"] = "qt.qpa.wayland.textinput=false"
os.environ["QT_QPA_PLATFORM"] = "xcb"

# ==============================================
# SSH 连接管理
# ==============================================
class SSHManager(QObject):
    """SSH 连接管理器"""
    
    output_received = pyqtSignal(str)
    connection_status = pyqtSignal(bool, str)
    
    def __init__(self):
        super().__init__()
        self.client = None
        self.channel = None
        self.connected = False
        self.shell_thread = None
        self.running = False
        self.sftp = None
    
    def connect_password(self, host, port, username, password):
        """密码方式连接 SSH 服务器"""
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(host, port=port, username=username, password=password, timeout=10)
            
            # 初始化 SFTP
            self.sftp = self.client.open_sftp()
            
            # 获取交互式 shell
            self.channel = self.client.invoke_shell(term='xterm', width=80, height=24)
            self.channel.setblocking(0)
            self.connected = True
            self.running = True
            
            # 启动接收线程
            self.shell_thread = threading.Thread(target=self._receive_output)
            self.shell_thread.daemon = True
            self.shell_thread.start()
            
            self.connection_status.emit(True, f"密码连接成功: {username}@{host}")
            return True, "连接成功"
        except Exception as e:
            self.connection_status.emit(False, str(e))
            return False, str(e)
    
    def connect_key(self, host, port, username, key_path, password=None):
        """密钥方式连接 SSH 服务器"""
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # 加载私钥
            key = paramiko.RSAKey.from_private_key_file(key_path, password=password)
            
            self.client.connect(host, port=port, username=username, pkey=key, timeout=10)
            
            # 初始化 SFTP
            self.sftp = self.client.open_sftp()
            
            # 获取交互式 shell
            self.channel = self.client.invoke_shell(term='xterm', width=80, height=24)
            self.channel.setblocking(0)
            self.connected = True
            self.running = True
            
            # 启动接收线程
            self.shell_thread = threading.Thread(target=self._receive_output)
            self.shell_thread.daemon = True
            self.shell_thread.start()
            
            self.connection_status.emit(True, f"密钥连接成功: {username}@{host}")
            return True, "连接成功"
        except Exception as e:
            self.connection_status.emit(False, str(e))
            return False, str(e)
    
    def _receive_output(self):
        """接收 SSH 输出"""
        while self.running and self.channel:
            try:
                if self.channel.recv_ready():
                    data = self.channel.recv(4096)
                    if data:
                        self.output_received.emit(data.decode('utf-8', errors='ignore'))
                time.sleep(0.01)
            except Exception as e:
                print(f"SSH接收错误: {e}")
                break
    
    def send(self, data):
        """发送数据到 SSH"""
        if self.channel and self.connected:
            try:
                self.channel.send(data)
                return True
            except Exception as e:
                print(f"SSH发送错误: {e}")
                return False
        return False
    
    def resize(self, cols, rows):
        """调整终端大小"""
        if self.channel and self.connected:
            try:
                self.channel.resize_pty(width=cols, height=rows)
            except:
                pass
    
    def upload_file(self, local_path, remote_path, callback=None):
        """使用 SFTP 上传文件到远程服务器"""
        if not self.sftp or not self.connected:
            return False, "未连接到服务器"
        
        try:
            # 上传文件
            self.sftp.put(local_path, remote_path, callback=callback)
            return True, "上传成功"
        except Exception as e:
            return False, str(e)
    
    def download_file(self, remote_path, local_path, callback=None):
        """使用 SFTP 从远程服务器下载文件"""
        if not self.sftp or not self.connected:
            return False, "未连接到服务器"
        
        try:
            # 下载文件
            self.sftp.get(remote_path, local_path, callback=callback)
            return True, "下载成功"
        except Exception as e:
            return False, str(e)
    
    def list_dir(self, remote_path):
        """使用 SFTP 列出远程目录内容"""
        if not self.sftp or not self.connected:
            return None
        
        try:
            files = self.sftp.listdir_attr(remote_path)
            return files
        except:
            return None
    
    def disconnect(self):
        """断开 SSH 连接"""
        self.running = False
        self.connected = False
        if self.sftp:
            try:
                self.sftp.close()
            except:
                pass
        if self.channel:
            try:
                self.channel.close()
            except:
                pass
        if self.client:
            try:
                self.client.close()
            except:
                pass


# ==============================================
# 终端处理器
# ==============================================
class TerminalHandler(QObject):
    """终端处理器"""
    
    output = pyqtSignal(str)
    dataReceived = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.master = None
        self.ssh_manager = None
        self.local_mode = True
    
    @pyqtSlot(str)
    def write(self, data):
        """接收来自 JavaScript 的数据"""
        self.dataReceived.emit(data)
    
    @pyqtSlot(int, int)
    def resize(self, cols, rows):
        """调整终端大小"""
        try:
            if self.local_mode and self.master:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.master, termios.TIOCSWINSZ, winsize)
            elif self.ssh_manager and self.ssh_manager.connected:
                self.ssh_manager.resize(cols, rows)
        except:
            pass


# ==============================================
# 文件搜索对话框
# ==============================================
class FileSearchDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.setWindowTitle("🔍 文件搜索")
        self.setModal(True)
        self.resize(600, 500)
        
        layout = QVBoxLayout(self)
        
        # 标签页：按类型/按名称
        self.tab_widget = QTabWidget()
        
        # 按类型搜索标签页
        self.type_search_widget = self.create_type_search_widget()
        self.tab_widget.addTab(self.type_search_widget, "按类型搜索")
        
        # 按名称搜索标签页
        self.name_search_widget = self.create_name_search_widget()
        self.tab_widget.addTab(self.name_search_widget, "按名称搜索")
        
        layout.addWidget(self.tab_widget)
        
        # 结果显示区域
        result_group = QGroupBox("搜索结果")
        result_layout = QVBoxLayout(result_group)
        
        self.result_list = QListWidget()
        self.result_list.setAlternatingRowColors(True)
        self.result_list.itemDoubleClicked.connect(self.open_file_location)
        result_layout.addWidget(self.result_list)
        
        # 状态标签
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        result_layout.addWidget(self.status_label)
        
        layout.addWidget(result_group)
        
        # 底部按钮
        btn_layout = QHBoxLayout()
        
        self.search_btn = QPushButton("🔍 搜索")
        self.search_btn.clicked.connect(self.start_search)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a90e2;
                color: white;
                padding: 8px 20px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #5a9ef2;
            }
        """)
        btn_layout.addWidget(self.search_btn)
        
        self.clear_btn = QPushButton("🗑️ 清空")
        self.clear_btn.clicked.connect(self.clear_results)
        btn_layout.addWidget(self.clear_btn)
        
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)
        
        layout.addLayout(btn_layout)
    
    def create_type_search_widget(self):
        """创建按类型搜索界面"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 搜索路径
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("搜索路径:"))
        
        self.type_path_edit = QLineEdit()
        self.type_path_edit.setPlaceholderText("例如: /home/user 或留空使用 ~/")
        self.type_path_edit.setText("~/")
        path_layout.addWidget(self.type_path_edit)
        
        self.browse_type_btn = QPushButton("浏览...")
        self.browse_type_btn.clicked.connect(lambda: self.browse_folder(self.type_path_edit))
        path_layout.addWidget(self.browse_type_btn)
        
        layout.addLayout(path_layout)
        
        # 文件类型选择
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("文件类型:"))
        
        self.type_combo = QComboBox()
        self.type_combo.addItems([
            "py - Python文件",
            "sh - Shell脚本",
            "txt - 文本文件",
            "md - Markdown文件",
            "json - JSON文件",
            "xml - XML文件",
            "yaml - YAML文件",
            "conf - 配置文件",
            "log - 日志文件",
            "c - C源文件",
            "cpp - C++源文件",
            "h - 头文件",
            "java - Java源文件",
            "js - JavaScript文件",
            "html - HTML文件",
            "css - CSS文件",
            "php - PHP文件",
            "rb - Ruby文件",
            "go - Go文件",
            "rs - Rust文件",
            "swift - Swift文件",
            "kt - Kotlin文件",
            "all - 所有文件"
        ])
        type_layout.addWidget(self.type_combo)
        
        # 递归搜索选项
        self.type_recursive = QCheckBox("递归搜索子目录")
        self.type_recursive.setChecked(True)
        type_layout.addWidget(self.type_recursive)
        
        type_layout.addStretch()
        layout.addLayout(type_layout)
        
        layout.addStretch()
        return widget
    
    def create_name_search_widget(self):
        """创建按名称搜索界面"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 搜索路径
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("搜索路径:"))
        
        self.name_path_edit = QLineEdit()
        self.name_path_edit.setPlaceholderText("例如: /home/user 或留空使用当前目录")
        self.name_path_edit.setText(".")
        path_layout.addWidget(self.name_path_edit)
        
        self.browse_name_btn = QPushButton("浏览...")
        self.browse_name_btn.clicked.connect(lambda: self.browse_folder(self.name_path_edit))
        path_layout.addWidget(self.browse_name_btn)
        
        layout.addLayout(path_layout)
        
        # 文件名
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("文件名:"))
        
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("输入文件名（支持通配符 * 和 ?）")
        name_layout.addWidget(self.name_edit)
        
        layout.addLayout(name_layout)
        
        # 选项
        options_layout = QHBoxLayout()
        
        self.name_recursive = QCheckBox("递归搜索子目录")
        self.name_recursive.setChecked(True)
        options_layout.addWidget(self.name_recursive)
        
        self.case_sensitive = QCheckBox("区分大小写")
        self.case_sensitive.setChecked(False)
        options_layout.addWidget(self.case_sensitive)
        
        options_layout.addStretch()
        layout.addLayout(options_layout)
        
        layout.addStretch()
        return widget
    
    def browse_folder(self, line_edit):
        """浏览文件夹"""
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if folder:
            line_edit.setText(folder)
    
    def start_search(self):
        """开始搜索"""
        current_tab = self.tab_widget.currentIndex()
        
        if current_tab == 0:
            self.search_by_type()
        else:
            self.search_by_name()
    
    def search_by_type(self):
        """按类型搜索"""
        # 获取搜索路径
        path = self.type_path_edit.text().strip()
        if not path:
            path = "~/"
        
        # 展开用户主目录
        path = os.path.expanduser(path)
        
        # 获取选择的文件类型
        type_text = self.type_combo.currentText()
        file_ext = type_text.split(' - ')[0]
        
        # 构建 find 命令
        if file_ext == "all":
            cmd = f"find {path} -type f"
        else:
            cmd = f"find {path} -name '*.{file_ext}' -type f"
        
        if not self.type_recursive.isChecked():
            cmd += " -maxdepth 1"
        
        self.status_label.setText("⏳ 正在搜索...")
        self.search_btn.setEnabled(False)
        self.result_list.clear()
        
        def search_thread():
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                
                if result.returncode == 0 and result.stdout.strip():
                    files = result.stdout.strip().split('\n')
                    QMetaObject.invokeMethod(self, "update_results",
                                            Qt.ConnectionType.QueuedConnection,
                                            Q_ARG(list, files))
                else:
                    QMetaObject.invokeMethod(self, "update_results",
                                            Qt.ConnectionType.QueuedConnection,
                                            Q_ARG(list, []))
            except Exception as e:
                print(f"搜索错误: {e}")
                QMetaObject.invokeMethod(self, "update_results",
                                        Qt.ConnectionType.QueuedConnection,
                                        Q_ARG(list, []))
        
        thread = threading.Thread(target=search_thread)
        thread.daemon = True
        thread.start()
    
    def search_by_name(self):
        """按名称搜索"""
        # 获取搜索路径
        path = self.name_path_edit.text().strip()
        if not path:
            path = "."
        
        # 获取文件名
        filename = self.name_edit.text().strip()
        if not filename:
            QMessageBox.warning(self, "警告", "请输入文件名")
            return
        
        # 构建 find 命令
        cmd = f"find {path} -name '{filename}' -type f"
        
        if not self.name_recursive.isChecked():
            cmd += " -maxdepth 1"
        
        if not self.case_sensitive.isChecked():
            cmd = cmd.replace("-name", "-iname")
        
        self.status_label.setText("⏳ 正在搜索...")
        self.search_btn.setEnabled(False)
        self.result_list.clear()
        
        def search_thread():
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                
                if result.returncode == 0 and result.stdout.strip():
                    files = result.stdout.strip().split('\n')
                    QMetaObject.invokeMethod(self, "update_results",
                                            Qt.ConnectionType.QueuedConnection,
                                            Q_ARG(list, files))
                else:
                    QMetaObject.invokeMethod(self, "update_results",
                                            Qt.ConnectionType.QueuedConnection,
                                            Q_ARG(list, []))
            except Exception as e:
                print(f"搜索错误: {e}")
                QMetaObject.invokeMethod(self, "update_results",
                                        Qt.ConnectionType.QueuedConnection,
                                        Q_ARG(list, []))
        
        thread = threading.Thread(target=search_thread)
        thread.daemon = True
        thread.start()
    
    @pyqtSlot(list)
    def update_results(self, files):
        """更新搜索结果"""
        self.search_btn.setEnabled(True)
        self.result_list.clear()
        
        if files:
            for file_path in files:
                if file_path.strip():
                    # 获取文件信息
                    try:
                        file_size = os.path.getsize(file_path)
                        size_str = self.format_size(file_size)
                        modified = time.ctime(os.path.getmtime(file_path))
                        
                        item_text = f"{file_path}  [{size_str}]  {modified}"
                        self.result_list.addItem(item_text)
                    except:
                        self.result_list.addItem(file_path)
            
            self.status_label.setText(f"✅ 找到 {len(files)} 个文件")
        else:
            self.status_label.setText("❌ 未找到匹配的文件")
    
    def format_size(self, size):
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f}{unit}"
            size /= 1024.0
        return f"{size:.1f}TB"
    
    def clear_results(self):
        """清空结果"""
        self.result_list.clear()
        self.status_label.setText("")
    
    def open_file_location(self, item):
        """打开文件所在位置"""
        file_path = item.text().split('  [')[0]
        folder = os.path.dirname(file_path)
        
        if os.path.exists(folder):
            # 在文件管理器中打开
            subprocess.run(f"xdg-open '{folder}'", shell=True)


# ==============================================
# 文件传输对话框
# ==============================================
class FileTransferDialog(QDialog):
    def __init__(self, ssh_manager, parent=None):
        super().__init__(parent)
        self.ssh_manager = ssh_manager
        self.setWindowTitle("文件传输")
        self.setModal(True)
        self.resize(600, 450)
        
        layout = QVBoxLayout(self)
        
        # 标签页：上传/下载
        self.tab_widget = QTabWidget()
        
        # 上传标签页
        self.upload_widget = self.create_upload_widget()
        self.tab_widget.addTab(self.upload_widget, "📤 上传到服务器")
        
        # 下载标签页
        self.download_widget = self.create_download_widget()
        self.tab_widget.addTab(self.download_widget, "📥 从服务器下载")
        
        layout.addWidget(self.tab_widget)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # 状态标签
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)
    
    def create_upload_widget(self):
        """创建上传界面"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)
        
        # 本地文件选择
        local_group = QGroupBox("本地文件")
        local_layout = QVBoxLayout(local_group)
        
        local_file_layout = QHBoxLayout()
        self.local_file_edit = QLineEdit()
        self.local_file_edit.setPlaceholderText("选择要上传的文件...")
        self.local_file_edit.setReadOnly(True)
        local_file_layout.addWidget(self.local_file_edit)
        
        self.browse_btn = QPushButton("浏览...")
        self.browse_btn.clicked.connect(self.browse_local_file)
        local_file_layout.addWidget(self.browse_btn)
        
        local_layout.addLayout(local_file_layout)
        layout.addWidget(local_group)
        
        # 远程路径
        remote_group = QGroupBox("远程路径")
        remote_layout = QVBoxLayout(remote_group)
        
        remote_path_layout = QHBoxLayout()
        self.remote_path_edit = QLineEdit()
        self.remote_path_edit.setPlaceholderText("例如: /home/user/ 或留空使用当前目录")
        remote_path_layout.addWidget(self.remote_path_edit)
        
        self.list_remote_btn = QPushButton("📋 列出目录")
        self.list_remote_btn.clicked.connect(self.list_remote_directory)
        remote_path_layout.addWidget(self.list_remote_btn)
        
        remote_layout.addLayout(remote_path_layout)
        
        # 远程目录列表
        self.remote_list = QListWidget()
        self.remote_list.setMaximumHeight(120)
        self.remote_list.itemDoubleClicked.connect(self.remote_item_double_clicked)
        remote_layout.addWidget(self.remote_list)
        
        layout.addWidget(remote_group)
        
        # 上传按钮
        self.upload_btn = QPushButton("⬆️ 开始上传")
        self.upload_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a90e2;
                color: white;
                font-weight: bold;
                padding: 10px;
                border-radius: 5px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #5a9ef2;
            }
            QPushButton:disabled {
                background-color: #666;
            }
        """)
        self.upload_btn.clicked.connect(self.start_upload)
        layout.addWidget(self.upload_btn)
        
        layout.addStretch()
        return widget
    
    def create_download_widget(self):
        """创建下载界面"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)
        
        # 远程文件路径
        remote_group = QGroupBox("远程文件")
        remote_layout = QVBoxLayout(remote_group)
        
        remote_file_layout = QHBoxLayout()
        self.remote_file_edit = QLineEdit()
        self.remote_file_edit.setPlaceholderText("输入远程文件路径...")
        remote_file_layout.addWidget(self.remote_file_edit)
        
        self.list_remote_download_btn = QPushButton("📋 列出目录")
        self.list_remote_download_btn.clicked.connect(self.list_remote_directory_download)
        remote_file_layout.addWidget(self.list_remote_download_btn)
        
        remote_layout.addLayout(remote_file_layout)
        
        # 远程目录列表
        self.remote_download_list = QListWidget()
        self.remote_download_list.setMaximumHeight(150)
        self.remote_download_list.itemDoubleClicked.connect(self.remote_download_item_double_clicked)
        remote_layout.addWidget(self.remote_download_list)
        
        layout.addWidget(remote_group)
        
        # 本地保存路径
        local_group = QGroupBox("保存到本地")
        local_layout = QVBoxLayout(local_group)
        
        local_save_layout = QHBoxLayout()
        self.local_save_edit = QLineEdit()
        self.local_save_edit.setPlaceholderText("选择保存位置...")
        self.local_save_edit.setReadOnly(True)
        local_save_layout.addWidget(self.local_save_edit)
        
        self.save_browse_btn = QPushButton("浏览...")
        self.save_browse_btn.clicked.connect(self.browse_save_location)
        local_save_layout.addWidget(self.save_browse_btn)
        
        local_layout.addLayout(local_save_layout)
        layout.addWidget(local_group)
        
        # 下载按钮
        self.download_btn = QPushButton("⬇️ 开始下载")
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a90e2;
                color: white;
                font-weight: bold;
                padding: 10px;
                border-radius: 5px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #5a9ef2;
            }
            QPushButton:disabled {
                background-color: #666;
            }
        """)
        self.download_btn.clicked.connect(self.start_download)
        layout.addWidget(self.download_btn)
        
        layout.addStretch()
        return widget
    
    def browse_local_file(self):
        """浏览本地文件"""
        file_path, _ = QFileDialog.getOpenFileName(self, "选择要上传的文件")
        if file_path:
            self.local_file_edit.setText(file_path)
    
    def browse_save_location(self):
        """浏览保存位置"""
        file_path, _ = QFileDialog.getSaveFileName(self, "保存文件")
        if file_path:
            self.local_save_edit.setText(file_path)
    
    def list_remote_directory(self):
        """列出远程目录"""
        path = self.remote_path_edit.text().strip() or "."
        self.remote_list.clear()
        self.remote_list.addItem("⏳ 加载中...")
        
        def list_thread():
            files = self.ssh_manager.list_dir(path)
            QMetaObject.invokeMethod(self, "update_remote_list",
                                    Qt.ConnectionType.QueuedConnection,
                                    Q_ARG(list, files if files else []),
                                    Q_ARG(str, path))
        
        thread = threading.Thread(target=list_thread)
        thread.daemon = True
        thread.start()
    
    def list_remote_directory_download(self):
        """列出远程目录（下载模式）"""
        path = self.remote_file_edit.text().strip() or "."
        self.remote_download_list.clear()
        self.remote_download_list.addItem("⏳ 加载中...")
        
        def list_thread():
            files = self.ssh_manager.list_dir(path)
            QMetaObject.invokeMethod(self, "update_remote_download_list",
                                    Qt.ConnectionType.QueuedConnection,
                                    Q_ARG(list, files if files else []),
                                    Q_ARG(str, path))
        
        thread = threading.Thread(target=list_thread)
        thread.daemon = True
        thread.start()
    
    @pyqtSlot(list, str)
    def update_remote_list(self, files, current_path):
        """更新远程列表（上传模式）"""
        self.remote_list.clear()
        
        if not files:
            self.remote_list.addItem("❌ 无法列出目录或目录为空")
            return
        
        # 添加上级目录选项
        if current_path != "/" and current_path != ".":
            self.remote_list.addItem("📁 .. (上级目录)")
        
        # 按类型排序：目录在前，文件在后
        dirs = [f for f in files if f.longname.startswith('d')]
        files_list = [f for f in files if not f.longname.startswith('d')]
        
        for f in dirs:
            if not f.filename.startswith('.'):
                self.remote_list.addItem(f"📁 {f.filename}/")
        
        for f in files_list:
            if not f.filename.startswith('.'):
                size = self.format_size(f.st_size)
                self.remote_list.addItem(f"📄 {f.filename} ({size})")
    
    @pyqtSlot(list, str)
    def update_remote_download_list(self, files, current_path):
        """更新远程列表（下载模式）"""
        self.remote_download_list.clear()
        
        if not files:
            self.remote_download_list.addItem("❌ 无法列出目录或目录为空")
            return
        
        # 添加上级目录选项
        if current_path != "/" and current_path != ".":
            self.remote_download_list.addItem("📁 .. (上级目录)")
        
        # 按类型排序：目录在前，文件在后
        dirs = [f for f in files if f.longname.startswith('d')]
        files_list = [f for f in files if not f.longname.startswith('d')]
        
        for f in dirs:
            if not f.filename.startswith('.'):
                self.remote_download_list.addItem(f"📁 {f.filename}/")
        
        for f in files_list:
            if not f.filename.startswith('.'):
                size = self.format_size(f.st_size)
                self.remote_download_list.addItem(f"📄 {f.filename} ({size})")
    
    def remote_item_double_clicked(self, item):
        """远程列表双击事件（上传模式）"""
        text = item.text()
        if text.startswith("📁 "):
            dir_name = text[2:].rstrip('/').split(' ')[0]
            current = self.remote_path_edit.text().strip() or "."
            
            if dir_name == ".. (上级目录)":
                # 返回上级目录
                if current == "/":
                    new_path = "/"
                else:
                    new_path = os.path.dirname(current.rstrip('/'))
                    if new_path == "":
                        new_path = "/"
            else:
                # 进入子目录
                if current.endswith('/'):
                    new_path = current + dir_name
                else:
                    new_path = current + "/" + dir_name
            
            self.remote_path_edit.setText(new_path)
            self.list_remote_directory()
    
    def remote_download_item_double_clicked(self, item):
        """远程列表双击事件（下载模式）"""
        text = item.text()
        if text.startswith("📁 "):
            dir_name = text[2:].rstrip('/').split(' ')[0]
            current = self.remote_file_edit.text().strip() or "."
            
            if dir_name == ".. (上级目录)":
                # 返回上级目录
                if current == "/":
                    new_path = "/"
                else:
                    new_path = os.path.dirname(current.rstrip('/'))
                    if new_path == "":
                        new_path = "/"
            else:
                # 进入子目录
                if current.endswith('/'):
                    new_path = current + dir_name
                else:
                    new_path = current + "/" + dir_name
            
            self.remote_file_edit.setText(new_path)
            self.list_remote_directory_download()
        elif text.startswith("📄 "):
            # 点击文件，自动填充路径
            filename = text[2:].split(' (')[0]
            current = self.remote_file_edit.text().strip() or "."
            
            if current.endswith('/'):
                full_path = current + filename
            else:
                full_path = current + "/" + filename
            
            self.remote_file_edit.setText(full_path)
    
    def format_size(self, size):
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"
    
    def start_upload(self):
        """开始上传"""
        local_path = self.local_file_edit.text()
        if not local_path:
            QMessageBox.warning(self, "警告", "请选择要上传的文件")
            return
        
        remote_path = self.remote_path_edit.text().strip() or "."
        filename = os.path.basename(local_path)
        
        # 构建完整的远程路径
        if remote_path.endswith('/'):
            full_remote = remote_path + filename
        else:
            full_remote = remote_path + "/" + filename
        
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.upload_btn.setEnabled(False)
        self.status_label.setText("⏳ 正在上传...")
        
        def upload_thread():
            success, msg = self.ssh_manager.upload_file(
                local_path, full_remote,
                callback=self.upload_progress
            )
            
            QMetaObject.invokeMethod(self, "upload_finished",
                                    Qt.ConnectionType.QueuedConnection,
                                    Q_ARG(bool, success),
                                    Q_ARG(str, msg))
        
        thread = threading.Thread(target=upload_thread)
        thread.daemon = True
        thread.start()
    
    def upload_progress(self, filename, size, transferred):
        """上传进度回调"""
        if size > 0:
            percent = int((transferred / size) * 100)
            QMetaObject.invokeMethod(self.progress_bar, "setValue",
                                    Qt.ConnectionType.QueuedConnection,
                                    Q_ARG(int, percent))
    
    @pyqtSlot(bool, str)
    def upload_finished(self, success, msg):
        """上传完成"""
        self.progress_bar.setVisible(False)
        self.upload_btn.setEnabled(True)
        self.status_label.setText("")
        
        if success:
            QMessageBox.information(self, "成功", f"✅ {msg}")
            # 刷新当前目录
            self.list_remote_directory()
        else:
            QMessageBox.critical(self, "失败", f"❌ {msg}")
    
    def start_download(self):
        """开始下载"""
        remote_path = self.remote_file_edit.text().strip()
        if not remote_path:
            QMessageBox.warning(self, "警告", "请输入远程文件路径")
            return
        
        local_path = self.local_save_edit.text()
        if not local_path:
            QMessageBox.warning(self, "警告", "请选择保存位置")
            return
        
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.download_btn.setEnabled(False)
        self.status_label.setText("⏳ 正在下载...")
        
        def download_thread():
            success, msg = self.ssh_manager.download_file(
                remote_path, local_path,
                callback=self.download_progress
            )
            
            QMetaObject.invokeMethod(self, "download_finished",
                                    Qt.ConnectionType.QueuedConnection,
                                    Q_ARG(bool, success),
                                    Q_ARG(str, msg))
        
        thread = threading.Thread(target=download_thread)
        thread.daemon = True
        thread.start()
    
    def download_progress(self, filename, size, transferred):
        """下载进度回调"""
        if size > 0:
            percent = int((transferred / size) * 100)
            QMetaObject.invokeMethod(self.progress_bar, "setValue",
                                    Qt.ConnectionType.QueuedConnection,
                                    Q_ARG(int, percent))
    
    @pyqtSlot(bool, str)
    def download_finished(self, success, msg):
        """下载完成"""
        self.progress_bar.setVisible(False)
        self.download_btn.setEnabled(True)
        self.status_label.setText("")
        
        if success:
            QMessageBox.information(self, "成功", f"✅ {msg}")
        else:
            QMessageBox.critical(self, "失败", f"❌ {msg}")


# ==============================================
# SSH 连接界面
# ==============================================
class SSHConnectWidget(QWidget):
    """SSH 连接配置界面"""
    
    connect_password = pyqtSignal(str, int, str, str)
    connect_key = pyqtSignal(str, int, str, str, str)
    
    def __init__(self):
        super().__init__()
        self.config_file = os.path.expanduser("~/systerm_config/systerm_config.json")
        self.key_file = None
        self.password_visible = False
        self.init_ui()
        self.load_config()
    
    def init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 标题
        title = QLabel("SSH 远程连接")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #4a90e2;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        # 连接方式选择
        method_layout = QHBoxLayout()
        self.password_radio = QRadioButton("密码连接")
        self.password_radio.setChecked(True)
        self.password_radio.toggled.connect(self.toggle_auth_method)
        method_layout.addWidget(self.password_radio)
        
        self.key_radio = QRadioButton("密钥连接")
        self.key_radio.toggled.connect(self.toggle_auth_method)
        method_layout.addWidget(self.key_radio)
        
        layout.addLayout(method_layout)
        
        # 基本信息表单
        base_group = QGroupBox("服务器信息")
        base_layout = QFormLayout(base_group)
        
        # IP 地址
        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("例如: 192.168.1.100")
        base_layout.addRow("IP 地址:", self.ip_edit)
        
        # 端口
        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("默认: 22")
        self.port_edit.setText("22")
        base_layout.addRow("端口:", self.port_edit)
        
        # 用户名
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("请输入用户名")
        base_layout.addRow("用户名:", self.username_edit)
        
        layout.addWidget(base_group)
        
        # 密码认证面板
        self.password_group = QGroupBox("密码认证")
        password_layout = QFormLayout(self.password_group)
        
        # 密码输入行
        password_widget = QWidget()
        password_widget_layout = QHBoxLayout(password_widget)
        password_widget_layout.setContentsMargins(0, 0, 0, 0)
        
        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("请输入密码")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        password_widget_layout.addWidget(self.password_edit)
        
        self.toggle_password_btn = QPushButton("👁️")
        self.toggle_password_btn.setToolTip("显示/隐藏密码")
        self.toggle_password_btn.setFixedWidth(30)
        self.toggle_password_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_password_btn.clicked.connect(self.toggle_password_visibility)
        password_widget_layout.addWidget(self.toggle_password_btn)
        
        password_layout.addRow("密码:", password_widget)
        
        layout.addWidget(self.password_group)
        
        # 密钥认证面板
        self.key_group = QGroupBox("密钥认证")
        self.key_group.setVisible(False)
        key_layout = QVBoxLayout(self.key_group)
        
        # 密钥文件选择
        key_file_layout = QHBoxLayout()
        self.key_file_edit = QLineEdit()
        self.key_file_edit.setPlaceholderText("选择私钥文件...")
        key_file_layout.addWidget(self.key_file_edit)
        
        self.key_browse_btn = QPushButton("浏览...")
        self.key_browse_btn.clicked.connect(self.browse_key_file)
        key_file_layout.addWidget(self.key_browse_btn)
        key_layout.addLayout(key_file_layout)
        
        # 密钥密码（可选）
        key_pass_layout = QHBoxLayout()
        key_pass_layout.addWidget(QLabel("密钥密码:"))
        
        key_pass_widget = QWidget()
        key_pass_widget_layout = QHBoxLayout(key_pass_widget)
        key_pass_widget_layout.setContentsMargins(0, 0, 0, 0)
        
        self.key_pass_edit = QLineEdit()
        self.key_pass_edit.setPlaceholderText("如密钥有密码请填写")
        self.key_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        key_pass_widget_layout.addWidget(self.key_pass_edit)
        
        self.toggle_key_pass_btn = QPushButton("👁️")
        self.toggle_key_pass_btn.setToolTip("显示/隐藏密码")
        self.toggle_key_pass_btn.setFixedWidth(30)
        self.toggle_key_pass_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_key_pass_btn.clicked.connect(self.toggle_key_pass_visibility)
        key_pass_widget_layout.addWidget(self.toggle_key_pass_btn)
        
        key_pass_layout.addWidget(key_pass_widget)
        key_layout.addLayout(key_pass_layout)
        
        # 生成密钥提示
        key_hint = QLabel("💡 提示: 可用 ssh-keygen 生成密钥对")
        key_hint.setStyleSheet("color: #888; font-size: 11px;")
        key_layout.addWidget(key_hint)
        
        layout.addWidget(self.key_group)
        
        # 连接按钮
        self.connect_btn = QPushButton("🔌 连接")
        self.connect_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a90e2;
                color: white;
                font-size: 14px;
                font-weight: bold;
                padding: 12px;
                border-radius: 6px;
                min-width: 150px;
            }
            QPushButton:hover {
                background-color: #5a9ef2;
            }
            QPushButton:pressed {
                background-color: #3a80d2;
            }
            QPushButton:disabled {
                background-color: #666;
            }
        """)
        self.connect_btn.clicked.connect(self.on_connect)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        # 状态显示
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self.status_label)
        
        layout.addStretch()
    
    def toggle_auth_method(self):
        """切换认证方式"""
        if self.password_radio.isChecked():
            self.password_group.setVisible(True)
            self.key_group.setVisible(False)
        else:
            self.password_group.setVisible(False)
            self.key_group.setVisible(True)
    
    def toggle_password_visibility(self):
        """切换密码显示/隐藏"""
        self.password_visible = not self.password_visible
        if self.password_visible:
            self.password_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_password_btn.setText("🔒")
            self.toggle_password_btn.setToolTip("隐藏密码")
        else:
            self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_password_btn.setText("👁️")
            self.toggle_password_btn.setToolTip("显示密码")
    
    def toggle_key_pass_visibility(self):
        """切换密钥密码显示/隐藏"""
        if self.key_pass_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.key_pass_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_key_pass_btn.setText("🔒")
            self.toggle_key_pass_btn.setToolTip("隐藏密码")
        else:
            self.key_pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_key_pass_btn.setText("👁️")
            self.toggle_key_pass_btn.setToolTip("显示密码")
    
    def browse_key_file(self):
        """浏览密钥文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择私钥文件", 
            os.path.expanduser("~/.ssh"),
            "密钥文件 (*.pem *.key);;所有文件 (*)"
        )
        if file_path:
            self.key_file_edit.setText(file_path)
            self.key_file = file_path
    
    def load_config(self):
        """加载配置文件"""
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            
            if os.path.exists(self.config_file):
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                
                self.username_edit.setText(config.get("user", ""))
                self.password_edit.setText(config.get("password", ""))
                self.ip_edit.setText(config.get("IP", ""))
                self.port_edit.setText(str(config.get("port", 22)))
                self.key_file_edit.setText(config.get("key_file", ""))
            else:
                config = {
                    "user": "",
                    "password": "",
                    "IP": "",
                    "port": 22,
                    "key_file": ""
                }
                with open(self.config_file, "w", encoding="utf-8") as f:
                    json.dump(config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"加载配置错误: {e}")
    
    def save_config(self):
        """保存配置"""
        try:
            config = {
                "user": self.username_edit.text(),
                "password": self.password_edit.text() if self.password_radio.isChecked() else "",
                "IP": self.ip_edit.text(),
                "port": int(self.port_edit.text() or 22),
                "key_file": self.key_file_edit.text() if self.key_radio.isChecked() else ""
            }
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存配置错误: {e}")
    
    def on_connect(self):
        """连接按钮点击事件"""
        username = self.username_edit.text().strip()
        ip = self.ip_edit.text().strip()
        port = int(self.port_edit.text().strip() or 22)
        
        if not username or not ip:
            self.status_label.setText("❌ 请填写完整信息")
            self.status_label.setStyleSheet("color: #ff4444;")
            return
        
        self.save_config()
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("⏳ 连接中...")
        self.status_label.setText("⏳ 正在连接...")
        self.status_label.setStyleSheet("color: #4a90e2;")
        
        if self.password_radio.isChecked():
            password = self.password_edit.text()
            if not password:
                self.status_label.setText("❌ 请输入密码")
                self.status_label.setStyleSheet("color: #ff4444;")
                self.connect_btn.setEnabled(True)
                self.connect_btn.setText("🔌 连接")
                return
            
            self.connect_password.emit(ip, port, username, password)
        else:
            key_file = self.key_file_edit.text().strip()
            if not key_file or not os.path.exists(key_file):
                self.status_label.setText("❌ 请选择有效的密钥文件")
                self.status_label.setStyleSheet("color: #ff4444;")
                self.connect_btn.setEnabled(True)
                self.connect_btn.setText("🔌 连接")
                return
            
            key_pass = self.key_pass_edit.text() or None
            self.connect_key.emit(ip, port, username, key_file, key_pass)
    
    def set_status(self, success, message):
        """设置连接状态"""
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("🔌 连接")
        
        if success:
            self.status_label.setText(f"✅ {message}")
            self.status_label.setStyleSheet("color: #4caf50;")
        else:
            self.status_label.setText(f"❌ {message}")
            self.status_label.setStyleSheet("color: #ff4444;")


# ==============================================
# 命令详情对话框
# ==============================================
class CommandDetailDialog(QDialog):
    def __init__(self, cmd, desc, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.desc = desc
        self.setWindowTitle(f"📖 {cmd.split()[0]} 用法")
        self.setModal(True)
        self.resize(450, 350)
        
        layout = QVBoxLayout(self)
        
        # 命令标题
        title_layout = QHBoxLayout()
        cmd_label = QLabel(f"<b>{cmd}</b>")
        cmd_label.setStyleSheet("font-size: 16px; color: #4a90e2;")
        title_layout.addWidget(cmd_label)
        title_layout.addStretch()
        layout.addLayout(title_layout)
        
        # 简要说明
        brief_label = QLabel(desc)
        brief_label.setWordWrap(True)
        brief_label.setStyleSheet("font-size: 13px; color: #888; padding: 5px;")
        layout.addWidget(brief_label)
        
        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)
        
        # 详细用法
        details = self.get_command_usage(cmd)
        details_text = QTextEdit()
        details_text.setReadOnly(True)
        details_text.setPlainText(details)
        details_text.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #d0d0d0;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                font-family: Monospace;
                font-size: 16px;
                padding: 10px;
            }
        """)
        layout.addWidget(details_text)
        
        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a90e2;
                color: white;
                padding: 6px;
                border-radius: 4px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #5a9ef2;
            }
        """)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)
    
    def get_command_usage(self, cmd):
        """获取命令的详细用法（修复版 - 支持多词命令）"""
        cmd_clean = cmd.strip()
        
        # 为每个命令提供基本解释
        usage_db = {
            # 文件操作 - 单词命令
            "ls": "📁 ls - 列出目录内容\n\n"
                  "常用用法：\n"
                  "• ls -l     # 显示文件详细信息\n"
                  "• ls -a     # 显示所有文件（包括隐藏文件）\n"
                  "• ls -lh    # 以人类可读格式显示文件大小\n"
                  "• ls -lt    # 按时间排序显示文件详细信息",
            
            "cd": "📂 cd - 切换目录\n\n"
                  "常用用法：\n"
                  "• cd ..     # 返回上级目录\n"
                  "• cd ~      # 返回用户主目录\n"
                  "• cd /home  # 切换到指定目录\n"
                  "• cd -      # 返回上次访问的目录",
            
            "pwd": "📍 pwd - 显示当前工作目录\n\n"
                   "常用用法：\n"
                   "• pwd       # 显示当前所在的绝对路径",
            
            "mkdir": "📁 mkdir - 创建目录\n\n"
                     "常用用法：\n"
                     "• mkdir dir     # 创建名为dir的目录\n"
                     "• mkdir -p a/b/c  # 递归创建多级目录",
            
            "rm": "🗑️ rm - 删除文件或目录\n\n"
                  "常用用法：\n"
                  "• rm file        # 删除文件\n"
                  "• rm -r dir      # 删除目录及其内容\n"
                  "• rm -rf dir     # 强制删除目录（谨慎使用）",
            
            "mv": "✂️ mv - 移动或重命名文件\n\n"
                  "常用用法：\n"
                  "• mv file dir     # 将文件移动到目录\n"
                  "• mv old new      # 将文件重命名\n"
                  "• mv file1 file2 dir  # 移动多个文件",
            
            "cp": "📋 cp - 复制文件或目录\n\n"
                  "常用用法：\n"
                  "• cp file1 file2  # 复制文件\n"
                  "• cp -r dir1 dir2 # 递归复制目录\n"
                  "• cp -i file1 file2 # 复制时提示覆盖",
            
            "cat": "📄 cat - 查看文件内容\n\n"
                   "常用用法：\n"
                   "• cat file        # 显示文件内容\n"
                   "• cat file1 file2 # 同时显示多个文件\n"
                   "• cat > file      # 创建文件并输入内容（Ctrl+D保存）",
            
            "less": "📑 less - 分页查看文件\n\n"
                    "常用用法：\n"
                    "• less file    # 分页查看文件\n"
                    "• 空格键       # 下一页\n"
                    "• b键          # 上一页\n"
                    "• q键          # 退出",
            
            "head": "🔝 head - 查看文件开头\n\n"
                    "常用用法：\n"
                    "• head file        # 默认显示前10行\n"
                    "• head -n 20 file  # 显示前20行",
            
            "tail": "🔚 tail - 查看文件结尾\n\n"
                    "常用用法：\n"
                    "• tail file        # 默认显示最后10行\n"
                    "• tail -n 20 file  # 显示最后20行\n"
                    "• tail -f file     # 实时跟踪文件更新",
            
            "touch": "🆕 touch - 创建空文件或更新时间戳\n\n"
                     "常用用法：\n"
                     "• touch file     # 创建空文件\n"
                     "• touch file1 file2  # 创建多个文件",
            
            "find": "🔍 find - 查找文件\n\n"
                    "常用用法：\n"
                    "• find . -name '*.txt'  # 查找当前目录下所有txt文件\n"
                    "• find /home -name 'file'  # 在指定目录查找\n"
                    "• find . -type f -size +10M  # 查找大于10M的文件",
            
            "du": "📊 du - 查看目录大小\n\n"
                  "常用用法：\n"
                  "• du -sh dir    # 查看目录总大小\n"
                  "• du -sh *      # 查看当前目录下所有文件和目录的大小\n"
                  "• du -h --max-depth=1  # 查看一级目录大小",
            
            # 文件操作 - 多词命令
            "rm -rf": "⚠️ rm -rf - 强制删除目录\n\n"
                      "警告：此命令会强制递归删除目录及其所有内容，无法恢复！\n\n"
                      "用法：\n"
                      "• rm -rf 目录名  # 强制递归删除指定目录",
            
            "cp -r": "📋 cp -r - 递归复制目录\n\n"
                     "用法：\n"
                     "• cp -r 源目录 目标目录  # 复制整个目录及其内容到另一个目录",
            
            "tail -f": "🔄 tail -f - 实时查看文件\n\n"
                       "常用用法：\n"
                       "• tail -f 日志文件  # 实时监控日志更新\n"
                       "• Ctrl+C           # 退出实时查看",
            
            "find . -name": "🔍 find . -name - 查找文件\n\n"
                            "用法：\n"
                            "• find . -name '*.txt'  # 查找当前目录下所有txt文件,txt也可以换为其他的文件后缀",
            
            # 包管理 - 单词命令
            "sudo": "🔐 sudo - 以管理员权限执行命令\n\n"
                    "常用用法：\n"
                    "• sudo 命令    # 以root权限执行命令\n"
                    "• sudo -i      # 切换到root用户\n"
                    "• sudo !!      # 用sudo执行上一条命令",
            
            "apt": "📦 apt - 包管理工具\n\n"
                   "常用用法：\n"
                   "• apt update           # 更新软件源\n"
                   "• apt upgrade          # 升级软件包\n"
                   "• apt install 包名     # 安装软件\n"
                   "• apt remove 包名      # 卸载软件\n"
                   "• apt search 关键词    # 搜索软件",
            
            # 包管理 - 多词命令
            "sudo apt update": "🔄 sudo apt update - 更新软件源\n\n"
                               "作用：从软件源获取最新的软件包列表",
            
            "sudo apt upgrade -y": "⬆️ sudo apt upgrade -y - 升级所有软件包\n\n"
                                    "作用：升级系统中所有已安装的软件包到最新版本\n"
                                    "• -y 参数表示自动确认",
            
            "sudo apt install": "📦 sudo apt install - 安装软件包\n\n"
                                "用法：\n"
                                "• sudo apt install 软件包名        # 安装指定软件\n"
                                "• sudo apt install 软件包名1 软件包名2 # 安装多个软件",
            
            "sudo apt search": "🔎 sudo apt search - 搜索软件包\n\n"
                               "用法：\n"
                               "• apt search 关键词  # 搜索包含关键词的软件包",
            
            "sudo apt remove": "🗑️ sudo apt remove - 卸载软件包\n\n"
                               "用法：\n"
                               "• sudo apt remove 包名  # 卸载指定软件",
            
            "apt list --installed": "📋 apt list --installed - 列出已安装软件\n\n"
                                     "作用：显示系统中所有已安装的软件包列表\n"
                                     "• 按 q 键可以退出",
            
            "sudo apt autoremove": "🧹 sudo apt autoremove - 自动卸载无用依赖\n\n"
                                    "作用：删除系统中不再需要的依赖包",
            
            "sudo dpkg -i": "📦 sudo dpkg -i - 安装本地deb包\n\n"
                            "用法：\n"
                            "• sudo dpkg -i 软件包.deb  # 安装本地deb文件\n"
                            "• 如果出现依赖问题，可以用 sudo apt install -f 修复",
            
            # 系统监控
            "ps": "📊 ps - 查看进程\n\n"
                  "常用用法：\n"
                  "• ps aux     # 查看所有进程\n"
                  "• ps -ef     # 查看所有进程（另一种格式）\n"
                  "• ps aux | grep 进程名  # 查找特定进程",
            
            "ps aux": "📊 ps aux - 查看所有进程\n\n"
                      "作用：显示系统中所有正在运行的进程详细信息\n"
                      "• a: 显示所有终端进程\n"
                      "• u: 显示用户格式\n"
                      "• x: 显示没有终端的进程",
            
            "df": "💾 df - 查看磁盘空间\n\n"
                  "常用用法：\n"
                  "• df -h     # 以人类可读格式显示,如(G,M)\n"
                  "• df -T     # 显示文件系统类型\n"
                  "• df -i     # 显示inode使用情况",
            
            "df -h": "💾 df -h - 查看磁盘空间\n\n"
                     "作用：以人类可读格式显示磁盘分区使用情况",
            
            "free": "🧠 free - 查看内存使用\n\n"
                    "常用用法：\n"
                    "• free -h     # 以人类可读格式显示\n"
                    "• free -m     # 以MB为单位显示\n"
                    "• free -g     # 以GB为单位显示",
            
            "free -h": "🧠 free -h - 查看内存使用情况\n\n"
                       "作用：以人类可读格式显示内存使用情况",
            
            "top": "📈 top - 动态查看进程\n\n"
                   "作用：实时显示系统进程状态，按CPU使用率排序\n"
                   "• q键 退出\n"
                   "• M键 按内存排序\n"
                   "• P键 按CPU排序\n"
                   "• k键 终止进程",
            
            "htop": "📊 htop - 增强版top\n\n"
                    "作用：更友好的进程查看器，支持鼠标操作\n"
                    "• F10 退出\n"
                    "• F6 排序\n"
                    "• F9 终止进程",
            
            "uptime": "⏱️ uptime - 查看系统运行时间\n\n"
                      "作用：显示系统已运行时间、负载等信息",
            
            "who": "👤 who - 查看登录用户\n\n"
                   "作用：显示当前登录系统的用户信息",
            
            "dmesg": "📋 dmesg - 查看系统日志\n\n"
                     "常用用法：\n"
                     "• dmesg | tail    # 查看最近的系统日志\n"
                     "• dmesg -T        # 显示人类可读的时间戳\n"
                     "• dmesg -w        # 实时监控日志",
            
            "dmesg | tail": "📋 dmesg | tail - 查看最近的系统日志\n\n"
                            "作用：显示内核环形缓冲区的最新消息",
            
            # 网络工具
            "ip": "🌐 ip - 网络配置工具\n\n"
                  "常用用法：\n"
                  "• ip a          # 查看网络接口\n"
                  "• ip link set   # 设置网络接口\n"
                  "• ip route      # 查看路由表",
            
            "ip a": "🌐 ip a - 查看网络接口\n\n"
                    "作用：显示所有网络接口的配置信息",
            
            "ifconfig": "🌐 ifconfig - 查看网络配置\n\n"
                        "作用：显示或配置网络接口（传统命令）",
            
            "ping": "📶 ping - 测试网络连通\n\n"
                    "用法：\n"
                    "• ping 8.8.8.8    # 测试与Google DNS的连通性\n"
                    "• ping -c 4 地址  # 发送4个数据包\n"
                    "• Ctrl+C 退出",
            
            "netstat": "🔌 netstat - 网络统计\n\n"
                       "常用用法：\n"
                       "• netstat -tulpn  # 查看端口监听\n"
                       "• netstat -an     # 显示所有连接\n"
                       "• netstat -r      # 显示路由表",
            
            "netstat -tulpn": "🔌 netstat -tulpn - 查看端口监听\n\n"
                               "参数说明：\n"
                               "• -t: TCP端口\n"
                               "• -u: UDP端口\n"
                               "• -l: 监听状态\n"
                               "• -p: 显示进程\n"
                               "• -n: 显示数字地址",
            
            "ss": "🔌 ss - 快速查看端口\n\n"
                  "常用用法：\n"
                  "• ss -tulpn      # 查看端口监听\n"
                  "• ss -s          # 显示统计信息\n"
                  "• ss -4          # 只显示IPv4",
            
            "ss -tulpn": "🔌 ss -tulpn - 快速查看端口\n\n"
                         "作用：比netstat更快的端口查看工具\n"
                         "参数同netstat",
            
            "curl": "🌍 curl - 发送网络请求\n\n"
                    "常用用法：\n"
                    "• curl 网址        # 获取网页内容\n"
                    "• curl -O 网址     # 下载文件\n"
                    "• curl -I 网址     # 只获取响应头",
            
            "wget": "⬇️ wget - 下载文件\n\n"
                    "常用用法：\n"
                    "• wget 网址        # 下载文件\n"
                    "• wget -c 网址     # 断点续传\n"
                    "• wget -r 网址     # 递归下载",
            
            "ssh": "🔐 ssh - 远程连接\n\n"
                   "用法：\n"
                   "• ssh user@host     # 连接远程服务器\n"
                   "• ssh -p 端口 user@host  # 指定端口连接\n"
                   "• ssh -i 密钥 user@host  # 使用密钥连接",
            
            # 权限管理
            "chmod": "🔧 chmod - 修改文件权限\n\n"
                     "常用用法：\n"
                     "• chmod +x 文件    # 添加执行权限\n"
                     "• chmod 755 文件   # 设置权限 rwxr-xr-x\n"
                     "• chmod 644 文件   # 设置权限 rw-r--r--\n"
                     "• chmod -R 755 目录  # 递归修改目录权限",
            
            "chmod +x": "🔧 chmod +x - 添加执行权限\n\n"
                        "用法：\n"
                        "• chmod +x 脚本.sh  # 给脚本添加执行权限",
            
            "chmod 755": "🔢 chmod 755 - 设置权限\n\n"
                         "作用：设置文件权限为 rwxr-xr-x\n"
                         "• 所有者：读、写、执行\n"
                         "• 组用户：读、执行\n"
                         "• 其他用户：读、执行",
            
            "chmod 644": "🔢 chmod 644 - 设置权限\n\n"
                         "作用：设置文件权限为 rw-r--r--\n"
                         "• 所有者：读、写\n"
                         "• 组用户：读\n"
                         "• 其他用户：读",
            
            "chown": "👑 chown - 修改文件所有者\n\n"
                     "用法：\n"
                     "• chown user file        # 修改文件所有者\n"
                     "• chown user:group file  # 同时修改所有者和组\n"
                     "• chown -R user dir      # 递归修改目录所有者",
            
            "sudo -i": "🔐 sudo -i - 切换到root用户\n\n"
                       "作用：切换到root用户环境\n"
                       "• 输入 exit 切换回原用户",
            
            "sudo !!": "⚡ sudo !! - 用sudo执行上一条命令\n\n"
                       "作用：用root权限重新执行上一条命令",
            
            "whoami": "👤 whoami - 显示当前用户名\n\n"
                      "作用：显示当前登录的用户",
            
            "id": "🆔 id - 显示用户信息\n\n"
                  "作用：显示当前用户的UID、GID和所属组信息",
            
            # 进程管理
            "ps aux | grep": "🔍 ps aux | grep - 查找进程\n\n"
                             "用法：\n"
                             "• ps aux | grep 进程名  # 查找指定进程\n"
                             "• ps aux | grep -v grep  # 排除grep自身",
            
            "kill": "💀 kill - 终止进程\n\n"
                    "用法：\n"
                    "• kill PID        # 终止指定PID的进程\n"
                    "• kill -9 PID     # 强制终止进程\n"
                    "• kill -15 PID    # 正常终止进程",
            
            "killall": "💀 killall - 按名称终止进程\n\n"
                       "用法：\n"
                       "• killall 进程名  # 终止所有同名进程",
            
            "pkill": "💀 pkill - 按模式终止进程\n\n"
                     "用法：\n"
                     "• pkill 进程名  # 终止匹配的进程\n"
                     "• pkill -f 模式  # 按完整命令行匹配",
            
            "jobs": "📋 jobs - 查看后台任务\n\n"
                    "作用：显示当前终端中的后台任务列表\n"
                    "• 任务编号用 % 引用，如 fg %1",
            
            "fg": "🔄 fg - 将后台任务调到前台\n\n"
                  "用法：\n"
                  "• fg %1  # 将编号为1的后台任务调到前台\n"
                  "• fg     # 将最近的后台任务调到前台",
            
            "bg": "🔄 bg - 将前台任务调到后台\n\n"
                  "用法：\n"
                  "• bg %1  # 将编号为1的任务放到后台运行\n"
                  "• 先按 Ctrl+Z 暂停任务，再输入 bg",
            
            "nohup": "⚡ nohup - 后台运行（退出后继续）\n\n"
                     "用法：\n"
                     "• nohup 命令 &  # 后台运行命令，关闭终端后继续\n"
                     "• nohup 命令 > output.log 2>&1 &  # 重定向输出",
            
            # 文本处理
            "grep": "🔍 grep - 搜索文本\n\n"
                    "常用用法：\n"
                    "• grep '模式' 文件     # 在文件中搜索\n"
                    "• grep -i '模式' 文件  # 忽略大小写\n"
                    "• grep -n '模式' 文件  # 显示行号\n"
                    "• grep -r '模式' .     # 递归搜索",
            
            "grep -r": "🔍 grep -r - 递归搜索\n\n"
                       "用法：\n"
                       "• grep -r '模式' .  # 在当前目录递归搜索",
            
            "sed": "✂️ sed - 替换文本\n\n"
                   "常用用法：\n"
                   "• sed 's/旧/新/g' 文件  # 替换所有匹配\n"
                   "• sed -i 's/旧/新/g' 文件  # 直接修改文件\n"
                   "• sed -n '2,5p' 文件   # 显示第2-5行内容。2,5也可以改为别的数字",
            
            "awk": "📊 awk - 提取字段\n\n"
                   "常用用法：\n"
                   "• awk '{print $1}' 文件  # 提取每行第一个字段\n"
                   "• awk -F':' '{print $1}' 文件  # 指定分隔符\n"
                   "• awk '$3 > 10' 文件     # 条件过滤",
            
            "cut": "✂️ cut - 切分字段\n\n"
                   "常用用法：\n"
                   "• cut -d':' -f1 文件  # 以:为分隔符取第一列\n"
                   "• cut -c1-10 文件     # 取每行前10个字符",
            
            "sort": "📊 sort - 排序\n\n"
                    "常用用法：\n"
                    "• sort 文件        # 排序\n"
                    "• sort -r 文件     # 反向排序\n"
                    "• sort -n 文件     # 按数值排序\n"
                    "• sort -u 文件     # 排序并去重",
            
            "uniq": "🔍 uniq - 去重\n\n"
                    "用法：\n"
                    "• sort 文件 | uniq  # 排序后去重\n"
                    "• uniq -c 文件      # 统计重复次数\n"
                    "• uniq -d 文件      # 只显示重复行",
            
            "wc": "📊 wc - 统计\n\n"
                  "常用用法：\n"
                  "• wc -l 文件  # 统计行数\n"
                  "• wc -w 文件  # 统计单词数\n"
                  "• wc -c 文件  # 统计字节数",
            
            "wc -l": "📊 wc -l - 统计行数\n\n"
                     "用法：\n"
                     "• wc -l 文件  # 统计文件行数",
            
            # 压缩备份
            "tar": "📦 tar - 打包压缩工具\n\n"
                   "常用用法：\n"
                   "• tar -czf 包.tar.gz 目录   # 创建tar.gz\n"
                   "• tar -xzf 包.tar.gz        # 解压tar.gz\n"
                   "• tar -cjf 包.tar.bz2 目录  # 创建tar.bz2\n"
                   "• tar -xjf 包.tar.bz2       # 解压tar.bz2",
            
            "tar -czf": "📦 tar -czf - 创建tar.gz压缩包\n\n"
                        "参数说明：\n"
                        "• -c: 创建归档\n"
                        "• -z: 用gzip压缩\n"
                        "• -f: 指定文件名\n\n"
                        "用法：\n"
                        "• tar -czf 包.tar.gz 目录  # 压缩目录",
            
            "tar -xzf": "📦 tar -xzf - 解压tar.gz文件\n\n"
                        "参数说明：\n"
                        "• -x: 解压\n"
                        "• -z: 用gzip解压\n"
                        "• -f: 指定文件名\n\n"
                        "用法：\n"
                        "• tar -xzf 包.tar.gz        # 解压到当前目录\n"
                        "• tar -xzf 包.tar.gz -C 目录 # 解压到指定目录",
            
            "zip": "📦 zip - 创建zip压缩包\n\n"
                   "常用用法：\n"
                   "• zip 包.zip 文件           # 压缩单个文件\n"
                   "• zip -r 包.zip 目录        # 递归压缩目录\n"
                   "• zip -e 包.zip 文件        # 加密压缩",
            
            "zip -r": "📦 zip -r - 创建zip压缩包\n\n"
                      "用法：\n"
                      "• zip -r 包.zip 目录  # 递归压缩目录",
            
            "unzip": "📦 unzip - 解压zip文件\n\n"
                     "用法：\n"
                     "• unzip 包.zip       # 解压到当前目录\n"
                     "• unzip 包.zip -d 目录  # 解压到指定目录\n"
                     "• unzip -l 包.zip    # 列出压缩包内容",
            
            "gzip": "📦 gzip - 压缩文件为.gz\n\n"
                    "用法：\n"
                    "• gzip 文件  # 压缩文件，生成.gz文件\n"
                    "• gzip -k 文件  # 保留原文件\n"
                    "• gzip -d 文件.gz  # 解压（同gunzip）",
            
            "gunzip": "📦 gunzip - 解压.gz文件\n\n"
                      "用法：\n"
                      "• gunzip 文件.gz  # 解压gz文件",
            
            # 系统信息
            "uname": "🐧 uname - 查看系统信息\n\n"
                     "常用用法：\n"
                     "• uname -a    # 显示所有信息\n"
                     "• uname -r    # 显示内核版本\n"
                     "• uname -m    # 显示硬件架构",
            
            "uname -a": "🐧 uname -a - 查看内核信息\n\n"
                        "作用：显示系统内核版本、主机名、处理器架构等信息",
            
            "lsb_release": "📀 lsb_release - 查看发行版信息\n\n"
                           "常用用法：\n"
                           "• lsb_release -a  # 显示所有信息\n"
                           "• lsb_release -c  # 显示代号\n"
                           "• lsb_release -r  # 显示版本号",
            
            "lsb_release -a": "📀 lsb_release -a - 查看发行版的版本\n\n"
                              "作用：显示发行版的详细信息",
            
            "cat /etc/os-release": "📄 cat /etc/os-release - 发行版信息\n\n"
                                    "作用：显示系统发行版详细信息",
            
            "lscpu": "💻 lscpu - 查看CPU信息\n\n"
                     "作用：显示CPU架构、型号、核心数、频率等信息",
            
            "lsblk": "💾 lsblk - 查看磁盘分区\n\n"
                     "作用：以树形显示所有磁盘和分区信息",
            
            "lspci": "🔌 lspci - 查看PCI设备\n\n"
                     "作用：显示所有PCI设备列表",
            
            "lsusb": "🔌 lsusb - 查看USB设备\n\n"
                     "作用：显示所有USB设备列表",
            
            "date": "📅 date - 显示日期时间\n\n"
                    "作用：显示当前系统日期和时间",
            
            "cal": "📅 cal - 显示日历\n\n"
                   "作用：显示当前月份的日历,需要先安装ncal,sudo apt install ncal ",
            
            "history": "📋 history - 查看命令历史\n\n"
                       "作用：显示执行过的命令历史\n"
                       "• !数字 执行历史中对应编号的命令\n"
                       "• !! 执行上一条命令"
        }
        
        # 先尝试精确匹配整个命令
        if cmd_clean in usage_db:
            return usage_db[cmd_clean]
        
        # 再尝试匹配命令的前两个词
        parts = cmd_clean.split()
        if len(parts) >= 2:
            two_word = f"{parts[0]} {parts[1]}"
            if two_word in usage_db:
                return usage_db[two_word]
        
        # 最后用第一个词
        cmd_base = parts[0]
        if cmd_base in usage_db:
            return usage_db[cmd_base]
        else:
            return f"📌 {cmd_base} 命令\n\n{self.desc}"


# ==============================================
# 命令面板
# ==============================================
class CommandPanel(QWidget):
    def __init__(self, terminal):
        super().__init__()
        self.terminal = terminal
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)
        
        # 标题
        title = QLabel("📦 命令面板")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 5px; color: #4a90e2;")
        layout.addWidget(title)
        
        # 搜索框
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 搜索命令...")
        self.search.setStyleSheet("padding: 5px;")
        layout.addWidget(self.search)
        
        # 分类列表
        self.category_list = QListWidget()
        self.category_list.setMaximumHeight(100)
        layout.addWidget(self.category_list)
        
        # 命令区域
        self.command_stack = QStackedWidget()
        layout.addWidget(self.command_stack, stretch=1)
        
        # 命令分组
        self.groups = {
            "📁 文件操作": [
                ("ls -l", "列出所有文件详细信息", True),
                ("ls -la", "列出所有文件及隐藏文件", True),
                ("cd ..", "返回上级目录", True),
                ("cd ", "切换到某目录", False),
                ("pwd", "显示当前目录", True),
                ("mkdir ", "创建目录", False),
                ("rm ", "删除文件", False),
                ("rm -rf ", "删除目录", False),
                ("mv ", "移动/重命名", False),
                ("cp -r ", "复制目录", False),
                ("cat ", "查看文件", False),
                ("less ", "分页查看", False),
                ("head ", "查看开头", False),
                ("tail -f ", "实时查看", False),
                ("touch ", "创建文件", False),
                ("find . -name ", "查找文件", False),
                ("du -sh ", "查看大小", False)
            ],
            "📦 包管理": [
                ("sudo apt update", "更新软件源", True),
                ("sudo apt upgrade -y", "升级所有软件包", True),
                ("sudo apt install", "安装软件", False),
                ("sudo apt search ", "搜索软件", False),
                ("sudo apt remove ", "卸载软件", False),
                ("apt list --installed", "已安装列表", True),
                ("sudo apt autoremove", "自动移除", True),
                ("sudo dpkg -i ", "安装deb包", False)
            ],
            "⚙️ 系统监控": [
                ("ps aux", "所有进程", True),
                ("df -h", "磁盘空间", True),
                ("free -h", "内存使用", True),
                ("top", "动态进程", True),
                ("htop", "增强top", True),
                ("uptime", "运行时间", True),
                ("who", "登录用户", True),
                ("dmesg | tail", "系统日志", True)
            ],
            "🌐 网络工具": [
                ("ip a", "网络接口", True),
                ("ifconfig", "网络配置", True),
                ("ping ", "测试连通", False),
                ("netstat -tulpn", "端口状态", True),
                ("ss -tulpn", "快速端口", True),
                ("curl ", "网络请求", False),
                ("wget ", "下载文件", False),
                ("ssh ", "远程连接", False)
            ],
            "🔧 权限管理": [
                ("chmod +x ", "添加执行权", False),
                ("chmod 755 ", "设置755", False),
                ("chmod 644 ", "设置644", False),
                ("chown ", "修改所有者", False),
                ("sudo -i", "切换到root", True),
                ("sudo !!", "sudo上一条", True),
                ("whoami", "当前用户", True),
                ("id", "用户信息", True)
            ],
            "📊 进程管理": [
                ("ps aux | grep ", "查找进程", False),
                ("kill ", "终止进程", False),
                ("killall ", "按名终止", False),
                ("pkill ", "模式终止", False),
                ("jobs", "后台任务", True),
                ("fg ", "调到前台", False),
                ("bg ", "调到后台", False),
                ("nohup ", "后台运行", False)
            ],
            "🔍 文本处理": [
                ("grep ", "搜索文本", False),
                ("grep -r ", "递归搜索", False),
                ("sed 's/old/new/g' ", "替换文本", False),
                ("awk '{print $1}' ", "提取字段", False),
                ("cut -d':' -f1", "切分字段", False),
                ("sort ", "排序", False),
                ("uniq", "去重", False),
                ("wc -l", "统计行数", False)
            ],
            "💾 压缩备份": [
                ("tar -czf archive.tar.gz ", "创建tar.gz", False),
                ("tar -xzf archive.tar.gz", "解压tar.gz", True),
                ("zip -r archive.zip ", "创建zip", False),
                ("unzip archive.zip", "解压zip", True),
                ("gzip ", "压缩文件", False),
                ("gunzip ", "解压gz", False)
            ],
            "🔐 系统信息": [
                ("uname -a", "内核信息", True),
                ("lsb_release -a", "Ubuntu版本", True),
                ("cat /etc/os-release", "发行版信息", True),
                ("lscpu", "CPU信息", True),
                ("lsblk", "磁盘信息", True),
                ("lspci", "PCI设备", True),
                ("lsusb", "USB设备", True),
                ("date", "日期时间", True),
                ("cal", "日历", True),
                ("history", "命令历史", True)
            ]
        }
        
        self.buttons = []
        
        # 创建分类和命令
        for group_name, commands in self.groups.items():
            self.category_list.addItem(group_name)
            
            panel = QWidget()
            panel_layout = QVBoxLayout(panel)
            panel_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            panel_layout.setSpacing(3)
            panel_layout.setContentsMargins(0, 0, 0, 0)
            
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet("QScrollArea { border: none; }")
            
            btn_widget = QWidget()
            btn_layout = QVBoxLayout(btn_widget)
            btn_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            btn_layout.setSpacing(3)
            
            for cmd, desc, auto_exec in commands:
                # 创建一个水平布局的按钮容器
                btn_container = QWidget()
                container_layout = QHBoxLayout(btn_container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.setSpacing(2)
                
                # 执行按钮（主要部分）
                exec_btn = QPushButton(f"  {cmd}")
                exec_btn.setToolTip(desc)
                exec_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                exec_btn.clicked.connect(lambda checked, x=cmd, exec=auto_exec: self.send_command(x, exec))
                exec_btn.setStyleSheet("""
                    QPushButton {
                        text-align: left;
                        padding: 8px;
                        font-size: 16px
                    }
                """)
                container_layout.addWidget(exec_btn, stretch=1)
                
                # 详情按钮（箭头图标）
                detail_btn = QPushButton("→")
                detail_btn.setToolTip("查看详细用法")
                detail_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                detail_btn.setFixedWidth(30)
                detail_btn.clicked.connect(lambda checked, x=cmd, d=desc: self.show_detail(x, d))
                detail_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #4a90e2;
                        color: white;
                        font-weight: bold;
                        padding: 8px 0;
                        border-radius: 4px;
                    }
                    QPushButton:hover {
                        background-color: #5a9ef2;
                    }
                """)
                container_layout.addWidget(detail_btn)
                
                btn_layout.addWidget(btn_container)
                self.buttons.append(exec_btn)
            
            scroll.setWidget(btn_widget)
            panel_layout.addWidget(scroll)
            self.command_stack.addWidget(panel)
        
        self.category_list.currentRowChanged.connect(self.command_stack.setCurrentIndex)
        self.search.textChanged.connect(self.filter_commands)
        self.category_list.setCurrentRow(0)
    
    def send_command(self, cmd, auto_exec):
        """发送命令到终端"""
        if self.terminal:
            self.terminal.send_to_terminal(cmd, auto_exec)
    
    def show_detail(self, cmd, desc):
        """显示命令详情"""
        dialog = CommandDetailDialog(cmd, desc, self)
        dialog.exec()
    
    def filter_commands(self, text):
        search = text.lower()
        for btn in self.buttons:
            if search in btn.text().lower():
                btn.show()
            else:
                btn.hide()


# ==============================================
# 系统信息界面
# ==============================================
class SystemInfoWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.start_updates()
    
    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # 标题
        title = QLabel("📊 系统信息")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #4a90e2;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        # 创建一个滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
        
        # 内容容器
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(15)
        
        # 基本信息卡片
        basic_group = QGroupBox(" 基本信息")
        basic_layout = QFormLayout(basic_group)
        basic_layout.setVerticalSpacing(10)
        basic_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.os_label = QLabel("加载中...")
        self.os_label.setStyleSheet("font-weight: bold; color: #4a90e2;")
        basic_layout.addRow("发行版:", self.os_label)
        
        self.kernel_label = QLabel("加载中...")
        self.kernel_label.setStyleSheet("font-weight: bold;")
        basic_layout.addRow("内核版本:", self.kernel_label)
        
        self.hostname_label = QLabel("加载中...")
        self.hostname_label.setStyleSheet("font-weight: bold;")
        basic_layout.addRow("主机名:", self.hostname_label)
        
        self.uptime_label = QLabel("加载中...")
        self.uptime_label.setStyleSheet("font-weight: bold;")
        basic_layout.addRow("运行时间:", self.uptime_label)
        
        content_layout.addWidget(basic_group)
        
        # CPU信息卡片
        cpu_group = QGroupBox(" CPU信息")
        cpu_layout = QFormLayout(cpu_group)
        cpu_layout.setVerticalSpacing(10)
        cpu_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.cpu_model_label = QLabel("加载中...")
        self.cpu_model_label.setStyleSheet("font-weight: bold; color: #4a90e2;")
        cpu_layout.addRow("型号:", self.cpu_model_label)
        
        self.cpu_cores_label = QLabel("加载中...")
        self.cpu_cores_label.setStyleSheet("font-weight: bold;")
        cpu_layout.addRow("核心数:", self.cpu_cores_label)
        
        self.cpu_threads_label = QLabel("加载中...")
        self.cpu_threads_label.setStyleSheet("font-weight: bold;")
        cpu_layout.addRow("线程数:", self.cpu_threads_label)
        
        self.cpu_freq_label = QLabel("加载中...")
        self.cpu_freq_label.setStyleSheet("font-weight: bold;")
        cpu_layout.addRow("主频:", self.cpu_freq_label)
        
        self.cpu_temp_label = QLabel("加载中...")
        self.cpu_temp_label.setStyleSheet("font-weight: bold;")
        cpu_layout.addRow("温度:", self.cpu_temp_label)
        
        # CPU使用率进度条
        cpu_usage_widget = QWidget()
        cpu_usage_layout = QHBoxLayout(cpu_usage_widget)
        cpu_usage_layout.setContentsMargins(0, 0, 0, 0)
        
        self.cpu_progress = QProgressBar()
        self.cpu_progress.setRange(0, 100)
        self.cpu_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #4d4d4d;
                border-radius: 5px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #4a90e2;
                border-radius: 5px;
            }
        """)
        cpu_usage_layout.addWidget(self.cpu_progress)
        
        self.cpu_percent_label = QLabel("0%")
        self.cpu_percent_label.setMinimumWidth(50)
        cpu_usage_layout.addWidget(self.cpu_percent_label)
        
        cpu_layout.addRow("使用率:", cpu_usage_widget)
        
        content_layout.addWidget(cpu_group)
        
        # 显卡信息卡片
        gpu_group = QGroupBox(" 显卡信息")
        gpu_layout = QFormLayout(gpu_group)
        gpu_layout.setVerticalSpacing(10)
        gpu_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.gpu_model_label = QLabel("加载中...")
        self.gpu_model_label.setStyleSheet("font-weight: bold; color: #4a90e2;")
        gpu_layout.addRow("型号:", self.gpu_model_label)
        
        self.gpu_driver_label = QLabel("加载中...")
        self.gpu_driver_label.setStyleSheet("font-weight: bold;")
        gpu_layout.addRow("驱动:", self.gpu_driver_label)
        
        self.gpu_memory_label = QLabel("加载中...")
        self.gpu_memory_label.setStyleSheet("font-weight: bold;")
        gpu_layout.addRow("显存:", self.gpu_memory_label)
        
        content_layout.addWidget(gpu_group)
        
        # 内存信息卡片
        mem_group = QGroupBox(" 内存信息")
        mem_layout = QFormLayout(mem_group)
        mem_layout.setVerticalSpacing(10)
        mem_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.mem_total_label = QLabel("加载中...")
        self.mem_total_label.setStyleSheet("font-weight: bold; color: #4a90e2;")
        mem_layout.addRow("总内存:", self.mem_total_label)
        
        self.mem_used_label = QLabel("加载中...")
        self.mem_used_label.setStyleSheet("font-weight: bold;")
        mem_layout.addRow("已用内存:", self.mem_used_label)
        
        self.mem_available_label = QLabel("加载中...")
        self.mem_available_label.setStyleSheet("font-weight: bold;")
        mem_layout.addRow("可用内存:", self.mem_available_label)
        
        # 内存使用率进度条
        mem_usage_widget = QWidget()
        mem_usage_layout = QHBoxLayout(mem_usage_widget)
        mem_usage_layout.setContentsMargins(0, 0, 0, 0)
        
        self.mem_progress = QProgressBar()
        self.mem_progress.setRange(0, 100)
        self.mem_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #4d4d4d;
                border-radius: 5px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #4a90e2;
                border-radius: 5px;
            }
        """)
        mem_usage_layout.addWidget(self.mem_progress)
        
        self.mem_percent_label = QLabel("0%")
        self.mem_percent_label.setMinimumWidth(50)
        mem_usage_layout.addWidget(self.mem_percent_label)
        
        mem_layout.addRow("使用率:", mem_usage_widget)
        
        content_layout.addWidget(mem_group)
        
        # 硬盘信息卡片
        disk_group = QGroupBox(" 硬盘信息")
        disk_layout = QFormLayout(disk_group)
        disk_layout.setVerticalSpacing(10)
        disk_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.disk_total_label = QLabel("加载中...")
        self.disk_total_label.setStyleSheet("font-weight: bold; color: #4a90e2;")
        disk_layout.addRow("总容量:", self.disk_total_label)
        
        self.disk_used_label = QLabel("加载中...")
        self.disk_used_label.setStyleSheet("font-weight: bold;")
        disk_layout.addRow("已用空间:", self.disk_used_label)
        
        self.disk_free_label = QLabel("加载中...")
        self.disk_free_label.setStyleSheet("font-weight: bold;")
        disk_layout.addRow("可用空间:", self.disk_free_label)
        
        # 硬盘使用率进度条
        disk_usage_widget = QWidget()
        disk_usage_layout = QHBoxLayout(disk_usage_widget)
        disk_usage_layout.setContentsMargins(0, 0, 0, 0)
        
        self.disk_progress = QProgressBar()
        self.disk_progress.setRange(0, 100)
        self.disk_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #4d4d4d;
                border-radius: 5px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #4a90e2;
                border-radius: 5px;
            }
        """)
        disk_usage_layout.addWidget(self.disk_progress)
        
        self.disk_percent_label = QLabel("0%")
        self.disk_percent_label.setMinimumWidth(50)
        disk_usage_layout.addWidget(self.disk_percent_label)
        
        disk_layout.addRow("使用率:", disk_usage_widget)
        
        content_layout.addWidget(disk_group)
        
        # 刷新按钮
        self.refresh_btn = QPushButton(" 刷新")
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a90e2;
                color: white;
                font-size: 14px;
                padding: 8px;
                border-radius: 4px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #5a9ef2;
            }
        """)
        self.refresh_btn.clicked.connect(self.refresh_all)
        content_layout.addWidget(self.refresh_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        
        scroll.setWidget(content)
        layout.addWidget(scroll)
    
    def start_updates(self):
        """启动定时更新"""
        self.refresh_all()
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_all)
        self.timer.start(5000)  # 5秒更新一次
    
    def refresh_all(self):
        """刷新所有信息"""
        self.refresh_basic_info()
        self.refresh_cpu_info()
        self.refresh_gpu_info()
        self.refresh_memory_info()
        self.refresh_disk_info()
    
    def refresh_basic_info(self):
        """刷新基本信息"""
        try:
            # 发行版信息
            result = subprocess.run("cat /etc/os-release | grep PRETTY_NAME | cut -d'\"' -f2", 
                                   shell=True, capture_output=True, text=True)
            if result.stdout:
                self.os_label.setText(result.stdout.strip())
            
            # 内核版本
            result = subprocess.run("uname -r", shell=True, capture_output=True, text=True)
            if result.stdout:
                self.kernel_label.setText(result.stdout.strip())
            
            # 主机名
            result = subprocess.run("uname -n", shell=True, capture_output=True, text=True)
            if result.stdout:
                self.hostname_label.setText(result.stdout.strip())
            
            # 运行时间
            result = subprocess.run("uptime -p | sed 's/up //'", shell=True, capture_output=True, text=True)
            if result.stdout:
                self.uptime_label.setText(result.stdout.strip())
        except Exception as e:
            print(f"刷新基本信息错误: {e}")
    
    def refresh_cpu_info(self):
        """刷新CPU信息（使用最通用的 /proc/cpuinfo）"""
        try:
            # ===== CPU型号 - 从 /proc/cpuinfo 读取 =====
            result = subprocess.run(
                "cat /proc/cpuinfo | grep -m 1 'model name' | cut -d':' -f2 | sed 's/^ //'", 
                shell=True, capture_output=True, text=True
            )
            
            if result.stdout and result.stdout.strip():
                cpu_model = result.stdout.strip()
            else:
                # 备选方法: lscpu
                result = subprocess.run(
                    "lscpu | grep -E 'Model name|型号名称' | awk -F: '{print $2}' | sed 's/^ //'", 
                    shell=True, capture_output=True, text=True
                )
                cpu_model = result.stdout.strip() if result.stdout else "未知"
            
            self.cpu_model_label.setText(cpu_model)
            
            # ===== CPU核心数 =====
            cores = psutil.cpu_count(logical=False)
            self.cpu_cores_label.setText(str(cores) if cores else "未知")
            
            # ===== CPU线程数 =====
            threads = psutil.cpu_count(logical=True)
            self.cpu_threads_label.setText(str(threads) if threads else "未知")
            
            # ===== CPU主频 =====
            freq = psutil.cpu_freq()
            if freq:
                self.cpu_freq_label.setText(f"{freq.current/1000:.2f} GHz")
            else:
                # 备选: 从 /proc/cpuinfo 读取
                result = subprocess.run(
                    "cat /proc/cpuinfo | grep -m 1 'cpu MHz' | awk -F: '{print $2}' | sed 's/^ //'", 
                    shell=True, capture_output=True, text=True
                )
                if result.stdout and result.stdout.strip():
                    try:
                        mhz = float(result.stdout.strip())
                        self.cpu_freq_label.setText(f"{mhz/1000:.2f} GHz")
                    except:
                        self.cpu_freq_label.setText("不支持")
                else:
                    self.cpu_freq_label.setText("不支持")
            
            # ===== CPU温度 =====
            temps = psutil.sensors_temperatures()
            temp_found = False
            # 常见的传感器名称
            for key in ['coretemp', 'cpu_thermal', 'acpitz', 'k10temp', 'zenpower', 'thinkpad']:
                if key in temps and temps[key]:
                    temp = temps[key][0].current
                    self.cpu_temp_label.setText(f"{temp:.1f}°C")
                    temp_found = True
                    break
            
            if not temp_found:
                self.cpu_temp_label.setText("不支持")
            
            # ===== CPU使用率 =====
            cpu_percent = psutil.cpu_percent(interval=0.1)
            self.cpu_progress.setValue(int(cpu_percent))
            self.cpu_percent_label.setText(f"{cpu_percent:.1f}%")
            
        except Exception as e:
            print(f"刷新CPU信息错误: {e}")
    
    def refresh_gpu_info(self):
        """刷新显卡信息"""
        try:
            # 获取显卡型号
            result = subprocess.run(
                "lspci | grep -E 'VGA|3D|Display' | head -1 | awk -F: '{print $3}' | sed 's/^ //'", 
                shell=True, capture_output=True, text=True
            )
            
            if result.stdout and result.stdout.strip():
                gpu_model = result.stdout.strip()
            else:
                gpu_model = "未检测到显卡"
            
            self.gpu_model_label.setText(gpu_model)
            
            # 尝试获取显卡驱动信息
            result = subprocess.run(
                "lspci -k | grep -A 2 -E 'VGA|3D|Display' | grep 'Kernel driver' | awk -F: '{print $2}' | sed 's/^ //'", 
                shell=True, capture_output=True, text=True
            )
            
            if result.stdout and result.stdout.strip():
                driver = result.stdout.strip()
            else:
                # 尝试另一种方式
                result = subprocess.run(
                    "lsmod | grep -E 'nvidia|amdgpu|i915|radeon|nouveau' | head -1 | awk '{print $1}'", 
                    shell=True, capture_output=True, text=True
                )
                driver = result.stdout.strip() if result.stdout else "未知"
            
            self.gpu_driver_label.setText(driver if driver else "未知")
            
            # 尝试获取显存信息
            if "NVIDIA" in gpu_model:
                result = subprocess.run(
                    "nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null", 
                    shell=True, capture_output=True, text=True
                )
                if result.stdout and result.stdout.strip():
                    mem = result.stdout.strip()
                    self.gpu_memory_label.setText(f"{mem} MB")
                else:
                    self.gpu_memory_label.setText("未知")
            elif "AMD" in gpu_model:
                # AMD显卡显存获取（简化）
                result = subprocess.run(
                    "lspci -v -s $(lspci | grep -E 'VGA|3D|Display' | head -1 | cut -d' ' -f1) | grep 'Memory at' | head -1", 
                    shell=True, capture_output=True, text=True
                )
                if result.stdout:
                    self.gpu_memory_label.setText("查看详情")
                else:
                    self.gpu_memory_label.setText("未知")
            else:
                self.gpu_memory_label.setText("未知")
                
        except Exception as e:
            print(f"刷新显卡信息错误: {e}")
            self.gpu_model_label.setText("获取失败")
            self.gpu_driver_label.setText("未知")
            self.gpu_memory_label.setText("未知")
    
    def refresh_memory_info(self):
        """刷新内存信息"""
        try:
            mem = psutil.virtual_memory()
            
            # 转换为GB
            total_gb = mem.total / 1024 / 1024 / 1024
            used_gb = mem.used / 1024 / 1024 / 1024
            available_gb = mem.available / 1024 / 1024 / 1024
            
            self.mem_total_label.setText(f"{total_gb:.2f} GB")
            self.mem_used_label.setText(f"{used_gb:.2f} GB")
            self.mem_available_label.setText(f"{available_gb:.2f} GB")
            
            # 使用率
            percent = mem.percent
            self.mem_progress.setValue(int(percent))
            self.mem_percent_label.setText(f"{percent:.1f}%")
            
        except Exception as e:
            print(f"刷新内存信息错误: {e}")
    
    def refresh_disk_info(self):
        """刷新硬盘信息（显示所有分区汇总）"""
        try:
            partitions = psutil.disk_partitions()
            total_used = 0
            total_free = 0
            total_size = 0
            
            for partition in partitions:
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    total_size += usage.total
                    total_used += usage.used
                    total_free += usage.free
                except:
                    continue
            
            total_gb = total_size / 1024 / 1024 / 1024
            used_gb = total_used / 1024 / 1024 / 1024
            free_gb = total_free / 1024 / 1024 / 1024
            percent = (total_used / total_size * 100) if total_size > 0 else 0
            
            self.disk_total_label.setText(f"{total_gb:.2f} GB")
            self.disk_used_label.setText(f"{used_gb:.2f} GB")
            self.disk_free_label.setText(f"{free_gb:.2f} GB")
            self.disk_progress.setValue(int(percent))
            self.disk_percent_label.setText(f"{percent:.1f}%")
            
        except Exception as e:
            print(f"刷新硬盘信息错误: {e}")


# ==============================================
# 可切换侧边栏（改为菜单按钮）
# ==============================================
class SideBar(QWidget):
    def __init__(self, terminal, ssh_widget, file_transfer_dialog, sysinfo_widget, file_search_dialog):
        super().__init__()
        self.terminal = terminal
        self.ssh_widget = ssh_widget
        self.file_transfer_dialog = file_transfer_dialog
        self.sysinfo_widget = sysinfo_widget
        self.file_search_dialog = file_search_dialog
        self.current_mode = "command"
        
        self.setFixedWidth(300)
        self.setStyleSheet("""
            QWidget {
                background-color: #2d2d2d;
                color: #ffffff;
            }
            QPushButton {
                background-color: #3d3d3d;
                color: #ffffff;
                border: 1px solid #4d4d4d;
                padding: 8px;
                border-radius: 4px;
                text-align: left;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
                border-color: #4a90e2;
            }
            QPushButton:pressed {
                background-color: #4a90e2;
            }
            QToolTip {
                background-color: #4a90e2;
                color: #ffffff;
                border: none;
                padding: 5px;
                border-radius: 3px;
                font-size: 12px;
            }
            QMenu {
                background-color: #3d3d3d;
                color: #ffffff;
                border: 1px solid #4d4d4d;
            }
            QMenu::item {
                padding: 8px 20px;
            }
            QMenu::item:selected {
                background-color: #4a90e2;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        
        # 创建菜单按钮
        self.menu_btn = QPushButton("☰ 菜单")
        self.menu_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a90e2;
                color: white;
                font-weight: bold;
                text-align: center;
                padding: 12px;
                font-size: 14px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #5a9ef2;
            }
        """)
        self.menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # 创建菜单
        self.menu = QMenu(self)
        
        # 添加菜单项
        self.cmd_action = self.menu.addAction("📦 命令面板")
        self.cmd_action.triggered.connect(lambda: self.switch_mode("command"))
        
        self.ssh_action = self.menu.addAction("🔌 SSH连接")
        self.ssh_action.triggered.connect(lambda: self.switch_mode("ssh"))
        
        self.sysinfo_action = self.menu.addAction("📊 系统信息")
        self.sysinfo_action.triggered.connect(lambda: self.switch_mode("sysinfo"))
        
        self.menu.addSeparator()
        
        self.file_action = self.menu.addAction("📁 文件传输")
        self.file_action.triggered.connect(self.open_file_transfer)
        
        self.search_action = self.menu.addAction("🔍 文件搜索")
        self.search_action.triggered.connect(self.open_file_search)
        
        self.menu_btn.setMenu(self.menu)
        layout.addWidget(self.menu_btn)
        
        # 当前模式显示
        self.mode_label = QLabel("当前: 📦 命令面板")
        self.mode_label.setStyleSheet("color: #888; font-size: 11px; padding: 5px;")
        layout.addWidget(self.mode_label)
        
        # 堆叠窗口
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        
        self.command_panel = CommandPanel(terminal)
        self.stack.addWidget(self.command_panel)
        self.stack.addWidget(ssh_widget)
        self.stack.addWidget(sysinfo_widget)
        
        # 默认显示命令面板
        self.stack.setCurrentIndex(0)
    
    def switch_mode(self, mode):
        if mode == self.current_mode:
            return
        
        self.current_mode = mode
        
        if mode == "command":
            self.stack.setCurrentIndex(0)
            self.mode_label.setText("当前: 📦 命令面板")
        elif mode == "ssh":
            self.stack.setCurrentIndex(1)
            self.mode_label.setText("当前: 🔌 SSH连接")
        elif mode == "sysinfo":
            self.stack.setCurrentIndex(2)
            self.mode_label.setText("当前: 📊 系统信息")
    
    def open_file_transfer(self):
        """打开文件传输对话框"""
        if self.terminal.ssh_manager and self.terminal.ssh_manager.connected:
            self.file_transfer_dialog.show()
        else:
            QMessageBox.warning(self, "提示", "请先连接SSH服务器")
    
    def open_file_search(self):
        """打开文件搜索对话框"""
        self.file_search_dialog.show()


# ==============================================
# 主窗口
# ==============================================
class XTerminal(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SysTerm - 终端")
        self.resize(1300, 800)
        
        # 初始化 SSH 管理器
        self.ssh_manager = SSHManager()
        self.ssh_manager.output_received.connect(self.on_ssh_output)
        self.ssh_manager.connection_status.connect(self.on_ssh_status)
        
        # 本地终端相关
        self.master = None
        self.pid = None
        self.local_mode = True
        
        # 创建本地 PTY
        self.create_pty()
        
        # 创建 WebView
        self.webview = QWebEngineView()
        
        # 创建 WebChannel
        self.channel = QWebChannel()
        self.terminal_handler = TerminalHandler()
        self.terminal_handler.master = self.master
        self.terminal_handler.ssh_manager = self.ssh_manager
        self.terminal_handler.local_mode = True
        self.channel.registerObject("terminal", self.terminal_handler)
        self.webview.page().setWebChannel(self.channel)
        
        # 连接信号
        self.terminal_handler.dataReceived.connect(self.on_terminal_input)
        
        # 加载 HTML
        self.webview.setHtml(self.create_html())
        
        # 定时器读取本地输出
        self.timer = QTimer()
        self.timer.timeout.connect(self.read_pty)
        self.timer.start(10)
        
        # 创建文件传输对话框
        self.file_transfer_dialog = FileTransferDialog(self.ssh_manager, self)
        
        # 创建文件搜索对话框
        self.file_search_dialog = FileSearchDialog(self)
        
        # 创建 SSH 连接界面
        self.ssh_widget = SSHConnectWidget()
        self.ssh_widget.connect_password.connect(self.on_ssh_connect_password)
        self.ssh_widget.connect_key.connect(self.on_ssh_connect_key)
        
        # 创建系统信息界面
        self.sysinfo_widget = SystemInfoWidget()
        
        # 创建界面
        self.setup_ui()
        self.setup_menu()
    
    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.sidebar = SideBar(self, self.ssh_widget, self.file_transfer_dialog, self.sysinfo_widget, self.file_search_dialog)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.webview)
        splitter.addWidget(self.sidebar)
        splitter.setSizes([900, 300])
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #3d3d3d;
                width: 2px;
            }
        """)
        
        layout.addWidget(splitter)
    
    def setup_menu(self):
        menubar = self.menuBar()
        menubar.setStyleSheet("""
            QMenuBar {
                background-color: #2d2d2d;
                color: #ffffff;
                border-bottom: 1px solid #3d3d3d;
            }
            QMenuBar::item {
                padding: 5px 10px;
                background-color: transparent;
                border-radius: 3px;
            }
            QMenuBar::item:selected {
                background-color: #4a90e2;
            }
            QMenu {
                background-color: #2d2d2d;
                color: #ffffff;
                border: 1px solid #3d3d3d;
                border-radius: 4px;
            }
            QMenu::item {
                padding: 5px 20px;
            }
            QMenu::item:selected {
                background-color: #4a90e2;
            }
        """)
        
        # 文件菜单
        file_menu = menubar.addMenu("文件")
        file_menu.addAction("新窗口", self.new_window)
        file_menu.addSeparator()
        file_menu.addAction("退出", self.close)
        
        # 搜索菜单
        search_menu = menubar.addMenu("搜索")
        search_menu.addAction("🔍 文件搜索", self.file_search_dialog.show)
        
        # 连接菜单
        conn_menu = menubar.addMenu("连接")
        
        self.local_action = QAction("本地终端", self)
        self.local_action.setCheckable(True)
        self.local_action.setChecked(True)
        self.local_action.triggered.connect(self.switch_to_local)
        conn_menu.addAction(self.local_action)
        
        self.ssh_action = QAction("SSH 远程", self)
        self.ssh_action.setCheckable(True)
        self.ssh_action.triggered.connect(lambda: self.sidebar.switch_mode("ssh"))
        conn_menu.addAction(self.ssh_action)
        
        conn_menu.addSeparator()
        conn_menu.addAction("断开连接", self.disconnect_ssh)
        
        # 传输菜单
        transfer_menu = menubar.addMenu("传输")
        transfer_menu.addAction("📤 上传文件", lambda: self.show_file_transfer("upload"))
        transfer_menu.addAction("📥 下载文件", lambda: self.show_file_transfer("download"))
        transfer_menu.addSeparator()
        transfer_menu.addAction("📁 文件传输管理器", self.file_transfer_dialog.show)
        
        # 工具菜单
        tools_menu = menubar.addMenu("工具")
        tools_menu.addAction("📊 系统信息", lambda: self.sidebar.switch_mode("sysinfo"))
        
        # 视图菜单
        view_menu = menubar.addMenu("视图")
        toggle_sidebar = QAction("切换侧边栏", self)
        toggle_sidebar.setShortcut("F9")
        toggle_sidebar.triggered.connect(
            lambda: self.sidebar.setVisible(not self.sidebar.isVisible())
        )
        view_menu.addAction(toggle_sidebar)
        
        # 帮助菜单
        help_menu = menubar.addMenu("帮助")
        help_menu.addAction("关于", self.show_about)
    
    def create_html(self):
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm/css/xterm.css" />
            <script src="https://cdn.jsdelivr.net/npm/xterm/lib/xterm.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit/lib/xterm-addon-fit.js"></script>
            <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
            <style>
                body { margin: 0; padding: 5px; background: #1e1e1e; height: 100vh; box-sizing: border-box; }
                #terminal { height: 100%; width: 100%; }
            </style>
        </head>
        <body>
            <div id="terminal"></div>
            <script>
                var terminal = null;
                var fitAddon = null;
                
                function initTerminal() {
                    terminal = new Terminal({
                        theme: {
                            background: '#1e1e1e',
                            foreground: '#d0d0d0',
                            cursor: '#4a90e2',
                            selection: 'rgba(74, 144, 226, 0.3)'
                        },
                        fontFamily: 'Monospace',
                        fontSize: 12,
                        cursorBlink: true,
                        cursorStyle: 'block',
                        scrollback: 1000,
                        tabStopWidth: 4
                    });
                    
                    fitAddon = new FitAddon.FitAddon();
                    terminal.loadAddon(fitAddon);
                    terminal.open(document.getElementById('terminal'));
                    fitAddon.fit();
                    
                    terminal.onData(function(data) {
                        if (window.terminalHandler) {
                            window.terminalHandler.write(data);
                        }
                    });
                    
                    setTimeout(function() { terminal.focus(); }, 100);
                    terminal.writeln('\\x1b[1;32m按 F9 切换侧边栏\\x1b[0m');
                    terminal.write('$ ');
                }
                
                new QWebChannel(qt.webChannelTransport, function(channel) {
                    window.terminalHandler = channel.objects.terminal;
                    initTerminal();
                    
                    window.terminalHandler.output.connect(function(data) {
                        if (terminal) terminal.write(data);
                    });
                    
                    window.addEventListener('resize', function() {
                        if (fitAddon) {
                            fitAddon.fit();
                            if (window.terminalHandler) {
                                var dims = fitAddon.proposeDimensions();
                                if (dims) window.terminalHandler.resize(dims.cols, dims.rows);
                            }
                        }
                    });
                });
            </script>
        </body>
        </html>
        """
    
    def create_pty(self):
        """创建本地伪终端"""
        self.master, self.slave = pty.openpty()
        self.pid = os.fork()
        
        if self.pid == 0:
            os.setsid()
            try:
                fcntl.ioctl(self.slave, termios.TIOCSCTTY, 0)
            except:
                pass
            
            os.dup2(self.slave, 0)
            os.dup2(self.slave, 1)
            os.dup2(self.slave, 2)
            
            if self.slave > 2:
                os.close(self.slave)
            
            os.environ['TERM'] = 'xterm-256color'
            os.environ['COLORTERM'] = 'truecolor'
            
            os.execvp("/bin/bash", ["bash", "--login"])
            os._exit(1)
        else:
            os.close(self.slave)
            flags = fcntl.fcntl(self.master, fcntl.F_GETFL)
            fcntl.fcntl(self.master, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    
    def read_pty(self):
        """读取本地 PTY 输出"""
        if not self.local_mode or not self.master:
            return
        
        try:
            r, _, _ = select.select([self.master], [], [], 0)
            if self.master in r:
                data = os.read(self.master, 4096)
                if data:
                    self.terminal_handler.output.emit(data.decode('utf-8', errors='ignore'))
        except (BlockingIOError, OSError):
            pass
        except Exception as e:
            print(f"读取错误: {e}")
    
    def on_terminal_input(self, data):
        """处理终端输入"""
        if self.local_mode and self.master:
            try:
                os.write(self.master, data.encode('utf-8'))
            except Exception as e:
                print(f"写入错误: {e}")
        elif not self.local_mode and self.ssh_manager.connected:
            self.ssh_manager.send(data.encode('utf-8'))
    
    def on_ssh_output(self, data):
        """处理 SSH 输出"""
        self.terminal_handler.output.emit(data)
    
    def on_ssh_status(self, success, message):
        """处理 SSH 连接状态"""
        self.ssh_widget.set_status(success, message)
        
        if success:
            self.local_mode = False
            self.terminal_handler.local_mode = False
            self.local_action.setChecked(False)
            self.ssh_action.setChecked(True)
            self.on_ssh_output(f"\r\n\x1b[1;32m✅ {message}\x1b[0m\r\n")
        else:
            self.local_mode = True
            self.terminal_handler.local_mode = True
    
    def on_ssh_connect_password(self, host, port, username, password):
        """密码方式 SSH 连接"""
        success, msg = self.ssh_manager.connect_password(host, port, username, password)
    
    def on_ssh_connect_key(self, host, port, username, key_path, password):
        """密钥方式 SSH 连接"""
        success, msg = self.ssh_manager.connect_key(host, port, username, key_path, password)
    
    def send_to_terminal(self, cmd, auto_exec):
        """发送命令到终端"""
        if self.local_mode and self.master:
            try:
                if auto_exec:
                    os.write(self.master, (cmd + "\n").encode('utf-8'))
                else:
                    os.write(self.master, cmd.encode('utf-8'))
                # 确保焦点回到终端
                self.webview.setFocus()
                self.webview.page().runJavaScript("terminal.focus();")
            except Exception as e:
                print(f"命令错误: {e}")
        elif not self.local_mode and self.ssh_manager.connected:
            try:
                if auto_exec:
                    self.ssh_manager.send((cmd + "\n").encode('utf-8'))
                else:
                    self.ssh_manager.send(cmd.encode('utf-8'))
                self.webview.setFocus()
                self.webview.page().runJavaScript("terminal.focus();")
            except Exception as e:
                print(f"SSH命令错误: {e}")
    
    def switch_to_local(self):
        """切换到本地模式"""
        if not self.local_mode:
            if self.ssh_manager.connected:
                self.ssh_manager.disconnect()
            self.local_mode = True
            self.terminal_handler.local_mode = True
            self.local_action.setChecked(True)
            self.ssh_action.setChecked(False)
            self.on_ssh_output("\r\n\x1b[1;33m已切换回本地终端\x1b[0m\r\n")
    
    def disconnect_ssh(self):
        """断开 SSH 连接"""
        if self.ssh_manager.connected:
            self.ssh_manager.disconnect()
            self.switch_to_local()
    
    def show_file_transfer(self, mode):
        """显示文件传输对话框"""
        if self.ssh_manager and self.ssh_manager.connected:
            if mode == "upload":
                self.file_transfer_dialog.tab_widget.setCurrentIndex(0)
            else:
                self.file_transfer_dialog.tab_widget.setCurrentIndex(1)
            self.file_transfer_dialog.show()
        else:
            QMessageBox.warning(self, "提示", "请先连接SSH服务器")
    
    def new_window(self):
        os.system(f"python3 {sys.argv[0]} &")
    
    def show_about(self):
        QMessageBox.about(self, "关于 SysTerm",
            "<b>SysTerm终端</b><br><br>"
            "版本: 1.0.1<br>"
            "基于 xterm.js + QtWebEngine<br><br>"
            "✨ 主要功能:<br>"
            "• SSH 远程连接<br>"
            "• 文件传输<br>"
            "• 系统信息监控<br>"
            "• 文件搜索<br>"
            "• 智能命令面板<br>"
            "• 9 大分类，81 个常用命令"
        )
    
    def closeEvent(self, event):
        try:
            if self.pid:
                os.kill(self.pid, signal.SIGTERM)
            if self.master:
                os.close(self.master)
            self.ssh_manager.disconnect()
        except:
            pass
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    window = XTerminal()
    window.show()
    
    signal.signal(signal.SIGINT, lambda s, f: app.quit())
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()