function Test-OK($msg) { Write-Host "OK: $msg" -ForegroundColor Green }
function Test-Err($msg) { Write-Host "ERR: $msg" -ForegroundColor Red }

function Check {
    $a = $true
    $b = $false
    if ($a) {
        Test-OK "A is true"
    } else {
        Test-Err "A is false"
    }
    if ($b) {
        Test-OK "B is true"
    } else {
        Test-Err "B is false"
    }
}

Check
Write-Host "Done"
