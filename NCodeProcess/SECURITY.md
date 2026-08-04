# NCodeProcess 发布安全说明

## 已启用的加固措施

- 使用 PyInstaller 单文件模式发布，不在便携包中携带 Python 源码或普通 `.pyc` 文件。
- 每次构建生成新的 16 字符随机密钥，通过 PyInstaller 5.13.2 和 `tinyaes` 对 PYZ 中的 Python 字节码进行 AES-CTR 加密。
- 使用 Python `-OO` 优化模式构建，移除断言和文档字符串等非运行期信息。
- 禁止窗口模式下输出完整 Python 回溯，发布包不包含测试、调试器和开发工具模块。
- 冻结程序启动后检测本地或远程调试器，禁用 Python trace/profile 接口，并持续检测运行期间的调试器附加。
- 临时密钥只存在于构建进程环境变量中；构建完成后自动删除密钥和临时构建目录。
- 便携包自动生成 `SHA256SUMS.txt`，用于核对 EXE 是否在复制或传输过程中发生变化。
- 构建脚本支持 Authenticode SHA-256 数字签名。配置受信任的代码签名证书后，Windows 可验证发布者身份和文件完整性。

## 构建

```powershell
conda run -n python38 python -m pip install -r requirements-build.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_portable.ps1 -CondaEnvironment python38
```

如已有代码签名证书，可使用当前用户证书库中的证书指纹签名：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_portable.ps1 `
  -CondaEnvironment python38 `
  -SigningCertificateThumbprint "证书指纹" `
  -TimestampServer "http://timestamp.digicert.com"
```

未提供证书时仍会完成加密与反调试构建，但 EXE 的 Windows 发布者将显示为未知。不要把 PFX 密码或构建密钥写入源码、脚本或发布包。

## 完整性验证

```powershell
Get-FileHash .\NCodeProcess.exe -Algorithm SHA256
Get-AuthenticodeSignature .\NCodeProcess.exe
```

第一条命令的结果应与 `SHA256SUMS.txt` 一致；已签名版本的第二条命令状态应为 `Valid`。

## 安全边界

任何在用户计算机上运行的本地软件都无法绝对阻止逆向、内存转储或二进制补丁。本项目的措施用于显著增加自动反编译和常规拆包的成本。涉及商业机密或授权控制时，仍应配合正式代码签名、最小范围分发、访问控制和版本追踪；不能把客户端中的静态密钥视为不可提取的长期秘密。
