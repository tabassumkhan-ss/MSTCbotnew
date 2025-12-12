param(
[string]$Endpoint = "https://mstcbotnew-production.up.railway.app/deposit/submit",
[Int64]$TelegramId = 7955075358,
[Int64]$UserId = 0,
[int]$Amount = 20,
[int]$Iterations = 1,
[int]$IntervalSeconds = 1,
[string]$IdPrefix = "TEST_DEPLOY_",
[switch]$UseUserId,
[string]$LogFile = "deposit_log.csv"
)


function Get-TimestampSeconds {
return [int](Get-Date -UFormat %s)
}


# Ensure log file exists with header
if (-not (Test-Path -Path $LogFile)) {
"timestamp,tx_musd,status,response" | Out-File -FilePath $LogFile -Encoding utf8
}


for ($i = 1; $i -le $Iterations; $i++) {
try {
$ts = Get-TimestampSeconds
# append iteration counter to ensure uniqueness if multiple runs in the same second
$tx_musd = "${IdPrefix}${ts}-$i"


if ($UseUserId) {
if ($UserId -eq 0) {
Write-Warning "-UseUserId specified but -UserId not provided. Skipping iteration $i."
continue
}
$payload = @{ user_id = $UserId; amount = $Amount; tx_musd = $tx_musd }
}
else {
$payload = @{ telegram_id = $TelegramId; amount = $Amount; tx_musd = $tx_musd }
}


$json = $payload | ConvertTo-Json -Depth 3


Write-Host "[$(Get-Date -Format 'u')] Sending: $tx_musd"


$response = Invoke-RestMethod -Uri $Endpoint -Method Post -ContentType 'application/json' -Body $json -ErrorAction Stop


$status = if ($response.ok -ne $null) { $response.ok } else { 'unknown' }
$respText = ($response | ConvertTo-Json -Depth 3) -replace '"', '"'


# Log as CSV line
$line = "$(Get-Date -Format o),$tx_musd,$status,$([System.Uri]::EscapeDataString($respText))"
Add-Content -Path $LogFile -Value $line


Write-Host "Response: $respText"
}
catch {
$err = $_.Exception.Message
$line = "$(Get-Date -Format o),$tx_musd,ERROR,$([System.Uri]::EscapeDataString($err))"
Add-Content -Path $LogFile -Value $line
Write-Host "Error sending $tx_musd : $err" -ForegroundColor Red
}


if ($i -lt $Iterations) {
Start-Sleep -Seconds $IntervalSeconds
}
}


Write-Host "Done. Log saved to $LogFile"