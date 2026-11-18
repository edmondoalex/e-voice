param(
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'EkonexAndroidSdk'),
    [string]$KeystorePath = (Join-Path $env:USERPROFILE '.ekonex\signing\wear-production.keystore'),
    [string]$OutputName = 'ekonex-wear-0.1.16-production.apk'
)
$ErrorActionPreference = 'Stop'
$officialCertificateSha256 = '157257dbaf141e76044e3dc071cba5d23be42fbf052a2d9ceb8ba228ebc7a6e4'
$apksigner = Join-Path $SdkRoot 'build-tools\35.0.0\apksigner.bat'
if (-not (Test-Path $KeystorePath)) { throw "Chiave ufficiale Ekonex non trovata: $KeystorePath" }
if (-not (Test-Path $apksigner)) { throw "apksigner non trovato: $apksigner" }
"sdk.dir=$($SdkRoot.Replace('\','\\'))" | Set-Content -Encoding ascii (Join-Path $PSScriptRoot 'local.properties')
Push-Location $PSScriptRoot
try {
    & .\gradlew.bat assembleRelease --no-daemon
    if ($LASTEXITCODE) { throw 'Build Gradle non riuscito.' }
    $unsigned = Join-Path $PSScriptRoot 'build\outputs\apk\release\wear-os-release-unsigned.apk'
    if (-not (Test-Path $unsigned)) {
        $unsigned = (Get-ChildItem (Join-Path $PSScriptRoot 'build\outputs\apk\release') -Filter '*-unsigned.apk' | Select-Object -First 1).FullName
    }
    if (-not $unsigned) { throw 'APK Gradle non trovato.' }
    $output = Join-Path $PSScriptRoot "build\$OutputName"
    & $apksigner sign --ks $KeystorePath --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android --out $output $unsigned
    if ($LASTEXITCODE) { throw 'Firma APK non riuscita.' }
    & $apksigner verify --verbose --print-certs $output | Tee-Object -Variable verification
    if ($LASTEXITCODE -or ($verification -join "`n") -notmatch $officialCertificateSha256) {
        throw 'Certificato APK non compatibile con le installazioni Ekonex Wear esistenti.'
    }
    Write-Output 'VersionName=0.1.16'
    Write-Output 'VersionCode=17'
    Write-Output "CertificateSha256=$officialCertificateSha256"
    Write-Output $output
} finally { Pop-Location }
