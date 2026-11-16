# Сохраняет секреты Telegram в GitHub Actions (значения не выводятся на экран и не сохраняются на диске).
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

function Test-Token([string]$v)   { return $v -match '^\d+:[A-Za-z0-9_-]{30,}$' }
function Test-GroupId([string]$v) { return $v -match '^-100\d+$' }
function Test-AdminId([string]$v) { return (-not $v.StartsWith('-100')) -and ($v -match '^\d+$') }

# Для автотеста: только загрузить функции проверки, ничего не спрашивать и не сохранять
if ($env:SET_SECRETS_SELFTEST -eq '1') { return }

# 1. Токен бота (скрытый ввод)
while ($true) {
    $token = (Read-Secret 'Вставьте BOT_TOKEN (символы не видны) и нажмите Enter').Trim()
    if (Test-Token $token) { break }
    Write-Host 'Это не похоже на токен бота (формат 123456789:AA...). Попробуйте ещё раз.' -ForegroundColor Yellow
}

# 2. ID группы: только -100...
while ($true) {
    $group = (Read-Host -Prompt 'GROUP_CHAT_ID — ID ГРУППЫ (начинается с -100)').Trim()
    if (Test-GroupId $group) { break }
    Write-Host 'ID группы должен начинаться с -100 (например -1001234567890). Возьмите строку «ГРУППА» из get_chat_id.py.' -ForegroundColor Yellow
}

# 3. Личный ID администратора: обычное положительное число, не группа
while ($true) {
    $admin = (Read-Host -Prompt 'ADMIN_CHAT_ID — личный ID (обычное число), не ID группы').Trim()
    if ($admin.StartsWith('-100')) {
        Write-Host 'Это ID группы, а нужен ваш ЛИЧНЫЙ ID (строка «ЛИЧНЫЙ» из get_chat_id.py). Попробуйте ещё раз.' -ForegroundColor Yellow
        continue
    }
    if (Test-AdminId $admin) { break }
    Write-Host 'Личный ID — это обычное положительное число без минуса. Попробуйте ещё раз.' -ForegroundColor Yellow
}

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
