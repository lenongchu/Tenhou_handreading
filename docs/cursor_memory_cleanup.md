# Cursor 点击 Agent 后内存飙升与卡顿 — 清理指南

## 问题概况

在点击任意 Agent 后，cursor.exe 的 Private Bytes 急剧上升、界面卡顿。即使已将大型数据库（如 240GB 的 tenhou.db）移出项目目录，问题仍可能存在，原因包括：

1. **索引残留 (Indexing ghosting)**：Cursor 的检索/索引曾在项目内记录过大量文件元数据，Agent 启动时仍尝试加载这些索引或监听事件。
2. **工作区状态污染 (Workspace state pollution)**：`state.vscdb` 中保存了编辑器/Composer 状态，可能包含对大文件的引用或大量会话数据，导致内存预分配过高。
3. **会话上下文权重 (Conversation context)**：当前或历史的 Composer/Agent 会话关联了大型上下文，Agent 初始化时加载导致内存激增。

以下步骤按**风险从低到高**排列，建议依次尝试。

---

## 前置确认

- 本项目已在 `.cursorignore` 中排除 `data/`、`*.db` 等，避免再次索引大文件。
- 若 `data/` 下仍有大库或大文件，请确保它们已移出或继续被 `.cursorignore` 覆盖。
- **所有清理操作前请完全退出 Cursor**（包括托盘图标），否则可能锁库或写入损坏。

---

## 步骤 1：删除本工作区的 workspaceStorage（推荐首选）

只清理**当前项目**对应的本地状态与缓存，不影响其他项目。会清除**本项目的聊天/Composer 历史**，但可解决工作区状态污染和该工作区相关的索引缓存。

1. **完全退出 Cursor**（文件 → 退出，并确认任务管理器/托盘无 cursor 进程）。
2. 打开资源管理器，进入：
   ```
   %APPDATA%\Cursor\User\workspaceStorage
   ```
   即：`C:\Users\<你的用户名>\AppData\Roaming\Cursor\User\workspaceStorage`。
3. 在每个子文件夹（UUID 命名）中查看 `workspace.json`，找到内容中包含你项目路径的那一个，例如：
   ```json
   {"folder":"file:///e:/Cursor/Tenhou_handreading"}
   ```
4. **备份**该 UUID 文件夹（复制整份到别处，以备需要恢复）。
5. **删除**该 UUID 文件夹（整份删除，而不是只删其中某个文件）。
6. 重新启动 Cursor 并打开同一项目。Cursor 会为该工作区重新生成干净的状态。

**效果**：移除本工作区下的 `state.vscdb`、索引缓存等，通常能明显减轻点击 Agent 后的内存与卡顿；聊天历史仅限本工作区丢失。

---

## 步骤 2：删除全局 state.vscdb.backup

备份文件会参与内存映射，删除可减少无效占用。

1. 完全退出 Cursor。
2. 进入：
   ```
   %APPDATA%\Cursor\User\globalStorage
   ```
3. 若存在 `state.vscdb.backup`，直接**删除**（此为备份，删除后 Cursor 会再按需生成）。
4. 重新启动 Cursor。

---

## 步骤 3：压缩全局 state.vscdb（VACUUM）

若全局状态库过大或碎片多，可尝试压缩以回收空间并减轻加载压力。

1. 完全退出 Cursor。
2. 使用 [DB Browser for SQLite](https://sqlitebrowser.org/) 打开：
   ```
   %APPDATA%\Cursor\User\globalStorage\state.vscdb
   ```
3. 执行：**Execute SQL** → 输入 `VACUUM;` → 执行。
4. 保存并关闭，再启动 Cursor。

若 VACUUM 后文件大小几乎不变，说明占用主要来自真实数据（如大量 `cursorDiskKV`），可考虑步骤 4；若明显变小，通常已能缓解一部分内存问题。

---

## 步骤 4：排查全局 state.vscdb 体积（可选）

若步骤 3 后全局 `state.vscdb` 仍然巨大（例如数 GB 以上），可先诊断再决定是否做更激进清理。

1. 完全退出 Cursor。
2. 用 DB Browser for SQLite 打开 `%APPDATA%\Cursor\User\globalStorage\state.vscdb`。
3. 若支持 `dbstat`，可执行（需先启用）：
   ```sql
   SELECT name, SUM(pgsize) AS size_bytes
   FROM dbstat
   GROUP BY name
   ORDER BY size_bytes DESC
   LIMIT 10;
   ```
   查看哪些表/索引占用最大。社区反馈中常见 `cursorDiskKV` 及与 `agentKv`、`bubbleId`、`checkpointId` 等相关的键。
4. **谨慎操作**：直接删除或重命名全局 `state.vscdb` 可能导致所有项目出现「Loading Chat…」且历史无法正常恢复（见 [Cursor 论坛相关讨论](https://forum.cursor.com/t/deleting-global-state-vscdb-causes-infinite-loading-chat-in-projects-history-not-recoverable-without-corrupted-backup/153220)）。仅在可接受丢失全局聊天索引、且已备份该文件时再考虑。

---

## 步骤 5：降低索引与检索压力

- 在 Cursor 设置中确认 **Indexing** 已排除 `.db`、`data/` 等（与 `.cursorignore` 一致）。
- 尽量不在同一工作区下打开含有巨大目录的其他文件夹（如 77GB 的 repos），否则检索/监听可能产生大量事件并导致 `retrieval-always-local` 进程内存飙升。
- 若曾把 tenhou.db 或其它大库放在项目内，完成上述清理后，用**新开的 Composer/Agent 会话**进行操作，避免沿用可能引用旧上下文的会话。

---

## 脚本辅助（仅清理本工作区）

项目在 `scripts/cursor_workspace_cleanup.ps1` 中提供了 PowerShell 脚本，用于：

- 根据项目路径自动找到对应的 `workspaceStorage` 子文件夹；
- 在用户确认后备份并删除该文件夹。

使用前请**先完全退出 Cursor**，再在 PowerShell 中执行，并按提示确认。详见脚本内注释。

---

## 小结

| 步骤 | 操作 | 风险 | 主要作用 |
|------|------|------|----------|
| 1 | 删除本项目的 workspaceStorage 文件夹 | 仅丢失本项目聊天历史 | 清除工作区状态污染与索引缓存，推荐首选 |
| 2 | 删除 globalStorage 下 state.vscdb.backup | 低 | 减少备份占用的内存映射 |
| 3 | 对 globalStorage/state.vscdb 执行 VACUUM | 低 | 压缩碎片、回收空间 |
| 4 | 诊断/清理全局 state.vscdb 内容或文件 | 高（可能影响所有聊天） | 仅在大文件且确认可接受时使用 |
| 5 | 索引排除 + 新会话 | 无 | 避免再次引入大文件与旧上下文 |

建议优先执行**步骤 1 + 2**，必要时加上**步骤 3**；若仍卡顿，再结合步骤 5 并观察是否与检索/索引相关（如 `retrieval-always-local` 进程内存激增）。
