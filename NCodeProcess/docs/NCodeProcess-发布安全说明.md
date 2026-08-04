# NCodeProcess 发布安全说明

> **维护说明**：本文件纳入版本管理，是发布产物的安全加固说明文档。构建脚本 `build_portable.ps1`、PyInstaller 配置 `NCodeProcess.spec`、运行时加固钩子 `security_runtime_hook.py` 或发布产物结构变化后，应同步核对并更新本文件（纳入「所有更改必须完善可能受影响的文档」约定）；项目根目录随发布包分发的 `SECURITY.md` 与本文件保持同步。

## 文档信息

| 项目 | 内容 |
|---|---|
| 文档名称 | NCodeProcess 发布安全说明 |
| 文档版本 | V1.0 |
| 编写日期 | 2026-08-05 |
| 最后核对 | 2026-08-05 |
| 文档状态 | 持续维护（随构建代码变化同步更新） |
| 适用范围 | 主程序发布产物（单文件 EXE / 便携目录 / ZIP 压缩包） |
| 关联文件 | 根目录 `SECURITY.md`（随发布包分发，内容与本文件同步）、`build_portable.ps1`、`NCodeProcess.spec`、`security_runtime_hook.py` |

## 一、加固目标

发布物使用 PyInstaller 单文件模式分发，不提供 Python 源码或可单独运行的字节码文件；构建期对字节码加密、运行时反调试、发布物完整性校验等措施用于提高自动反编译、常规拆包和调试分析的**成本**。任何本地软件都无法绝对阻止逆向与内存分析，本文不承诺绝对防逆向（见第五章「安全边界」）。

## 二、已启用的加固措施

| # | 措施 | 实现位置 | 说明 |
|---|---|---|---|
| 1 | 单文件发布，不分发 Python 源码或普通 `.pyc` | `NCodeProcess.spec`（EXE 单文件模式） | 源码打包为单一 EXE；便携包仅含 EXE 与说明/校验文件 |
| 2 | 随机密钥 AES-CTR 加密 PYZ 字节码 | `build_portable.ps1` + `NCodeProcess.spec` | 每次构建用 `RandomNumberGenerator` 生成 16 字节随机密钥（取前 16 个十六进制字符），经 PyInstaller 5.13.2 `PyiBlockCipher`（tinyaes 1.1.2）对 PYZ 字节码 AES-CTR 加密；每次构建密钥不同 |
| 3 | 密钥仅存在于构建进程 | `build_portable.ps1` | 密钥写入构建进程环境变量 `NCODEPROCESS_PYI_KEY`，不落盘、不入库、不随发布包分发；构建结束即清除 |
| 4 | `-OO` 优化构建 | `build_portable.ps1`（PyInstaller 参数含 `-OO`） | 移除断言与文档字符串等非运行期信息 |
| 5 | 窗口模式禁止完整回溯 | `NCodeProcess.spec`（`console=False` + `disable_windowed_traceback=True`） | 冻结程序出错时不显示完整 Python 回溯 |
| 6 | 运行时反调试 | `security_runtime_hook.py`（作为 runtime_hook 注入） | 仅冻结构建生效，源码运行与测试不受影响：启动时清理 `PYTHONPATH`/`PYTHONHOME`/`PYTHONINSPECT`/`PYTHONBREAKPOINT` 等辅助变量；检测 `IsDebuggerPresent`/`CheckRemoteDebuggerPresent`，发现调试器即提示并退出（退出码 `0x5A`）；禁用 `sys.settrace`/`sys.setprofile`（含线程级）；后台 watchdog 每 3 秒轮询，检测运行期附加的调试器 |
| 7 | 调试/测试模块裁剪 | `NCodeProcess.spec`（excludes） | 排除 `pdb`、`doctest`、`unittest`、`test`、`idlelib`、`lib2to3` 等调试与开发模块 |
| 8 | 未使用模块裁剪 | `NCodeProcess.spec`（excludes） | 排除 `ssl`、`socket`、`xml`、`multiprocessing`、`decimal`、`bz2`/`lzma` 等程序未使用的模块以减小体积；不影响 NC 文件扫描、统计、校验、目录处理与报告导出 |
| 9 | UPX 压缩 | `NCodeProcess.spec`（`upx=True`） | 对可执行文件进一步压缩，减小发布体积 |
| 10 | 构建环境加固 | `build_portable.ps1` | 构建期设置 `PYTHONDONTWRITEBYTECODE=1`、`PYTHONHASHSEED=random`，`PYTHONUSERBASE` 指向项目内 `.pyuser`，避免污染用户目录 |
| 11 | 临时构建痕迹清理 | `build_portable.ps1`（finally） | 构建结束删除 `.hardened-build` 工作目录并清除全部注入的环境变量 |
| 12 | SHA-256 完整性记录 | `build_portable.ps1` | 便携包生成 `SHA256SUMS.txt`，记录 EXE 的 SHA-256 哈希，用于核对复制或传输过程中文件是否发生变化 |
| 13 | Authenticode SHA-256 签名（可选） | `build_portable.ps1`（`Set-AuthenticodeSignature`） | 从当前用户证书库按证书指纹签名（SHA-256），可配时间戳服务器；签名失败即终止构建；已签名时额外生成 `AUTHENTICODE.txt` 签名状态记录 |

## 三、构建与签名

安装构建依赖并执行打包（在项目目录中）：

```powershell
conda run -n <Python 3.8 环境名> python -m pip install -r requirements-build.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_portable.ps1 -CondaEnvironment <Python 3.8 环境名>
```

如已有代码签名证书，可使用当前用户证书库中的证书指纹签名：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_portable.ps1 `
  -CondaEnvironment <Python 3.8 环境名> `
  -SigningCertificateThumbprint "证书指纹" `
  -TimestampServer "http://timestamp.digicert.com"
```

发布产物位于 `dist`：

- `NCodeProcess.exe`：单文件可执行程序。
- `NCodeProcess-Package\`：便携目录，含 EXE、`VERSION.txt`、`README.md`、`SECURITY.md`、`SHA256SUMS.txt`；签名时另含 `AUTHENTICODE.txt`。
- `NCodeProcess-Windows7-Portable.zip`：便携目录的 ZIP 压缩包。

未提供证书时仍会完成加密与反调试构建，但 EXE 的 Windows 发布者将显示为「未知」。不要把 PFX 密码、构建密钥或证书私钥写入源码、脚本或发布包。

## 四、完整性验证

```powershell
Get-FileHash .\NCodeProcess.exe -Algorithm SHA256
Get-AuthenticodeSignature .\NCodeProcess.exe
```

第一条命令的结果应与 `SHA256SUMS.txt` 一致；已签名版本的第二条命令状态应为 `Valid`。

## 五、安全边界

任何在用户计算机上运行的本地软件都无法绝对阻止逆向、内存转储或二进制补丁。本项目的措施用于显著增加自动反编译和常规拆包的成本，属于威慑性防护而非加密保证。涉及商业机密或授权控制时，仍应配合正式代码签名、最小范围分发、访问控制和版本追踪；不要把客户端中的静态密钥视为不可提取的长期秘密。

## 六、维护约定

1. 构建脚本、PyInstaller 配置或运行时加固钩子变更后，必须同步核对并更新本文件及根目录 `SECURITY.md`。
2. 本文件及 `SECURITY.md` 描述的是对外发布物：命令中的环境名、路径一律使用占位符，不得写入本机环境信息、密钥、证书信息或测试数据。
3. 发布前核对本文件描述的加固措施与当前构建代码一致（见「所有更改必须完善可能受影响的文档」约定）。
