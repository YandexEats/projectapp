# Сохраняет секреты Telegram в GitHub Actions (значения никуда не выводятся и не сохраняются на диске).
# Запуск (из папки проекта):  powershell -ExecutionPolicy Bypass -File .\set_secrets.ps1
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) { $gh = 'C:\Program Files\GitHub CLI\gh.exe' }
if (-not (Test-Path $gh)) { Write-Host 'Не найден GitHub CLI (gh).' -ForegroundColor Red; exit 1 }

$repo = 'YandexEats/tezkor-referral-monitor'
Write-Host "Репозиторий: $repo" -ForegroundColor Cyan

function Read-Secret([string]$prompt) {
    $sec = Read-Host -Prompt $prompt -AsSecureString
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

$token = (Read-Secret 'Вставьте BOT_TOKEN (символы не видны) и нажмите Enter').Trim()
if ($token -notmatch '^\d+:[A-Za-z0-9_-]{30,}$') { Write-Host 'Похоже, это не токен бота (формат 123456:ABC...). Отмена.' -ForegroundColor Red; exit 1 }

$group = (Read-Host -Prompt 'Введите GROUP_CHAT_ID (число, обычно начинается с -100)').Trim()
if ($group -notmatch '^-?\d+$') { Write-Host 'GROUP_CHAT_ID должен быть числом. Отмена.' -ForegroundColor Red; exit 1 }

$admin = (Read-Host -Prompt 'ADMIN_CHAT_ID — куда слать служебные сообщения (Enter = та же группа)').Trim()
if ([string]::IsNullOrEmpty($admin)) { $admin = $group }
if ($admin -notmatch '^-?\d+$') { Write-Host 'ADMIN_CHAT_ID должен быть числом. Отмена.' -ForegroundColor Red; exit 1 }

# Значения не печатаются и не попадают в историю команд PowerShell
& $gh secret set BOT_TOKEN --repo $repo --body $token
if ($LASTEXITCODE -ne 0) { Write-Host 'Ошибка сохранения BOT_TOKEN' -ForegroundColor Red; exit 1 }
& $gh secret set GROUP_CHAT_ID --repo $repo --body $group
if ($LASTEXITCODE -ne 0) { Write-Host 'Ошибка сохранения GROUP_CHAT_ID' -ForegroundColor Red; exit 1 }
& $gh secret set ADMIN_CHAT_ID --repo $repo --body $admin
if ($LASTEXITCODE -ne 0) { Write-Host 'Ошибка сохранения ADMIN_CHAT_ID' -ForegroundColor Red; exit 1 }
$token = $null

Write-Host ''
& $gh secret list --repo $repo
Write-Host ''
Write-Host 'Готово: секреты сохранены в GitHub. Напишите Claude: «секреты заданы».' -ForegroundColor Green
