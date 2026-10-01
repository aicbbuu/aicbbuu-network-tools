# 签名 exe —— 供 ci.yml 和 release.yml 共用
#
# 用的是**自签证书**（self-signed），不是购买的受信任证书。
# 因此 Windows 会显示「未知发布者」，但文件确实带数字签名，
# 完整性可校验，SmartScreen / Defender 的误报概率也会降低。
#
# 设计要点：
# 1. **没证书就跳过，绝不让发布失败。** 签名是增强项，不是前置条件。
# 2. **只在打 tag 时签。** 日常 CI 跑出来的中间产物没有签名意义，
#    也省掉每次构建都做一次签名的时间。
# 3. **时间戳失败降级为无时间戳重签**，再失败才警告并放过——
#    宁可交付一个「签了但没时间戳」的 exe，也不要什么都发不出去。
# 4. **verify 报不信任是自签证书的正常现象**，警告而不失败。
# 5. 证书只从 Secrets 读，写进 runner 临时目录，用完立刻删。
#    日志里不出现口令。
#
# 用法：
#   ./tools/sign-exe.ps1 -Target "dist\aicbbuu-network-tools.exe"

param(
    [Parameter(Mandatory = $true)]
    [string]$Target,

    # RFC 3161 时间戳服务。自签证书的签名会随时间失效（证书本身有
    # 有效期），打上时间戳后，只要时间戳时签名工具认可这个证书，
    # 签名就永久有效——所以这一项对自签证书比对购买的证书更重要。
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = 'Stop'

$certB64 = $env:NETDIAG_PFX_BASE64
$certPwd = $env:NETDIAG_PFX_PASSWORD

if ([string]::IsNullOrWhiteSpace($certB64) -or [string]::IsNullOrWhiteSpace($certPwd)) {
    Write-Output "[签名] 未配置 NETDIAG_PFX_BASE64 / NETDIAG_PFX_PASSWORD，跳过。"
    Write-Output "[签名] 未签名 exe 可能被 Defender 误报，属 PyInstaller 已知现象。"
    exit 0
}

# 定位 signtool：优先 PATH，其次 Windows Kits。显式检查后给出清晰
# 提示，而不是让下一步报一个莫名其妙的口令错误。
$signtool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
if (-not $signtool) {
    $kits = @("${env:ProgramFiles(x86)}\Windows Kits\10\bin",
              "$env:ProgramFiles\Windows Kits\10\bin") |
            Where-Object { $_ -and (Test-Path $_) }
    $signtool = Get-ChildItem $kits -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match 'x64\\signtool\.exe$' } |
                Sort-Object FullName -Descending |
                Select-Object -First 1 -ExpandProperty FullName
}
if (-not $signtool) {
    Write-Warning "[签名] 找不到 signtool.exe，跳过"
    exit 0
}

# 写进 runner 临时目录而不是仓库里——避免被后续步骤的 git 操作
# 或依赖缓存捎带上传。文件名带 GUID，避免并发 job 撞车。
$pfx = Join-Path $env:RUNNER_TEMP ("signing-{0}.pfx" -f [guid]::NewGuid())
[IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($certB64))

try {
    Write-Output "[签名] 签名 $Target ..."

    # 优先带时间戳签名
    & $signtool sign /fd SHA256 /f $pfx /p $certPwd /tr $TimestampUrl /td SHA256 $Target
    if ($LASTEXITCODE -ne 0) {
        # 时间戳服务不可达时降级：无时间戳重签一次
        Write-Warning "[签名] 时间戳失败，改为无时间戳签名"
        & $signtool sign /fd SHA256 /f $pfx /p $certPwd $Target
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "[签名] 签名失败，继续发布未签名 exe"
            exit 0
        }
    }

    # 验证签名结构。自签证书在 verify /pa 下必然报 "not trusted"
    # ——这是预期行为，不是错误。
    & $signtool verify /pa $Target *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "[签名] verify 报不信任（自签证书属预期），但签名已写入"
    }

    # 打印签名者，方便在日志里确认签的是哪个主体
    $sig = Get-AuthenticodeSignature $Target
    Write-Output ("[签名] 状态={0}" -f $sig.Status)
    if ($sig.SignerCertificate) {
        Write-Output ("[签名] 签名者={0}" -f $sig.SignerCertificate.Subject)
        Write-Output ("[签名] 有效期至={0}" -f $sig.SignerCertificate.NotAfter)
    }
}
finally {
    # 无论成败都立刻删掉证书副本
    Remove-Item $pfx -Force -ErrorAction SilentlyContinue
}

Write-Output "[签名] 完成"
exit 0
