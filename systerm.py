#!/usr/bin/env python3
import sys
import os
import pty
import fcntl
import select
import signal
import termios
import struct
import re
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWebEngineWidgets import *
from PyQt6.QtWebEngineCore import *
from PyQt6.QtWebChannel import *

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
    
    @pyqtSlot(str)
    def write(self, data):
        """接收来自 JavaScript 的数据"""
        self.dataReceived.emit(data)
    
    @pyqtSlot(int, int)
    def resize(self, cols, rows):
        """调整终端大小"""
        try:
            if self.master:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.master, termios.TIOCSWINSZ, winsize)
        except:
            pass


# ==============================================
# 侧边栏
# ==============================================
class SideBar(QWidget):
    def __init__(self, terminal):
        super().__init__()
        self.terminal = terminal
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
            QLineEdit {
                background-color: #3d3d3d;
                color: #ffffff;
                border: 1px solid #4d4d4d;
                padding: 8px;
                border-radius: 4px;
                font-size: 13px;
            }
            QListWidget {
                background-color: #3d3d3d;
                color: #ffffff;
                border: 1px solid #4d4d4d;
                border-radius: 4px;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 5px;
            }
            QListWidget::item:selected {
                background-color: #4a90e2;
            }
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QToolTip {
                background-color: #4a90e2;
                color: #ffffff;
                border: none;
                padding: 5px;
                border-radius: 3px;
                font-size: 12px;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # 标题
        title = QLabel("📦 命令面板")
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 5px;")
        layout.addWidget(title)
        
        # 搜索框
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 搜索命令...")
        layout.addWidget(self.search)
        
        # 分类列表
        self.category_list = QListWidget()
        self.category_list.setFixedHeight(120)
        layout.addWidget(self.category_list)
        
        # 命令区域
        self.command_stack = QStackedWidget()
        layout.addWidget(self.command_stack, stretch=1)
        
        # 命令分组（带描述）- 去掉了快捷操作
        self.groups = {
            "📁 文件操作": [
                ("ls -l", "列出所有文件详细信息", True),
                ("ls -la", "列出所有文件及隐藏文件详细信息", True),
                ("cd ..", "返回上级目录", True),
                ("cd ", "后面接目录，切换到某目录", False),
                ("pwd", "显示当前目录路径", True),
                ("mkdir ", "创建目录，后面接目录名", False),
                ("rm ", "删除文件，后面接文件名", False),
                ("rm -rf ", "删除目录及全部文件（谨慎使用）", False),
                ("mv ", "移动文件或重命名：mv 文件 目录 或 mv 文件名1 文件名2", False),
                ("cp -r ", "递归复制目录，后面接源目录 目标目录", False),
                ("cat ", "查看文件内容，后面接文件名", False),
                ("less ", "分页查看文件内容", False),
                ("head ", "查看文件开头几行，如 head -n 20 file", False),
                ("tail -f ", "实时查看文件追加内容", False),
                ("touch ", "创建空文件或更新文件时间戳", False),
                ("find . -name ", "按名称查找文件", False),
                ("du -sh ", "查看目录或文件大小", False)
            ],
            
            "📦 包管理": [
                ("sudo apt update", "检查软件包更新", True),
                ("sudo apt upgrade -y", "更新所有软件包", True),
                ("sudo apt install", "安装软件包，后面接要安装的软件名", False),
                ("sudo apt search ", "搜索相关软件，后面加关键词", False),
                ("sudo apt remove ", "卸载软件包", False),
                ("apt list --installed", "列出已安装的软件包", True),
                ("sudo apt autoremove", "自动卸载不需要的依赖包", True),
                ("sudo dpkg -i ", "安装本地 .deb 包", False)
            ],
            
            "⚙️ 系统监控": [
                ("ps aux", "查看所有进程占用资源情况", True),
                ("df -h", "查看硬盘空间使用情况", True),
                ("free -h", "查看内存使用情况", True),
                ("top", "动态查看进程资源占用，按 q 退出", True),
                ("htop", "增强版的 top 查看器", True),
                ("uptime", "查看系统运行时间和负载", True),
                ("who", "查看当前登录用户", True),
                ("dmesg | tail", "查看最近的系统日志", True)
            ],
            
            "🌐 网络工具": [
                ("ip a", "查看网络接口信息", True),
                ("ifconfig", "查看网络配置（旧版）", True),
                ("ping ", "测试网络连通性，后面加 IP 或域名", False),
                ("netstat -tulpn", "查看端口监听状态", True),
                ("ss -tulpn", "更快的 netstat 替代品", True),
                ("curl ", "发送网络请求，后面加 URL", False),
                ("wget ", "下载文件，后面加 URL", False),
                ("ssh ", "远程连接服务器，后面加 user@host", False)
            ],
            
            "🔧 权限管理": [
                ("chmod +x ", "添加文件执行权限，后面加文件名", False),
                ("chmod 755 ", "设置文件权限为 rwxr-xr-x（常用于可执行文件）", False),
                ("chmod 644 ", "设置文件权限为 rw-r--r--", False),
                ("chown ", "修改文件所有者，如 chown user:group file", False),
                ("sudo -i", "切换到 root 用户", True),
                ("sudo !!", "用 sudo 执行上一条命令", True),
                ("whoami", "显示当前用户名", True),
                ("id", "显示当前用户信息", True)
            ],
            
            "📊 进程管理": [
                ("ps aux | grep ", "查找特定进程", False),
                ("kill ", "终止进程，后面加 PID", False),
                ("killall ", "按名称终止进程", False),
                ("pkill ", "按模式终止进程", False),
                ("jobs", "查看后台任务", True),
                ("fg ", "将后台任务调到前台，后面加任务号", False),
                ("bg ", "将前台任务调到后台", False),
                ("nohup ", "使命令在退出后继续运行", False)
            ],
            
            "🔍 文本处理": [
                ("grep ", "在文件中搜索文本，后面加'模式' 文件", False),
                ("grep -r ", "递归搜索目录中的文件", False),
                ("sed 's/old/new/g' ", "替换文本内容", False),
                ("awk '{print $1}' ", "提取每行第一个字段", False),
                ("cut -d':' -f1", "按冒号切分取第一个字段", False),
                ("sort ", "排序文本行", False),
                ("uniq", "去除重复行", False),
                ("wc -l", "统计文件内容的行数", False)
            ],
            
            "💾 压缩备份": [
                ("tar -czf archive.tar.gz ", "创建 tar.gz 压缩包", False),
                ("tar -xzf archive.tar.gz", "解压 tar.gz 文件", True),
                ("zip -r archive.zip ", "创建 zip 压缩包", False),
                ("unzip archive.zip", "解压 zip 文件", True),
                ("gzip ", "压缩文件为 .gz", False),
                ("gunzip ", "解压 .gz 文件", False)
            ],
            
            "🔐 系统信息": [
                ("uname -a", "查看系统内核信息", True),
                ("lsb_release -a", "查看 Ubuntu 版本信息", True),
                ("cat /etc/os-release", "查看系统发行版信息", True),
                ("lscpu", "查看 CPU 信息", True),
                ("lsblk", "查看磁盘分区信息", True),
                ("lspci", "查看 PCI 设备信息", True),
                ("lsusb", "查看 USB 设备信息", True),
                ("date", "查看当前日期时间", True),
                ("cal", "查看日历", True),
                ("history", "查看命令历史", True)
            ]
        }
        
        self.buttons = []
        
        # 创建分类和命令
        for group_name, commands in self.groups.items():
            self.category_list.addItem(group_name)
            
            # 创建命令面板
            panel = QWidget()
            panel_layout = QVBoxLayout(panel)
            panel_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            panel_layout.setSpacing(5)
            panel_layout.setContentsMargins(0, 0, 0, 0)
            
            # 滚动区域
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet("QScrollArea { border: none; }")
            
            btn_widget = QWidget()
            btn_layout = QVBoxLayout(btn_widget)
            btn_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            btn_layout.setSpacing(5)
            
            # 添加命令按钮（带悬停提示和自动执行标志）
            for cmd, desc, auto_exec in commands:
                btn = QPushButton(f"  {cmd}")
                btn.setToolTip(desc)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda checked, x=cmd, exec=auto_exec: self.send_command(x, exec))
                btn_layout.addWidget(btn)
                self.buttons.append(btn)
            
            scroll.setWidget(btn_widget)
            panel_layout.addWidget(scroll)
            self.command_stack.addWidget(panel)
        
        # 连接信号
        self.category_list.currentRowChanged.connect(self.command_stack.setCurrentIndex)
        self.search.textChanged.connect(self.filter_commands)
        
        # 默认选中第一项
        self.category_list.setCurrentRow(0)
    
    def send_command(self, cmd, auto_exec):
        """发送命令到终端"""
        if self.terminal and self.terminal.master:
            try:
                if auto_exec:
                    # 完整命令，直接执行
                    os.write(self.terminal.master, (cmd + "\n").encode('utf-8'))
                    print(f"执行命令: {cmd}")
                else:
                    # 需要用户补全的命令，只输入不执行
                    os.write(self.terminal.master, cmd.encode('utf-8'))
                    print(f"输入命令: {cmd} (等待用户补全)")
            except Exception as e:
                print(f"命令执行错误: {e}")
        
        # 恢复焦点
        QTimer.singleShot(50, self.restore_focus)
    
    def restore_focus(self):
        """恢复终端焦点"""
        if self.terminal:
            self.terminal.webview.setFocus()
    
    def filter_commands(self, text):
        """过滤命令"""
        search = text.lower()
        for btn in self.buttons:
            if search in btn.text().lower():
                btn.show()
            else:
                btn.hide()


