# Задаёт или меняет пароль дашборда (секрет DASHBOARD_PASSWORD) и перешифровывает данные.
# Запуск (из папки проекта):  powershell -ExecutionPolicy Bypass -File .\set_dashboard_password.ps1
#
# Что делает:
#  1. Если пароль уже задан — спрашивает ТЕКУЩИЙ пароль (им робот расшифрует старые данные).
#  2. Дважды спрашивает НОВЫЙ пароль (ввод скрыт), проверяет совпадение и длину.
#  3. Сохраняет его в GitHub через `gh secret set` (значение идёт через stdin, не в командной строке).
#  4. Запускает workflow в режиме reencrypt и ждёт результата.
#  5. После успеха удаляет временный секрет DASHBOARD_PASSWORD_OLD.
# Пароль нигде на диске не сохраняется и на экран не выводится.
$ErrorActionPreference = 'Continue'   # ошибки gh проверяются по $LASTEXITCODE
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = New-Object System.Text.UTF8Encoding $false   # stdin для gh: UTF-8 без BOM

$repo = 'YandexEats/tezkor-referral-monitor'
$minLength = 10

function Read-Secret([string]$prompt) {
    $sec = Read-Host -Prompt $prompt -AsSecureString
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

# Пустая строка = пароль подходит, иначе текст проблемы
function Get-PasswordProblem([string]$v) {
    if ($v.Length -lt $minLength) { return "Пароль слишком короткий: нужно не меньше $minLength символов." }
    if ($v -ne $v.Trim()) { return 'Пароль не должен начинаться или заканчиваться пробелом.' }
    return ''
}
function Test-Password([string]$v) { return (Get-PasswordProblem $v) -eq '' }

# Для автотеста: только загрузить функции проверки, ничего не спрашивать и не сохранять
if ($env:SET_SECRETS_SELFTEST -eq '1') { return }

$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) { $gh = 'C:\Program Files\GitHub CLI\gh.exe' }
if (-not (Test-Path $gh)) { Write-Host 'Не найден GitHub CLI (gh). Установите: winget install GitHub.cli' -ForegroundColor Red; exit 1 }
$null = & $gh auth status 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host 'GitHub CLI не авторизован. Выполните: gh auth login --web --git-protocol https' -ForegroundColor Red; exit 1 }

Write-Host "Репозиторий: $repo" -ForegroundColor Cyan

# 1. Текущий пароль (если уже задан)
$secretNames = & $gh secret list --repo $repo | ForEach-Object { ($_ -split "\s+")[0] }
$old = $null
$hadOld = $secretNames -contains 'DASHBOARD_PASSWORD'
if ($hadOld) {
    Write-Host 'Пароль дашборда уже задан. Чтобы перешифровать старые данные, нужен ТЕКУЩИЙ пароль.'
    $old = Read-Secret 'Текущий пароль (символы не видны)'
    if (-not $old) { Write-Host 'Текущий пароль не введён — отмена.' -ForegroundColor Yellow; exit 1 }
}

# 2. Новый пароль дважды
while ($true) {
    $new1 = Read-Secret 'Новый пароль (символы не видны)'
    $problem = Get-PasswordProblem $new1
    if ($problem) { Write-Host $problem -ForegroundColor Yellow; continue }
    $new2 = Read-Secret 'Повторите новый пароль'
    if ($new1 -ceq $new2) { break }
    Write-Host 'Пароли не совпадают. Попробуйте ещё раз.' -ForegroundColor Yellow
}
$new2 = $null

# 3. Сохранить секреты (значение через stdin)
if ($old) {
    $old | & $gh secret set DASHBOARD_PASSWORD_OLD --repo $repo
    if ($LASTEXITCODE -ne 0) { Write-Host 'Ошибка сохранения DASHBOARD_PASSWORD_OLD' -ForegroundColor Red; exit 1 }
}
$new1 | & $gh secret set DASHBOARD_PASSWORD --repo $repo
if ($LASTEXITCODE -ne 0) { Write-Host 'Ошибка сохранения DASHBOARD_PASSWORD' -ForegroundColor Red; exit 1 }
$old = $null; $new1 = $null
Write-Host 'Секрет DASHBOARD_PASSWORD сохранён.' -ForegroundColor Green

# 4. Перешифровка
$started = (Get-Date).ToUniversalTime().AddSeconds(-5)
& $gh workflow run monitor.yml --repo $repo -f mode=reencrypt
if ($LASTEXITCODE -ne 0) { Write-Host 'Не удалось запустить workflow.' -ForegroundColor Red; exit 1 }
Write-Host 'Запущена перешифровка, жду появления запуска…'
$run = $null
for ($i = 0; $i -lt 20 -and -not $run; $i++) {
    Start-Sleep -Seconds 3
    $run = & $gh run list --repo $repo --workflow monitor.yml --event workflow_dispatch --limit 5 --json databaseId,url,createdAt |
        ConvertFrom-Json | Where-Object { [datetime]::Parse($_.createdAt).ToUniversalTime() -ge $started } | Select-Object -First 1
}
if (-not $run) { Write-Host 'Запуск не найден. Проверьте вкладку Actions вручную.' -ForegroundColor Yellow; exit 1 }
Write-Host "Запуск: $($run.url)" -ForegroundColor Cyan
& $gh run watch $run.databaseId --repo $repo --exit-status
if ($LASTEXITCODE -ne 0) {
    Write-Host "Перешифровка НЕ удалась: $($run.url)" -ForegroundColor Red
    Write-Host 'DASHBOARD_PASSWORD_OLD оставлен: ежедневная проверка сама перешифрует данные, когда ошибка будет исправлена.' -ForegroundColor Yellow
    exit 1
}

# 5. Убрать временный старый пароль
if ($hadOld) {
    & $gh secret delete DASHBOARD_PASSWORD_OLD --repo $repo
    if ($LASTEXITCODE -ne 0) { Write-Host 'Не удалось удалить DASHBOARD_PASSWORD_OLD — удалите его вручную в Settings → Secrets.' -ForegroundColor Yellow }
}
Write-Host ''
Write-Host 'Готово: данные зашифрованы новым паролем, дашборд обновлён.' -ForegroundColor Green
Write-Host 'Сохраните пароль в менеджере паролей: восстановить его нельзя.'
