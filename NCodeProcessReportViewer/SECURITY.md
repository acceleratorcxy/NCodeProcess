# NCodeProcessReportViewer 发布安全说明

## 已启用的加固措施

- 使用 PyInstaller 单文件模式发布，不分发 Python 源码或普通 `.pyc` 文件。
- 每次构建使用新的随机密钥，通过 PyInstaller 5.13.2 和 `tinyaes` 对 PYZ 字节码进行 AES-CTR 加密。
- 使用 Python `-OO` 优化模式，并从发布包排除测试、调试器和开发工具模块。
- 禁止窗口模式完整回溯；冻结程序会检测调试器、禁用 trace/profile 接口并持续检查后续附加。
- 临时密钥和构建中间目录在构建完成后自动清理。
- 发布包包含 `SHA256SUMS.txt`，并支持使用受信任代码签名证书进行 Authenticode SHA-256 签名。

## 构建与签名

```powershell
conda run -n python38 python -m pip install -r requirements-build.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_portable.ps1 -CondaEnvironment python38
```

如已有代码签名证书：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_portable.ps1 `
  -CondaEnvironment python38 `
  -SigningCertificateThumbprint "证书指纹" `
  -TimestampServer "http://timestamp.digicert.com"
```

可使用以下命令核对发布文件：

```powershell
Get-FileHash .\NCodeProcessReportViewer.exe -Algorithm SHA256
Get-AuthenticodeSignature .\NCodeProcessReportViewer.exe
```

## 安全边界

本地 Python/原生程序都无法绝对阻止逆向和内存分析。以上措施用于提高自动反编译、普通拆包和调试分析的成本。正式对外分发时仍建议使用受信任的代码签名证书、限制分发范围并保留版本和哈希记录。
