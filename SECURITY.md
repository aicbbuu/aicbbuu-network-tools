# 安全策略

## 支持的版本

只支持最新版本。Windows 桌面工具没有长期支持分支的惯例——
有修复就直接发新版本。

| 版本 | 支持 |
|------|------|
| 1.0.x | ✓ |
| < 1.0 | ✗ |

## 报告漏洞

**请不要用公开 issue 报告安全漏洞。**

请通过 GitHub 的 [私密漏洞报告](https://github.com/aicbbuu/aicbbuu-network-tools/security/advisories/new)
提交（如果仓库未启用该功能，请直接联系维护者）。

报告内容请包含：

- 漏洞描述与影响范围
- 复现步骤
- 受影响的版本
- 已验证的内容

**请留出合理的修复时间**（通常 7-14 天）。修复后会在 advisory 中公开
说明。贡献者可选择署名或匿名。

## 这个工具会做什么、不做什么

说清楚边界比事后解释有用：

**它不做：**
- 不上传任何数据到第三方。所有探测都在本机执行，IP 工具是纯位运算
- 不收集遥测、不上报崩溃报告
- 不读写任何与网络诊断无关的文件
- 不含任何第三方遥测 SDK

**它会：**
- 读取本机网络配置（网卡、IP、DNS、代理、ARP 表）
- 调用 Windows 自带命令：`ping` / `tracert` / `nslookup` / `ipconfig` /
  `netstat` / `netsh` / `arp`
- 主动连接用户指定的目标和测速源

WiFi 网络扫描会显示周围可见的 AP 的 SSID / BSSID / 频段 / 信道 /
信号——这些信息由 Windows 的 WLAN 服务提供并在本地显示，不上传。
`netsh` 不提供扫描时间戳和地理位置，本工具不会伪造这两项。

## 关于打包产物的信任

发布的所有 exe 都带 SHA256 校验和（见 Release 页的 `SHA256SUMS.txt`）。
请用它验证下载完整性：

```powershell
Get-FileHash aicbbuu-network-tools.exe -Algorithm SHA256
```

## 依赖

- **PySide6 / Qt 6** — LGPL-3.0，动态链接
- 许可：GPL-3.0-or-later

Qt 相关的安全问题请向 Qt 官方报告。
