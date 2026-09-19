param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("human", "companion")]
    [string]$Actor
)

$name = if ($Actor -eq "human") {
    "SUPERPOSITION_TOKEN_HUMAN"
} else {
    "SUPERPOSITION_TOKEN_COMPANION"
}

$token = [Environment]::GetEnvironmentVariable($name, "User")
if ([string]::IsNullOrWhiteSpace($token)) {
    Write-Error "没有找到 $name。请先配置许愿星身份凭证。"
    exit 1
}

Set-Clipboard -Value $token
Write-Host "已把 $Actor 的许愿星登录钥匙复制到剪贴板；终端不会显示钥匙内容。"
