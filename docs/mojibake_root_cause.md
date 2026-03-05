# 乱码根因与预防

## 〇、AI Agent 编码安全须知（必读）

**所有 AI Agent 在编辑本项目时请务必遵守：**

1. **禁止经由终端输出修改文件**：勿使用 `echo`、`cat`、heredoc、PowerShell 管道等将内容写入源文件，终端控制台编码（如 PowerShell 默认 GBK/系统代码页）与文件 UTF-8 不一致，会直接产生或放大乱码。

2. **禁止使用不安全的文本替换链路**：若编辑流程涉及“读入 → 处理后 → 通过终端重定向/管道写回”，终端编码会介入，导致双重编码、引号断裂、私有区字符注入。

3. **优先使用编辑器/API 级写入**：通过 IDE 的 search_replace、write 等直接写入磁盘，确保与源文件同编码（UTF-8）。

4. **大文件编辑后必验证**：运行 `python -m py_compile src/*.py`，发现语法错误时优先怀疑编码问题，而非逻辑错误。

5. **Codex 等外部 AI 插件**：在 Windows 上经由 PowerShell 执行编辑时，已证实会引入乱码与引号断裂，编辑后需人工或脚本校验。

---

## 一、为什么不断产生乱码

### 1. 双重编码（Double Encoding）

最常见原因：**UTF-8 文件被错误编码打开并保存**。

- 正确流程：编辑器用 UTF-8 读写 → 文件保持 UTF-8
- 出错流程：UTF-8 文件被用 GBK/CP936/系统默认 等打开 → 中文显示为乱码 → 保存时按当前编码写入 → 产生双重编码

示例：
- 原始：`实时` (UTF-8: E5 AE 9E E6 97 B6)
- 被当 Latin-1 解析：é®æ¶（乱码）
- 再按 UTF-8 保存：得到 `瀹炴椂` 等 mojibake

### 2. 常见触发场景

| 场景 | 说明 |
|------|------|
| 多编辑器混用 | 不同编辑器默认编码不同（VS Code UTF-8、某些老工具 GBK） |
| AI 工具编辑 | Codex 等经 PowerShell 终端写回文件时，控制台编码≠UTF-8，会放大乱码并引入引号断裂（见 §2.1） |
| Git 操作 | 误用 `core.autocrlf`、`merge` 时编码冲突 |
| 终端/剪贴板 | 从终端复制到编辑器时编码转换错误 |
| 批量脚本 | 脚本未指定 `encoding='utf-8'` 导致用系统默认编码 |

### 2.1 已确认根因：Codex + PowerShell 终端链路（2025-03）

**经过排查，本项目乱码与语法损坏的主要放大源已确认：**

- **工具**：Codex 插件在编辑大文件（如 `live_analyzer.py`、`gui_app.py`）时
- **链路**：使用了**不安全的文本替换/终端输出链路**
- **环境**：Windows 下 PowerShell 控制台编码与文件 UTF-8 不一致（控制台多用 GBK/系统代码页）
- **后果**：
  - 已有乱码被进一步放大
  - 引号 `"`、`)` 被破坏，导致 f-string/docstring 未闭合
  - 出现语法级损坏（如 `unterminated string literal`）
  - 私有区字符（U+E000–U+F8FF）被注入

**教训**：AI 工具若经终端管道/重定向写回文件，必须保证全链路 UTF-8；否则应改用编辑器/API 直接写入。

### 3. 乱码如何导致语法错误

- 引号 `"` 被替换成类似字符（如 U+201C），Python 不识别
- 行尾的 `")` 被破坏，导致 f-string/docstring 未闭合
- 某些 Unicode（如 U+FF0C、U+3002）在部分环境被误判，触发 "invalid character"

## 二、预防措施

### 1. 编辑器与工具

- 统一使用 UTF-8：VS Code / Cursor 设置为 "files.encoding": "utf8"
- 文件头：在 Python 文件加 `# -*- coding: utf-8 -*-`
- 避免用不支持 UTF-8 的老工具编辑

### 2. Git

```ini
[core]
    autocrlf = false
    quotepath = false
```

### 3. Python 读写

```python
with open(path, "r", encoding="utf-8") as f:
    content = f.read()
with open(path, "w", encoding="utf-8") as f:
    f.write(content)
```

### 4. 定期检查与修复

- 运行 `python -m py_compile src/*.py` 检查语法
- 使用 `fix_mojibake_*.py` 修复已知乱码
- Codex 等 AI 在编辑大文件时可能引入编码错误，编辑后建议验证

## 三、本项目修复脚本

| 脚本 | 用途 |
|------|------|
| `fix_mojibake_live_analyzer.py` | 修复 live_analyzer.py 中的 mojibake |
| `fix_mojibake_gui_app.py` | 修复 gui_app.py 中的 mojibake |

运行顺序建议：先 live_analyzer，再 gui_app，最后验证 `py_compile`。

### 已修复（本次会话）

- **live_analyzer.py**：1664–1673 行 f-string 未闭合及乱码、2026/2033 SQL 引号、2801 流局、以及相关 mojibake
- **gui_app.py**：309/323 docstring、556–739 进度文案、792/804/805/825/836/856 docstring、747 样本展示、878 保存按钮等

### 待修复（gui_app 仍有少量）

- 892 行附近：含 U+E632 等私有区字符的 QMessageBox 文案
- 全角标点替换可能影响部分语义，建议后续人工校对