# ==============================================
# 主终端窗口
# ==============================================
class XTerminal(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("systerm")
        self.resize(1200, 700)
        
        # 创建 WebView
        self.webview = QWebEngineView()
        
        # 创建 WebChannel
        self.channel = QWebChannel()
        
        # 创建终端对象
        self.terminal_handler = TerminalHandler()
        self.channel.registerObject("terminal", self.terminal_handler)
        
        # 设置 WebChannel
        self.webview.page().setWebChannel(self.channel)
        
        # 加载 xterm.js
        html = self.create_html()
        self.webview.setHtml(html)
        
        # 连接信号
        self.terminal_handler.dataReceived.connect(self.on_terminal_data)
        
        # 保存 master
        self.master = None
        self.pid = None
        
        # 创建 PTY
        self.create_pty()
        
        # 定时器读取输出
        self.timer = QTimer()
        self.timer.timeout.connect(self.read_pty)
        self.timer.start(10)
        
        # 创建界面布局
        self.setup_ui()
        
        # 创建菜单栏
        self.setup_menu()
    
    def setup_ui(self):
        """设置界面"""
        central = QWidget()
        self.setCentralWidget(central)
        
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # 创建侧边栏
        self.sidebar = SideBar(self)
        
        # 创建分割器
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
        """设置菜单栏"""
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
        
        # 编辑菜单
        edit_menu = menubar.addMenu("编辑")
        edit_menu.addAction("复制", self.copy)
        edit_menu.addAction("粘贴", self.paste)
        edit_menu.addSeparator()
        edit_menu.addAction("清屏", self.clear_screen)
        
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
        """创建包含 xterm.js 的 HTML"""
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm/css/xterm.css" />
            <script src="https://cdn.jsdelivr.net/npm/xterm/lib/xterm.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit/lib/xterm-addon-fit.js"></script>
            <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
            <style>
                body { 
                    margin: 0; 
                    padding: 5px; 
                    background: #1e1e1e;
                    height: 100vh;
                    box-sizing: border-box;
                }
                #terminal { 
                    height: 100%; 
                    width: 100%;
                }
                .xterm-viewport {
                    overflow-y: auto !important;
                }
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
                    
                    setTimeout(function() {
                        terminal.focus();
                    }, 100);
                    
                    terminal.writeln('\\x1b[1;34m欢迎使用 SysTerm \\x1b[0m');
                    terminal.writeln('\\x1b[1;32m按 F9 可以切换侧边栏命令面板\\x1b[0m\\n');
                    terminal.write('$ ');
                }
                
                new QWebChannel(qt.webChannelTransport, function(channel) {
                    window.terminalHandler = channel.objects.terminal;
                    initTerminal();
                    
                    window.terminalHandler.output.connect(function(data) {
                        if (terminal) {
                            terminal.write(data);
                        }
                    });
                    
                    window.addEventListener('resize', function() {
                        if (fitAddon) {
                            fitAddon.fit();
                            if (window.terminalHandler) {
                                var dims = fitAddon.proposeDimensions();
                                if (dims) {
                                    window.terminalHandler.resize(dims.cols, dims.rows);
                                }
                            }
                        }
                    });
                });
            </script>
        </body>
        </html>
        """
    
    def create_pty(self):
        """创建伪终端"""
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
            self.terminal_handler.master = self.master
    
    def read_pty(self):
        """读取 PTY 输出"""
        try:
            data = os.read(self.master, 4096)
            if data:
                self.terminal_handler.output.emit(data.decode('utf-8', errors='ignore'))
        except (BlockingIOError, OSError):
            pass
        except Exception as e:
            print(f"读取错误: {e}")
    
    def on_terminal_data(self, data):
        """接收终端输入"""
        try:
            os.write(self.master, data.encode('utf-8'))
        except Exception as e:
            print(f"写入错误: {e}")
    
    def new_window(self):
        os.system(f"python3 {sys.argv[0]} &")
    
    def copy(self):
        self.webview.page().runJavaScript("terminal.getSelection()")
    
    def paste(self):
        clipboard = QApplication.clipboard().text()
        if clipboard:
            try:
                os.write(self.master, clipboard.encode('utf-8'))
            except Exception as e:
                print(f"粘贴错误: {e}")
    
    def clear_screen(self):
        try:
            os.write(self.master, b"\x0c")  # Ctrl+L
        except Exception as e:
            print(f"清屏错误: {e}")
    
    def show_about(self):
        QMessageBox.about(
            self,
            "关于 SysTerm",
            "基于 xterm.js + QtWebEngine<br>"
            "版本: 1.0.0<br>"
            "快捷键: F9 切换侧边栏"
        )
    
    def closeEvent(self, event):
        try:
            if self.pid:
                os.kill(self.pid, signal.SIGTERM)
            if self.master:
                os.close(self.master)
        except:
            pass
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    app.setApplicationName("SysTerm")
    app.setOrganizationName("SysTerm")
    
    window = XTerminal()
    window.show()
    
    signal.signal(signal.SIGINT, lambda s, f: app.quit())
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
