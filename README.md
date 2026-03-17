# SysTerm 终端


 🎯 项目背景
Linux 命令太多记不住？每次都要去搜太麻烦？  
SysTerm 就是为了解决这个问题——**把常用命令做成面板，边用边学，用多了自然就记住了。**

 ✨ 核心特性

 1. 稳定可靠的终端核心
 xterm.js + QtWebEngine，支持：
- 光标闪烁、颜色高亮
- 方向键查看/切换历史命令
- vim、htop、tmux 等所有终端应用

### 2. 智能命令面板（核心亮点）
内置 **9 大分类、81 个常用 Linux 命令**：

| 分类 | 数量 | 示例命令 |
|------|------|----------|
| 📁 文件操作 | 17 | ls, cd, mkdir, rm, cp, mv, cat... |
| 📦 包管理 | 8 | apt update, apt install, apt search... |
| ⚙️ 系统监控 | 8 | ps, df, free, top, htop... |
| 🌐 网络工具 | 8 | ip, ping, netstat, ssh, curl... |
| 🔧 权限管理 | 8 | chmod, chown, sudo, whoami... |
| 📊 进程管理 | 8 | kill, jobs, fg, bg, nohup... |
| 🔍 文本处理 | 8 | grep, sed, awk, cut, sort... |
| 💾 压缩备份 | 6 | tar, zip, gzip... |
| 🔐 系统信息 | 10 | uname, lscpu, lsblk, date... |

**交互设计：**
- 🖱️ 鼠标悬停 → 查看命令详细说明
- 👆 点击按钮 → 自动输入命令（需要补全的只输入不执行）
- ⌨️ F9 快捷键 → 一键切换侧边栏

 3. 两种使用方式
- **学习模式**：悬停看说明，了解命令用途
- **效率模式**：直接点击执行，不用手敲

## 📦 安装方式
 方式：.deb 包（推荐）
```bash
sudo dpkg -i systerm_1.0.0_all.deb
sudo apt update
sudo apt install -f
systerm

 ⌨️ 快捷键
- `F9`：切换侧边栏命令面板


 🤝 贡献
欢迎提 Issue 和 PR！  
如果你有常用的命令想加进去，直接在 Issue 里告诉我。

 📄 许可证
MIT
