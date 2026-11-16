param(
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'EkonexAndroidSdk'),
    [string]$JdkRoot = $env:JAVA_HOME,
    [string]$KeystorePath = '',
    [ValidateSet('Production', 'Laboratory')]
    [string]$Environment = 'Production',
    [string]$OutputName = ''
)
$ErrorActionPreference = 'Stop'
$apiBase = switch ($Environment) {
    'Production' { 'https://voice.e-control.tech/api/wear/v1' }
    'Laboratory' { 'https://voice-lab.e-control.tech/api/wear/v1' }
    default { throw "Ambiente Wear non consentito: $Environment" }
}
if (-not $JdkRoot) {
    $JdkRoot = (Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Microsoft') -Directory -Filter 'jdk-17*' |
        Sort-Object Name -Descending | Select-Object -First 1).FullName
}
if (-not $JdkRoot) { throw 'JDK 17 non trovato.' }
$project=$PSScriptRoot; $build=Join-Path $project 'build'; $classes=Join-Path $build 'classes'; $dex=Join-Path $build 'dex'; $compiled=Join-Path $build 'compiled-resources'; $generated=Join-Path $build 'generated\it\ekonex\voice\watch'
$tools=Join-Path $SdkRoot 'build-tools\35.0.0'; $androidJar=Join-Path $SdkRoot 'platforms\android-35\android.jar'
$aapt2=Join-Path $tools 'aapt2.exe'; $d8=Join-Path $tools 'd8.bat'; $zipalign=Join-Path $tools 'zipalign.exe'; $apksigner=Join-Path $tools 'apksigner.bat'
foreach($required in @($androidJar,$aapt2,$d8,$zipalign,$apksigner)){if(-not(Test-Path $required)){throw "Strumento Android mancante: $required"}}
$env:JAVA_HOME=$JdkRoot; $env:PATH=(Join-Path $JdkRoot 'bin')+';'+$env:PATH
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $classes,$dex,$compiled,$generated
New-Item -ItemType Directory -Force -Path $classes,$dex,$compiled,$generated | Out-Null
$unsigned=Join-Path $build 'unsigned.apk'; $aligned=Join-Path $build 'aligned.apk'
if (-not $OutputName) { $OutputName = "ekonex-wear-0.1.0-$($Environment.ToLowerInvariant()).apk" }
if ([IO.Path]::GetFileName($OutputName) -ne $OutputName -or [IO.Path]::GetExtension($OutputName) -ne '.apk') { throw 'OutputName deve essere un nome file .apk senza percorso.' }
$output=Join-Path $build $OutputName; $keystore=if($KeystorePath){$KeystorePath}else{Join-Path $build 'debug.keystore'}
& $aapt2 compile --dir (Join-Path $project 'res') -o $compiled
if($LASTEXITCODE){throw 'Compilazione risorse Android non riuscita.'}
$resources=@(Get-ChildItem $compiled -Filter '*.flat' | ForEach-Object FullName)
& $aapt2 link -o $unsigned --manifest (Join-Path $project 'AndroidManifest.xml') -I $androidJar --min-sdk-version 30 --target-sdk-version 35 $resources
if($LASTEXITCODE){throw 'aapt2 non riuscito.'}
$sources=@()
Get-ChildItem (Join-Path $project 'src\it\ekonex\voice\watch') -Filter '*.java' | ForEach-Object {
    $source=Join-Path $generated $_.Name
    $sourceText=(Get-Content -Raw $_.FullName)
    [IO.File]::WriteAllText($source,$sourceText.Replace('__EKONEX_WEAR_API_BASE__',$apiBase),(New-Object Text.UTF8Encoding($false)))
    $sources += $source
}
if (-not ($sources | ForEach-Object { Get-Content -Raw $_ } | Select-String -SimpleMatch $apiBase)) { throw 'Placeholder endpoint Wear mancante.' }
& (Join-Path $JdkRoot 'bin\javac.exe') --release 11 -cp $androidJar -d $classes $sources
if($LASTEXITCODE){throw 'javac non riuscito.'}
$classFiles=@(Get-ChildItem $classes -Recurse -Filter '*.class' | ForEach-Object FullName)
& $d8 --min-api 30 --lib $androidJar --output $dex $classFiles
if($LASTEXITCODE){throw 'd8 non riuscito.'}
& (Join-Path $JdkRoot 'bin\jar.exe') uf $unsigned -C $dex classes.dex
& $zipalign -f 4 $unsigned $aligned
if(-not(Test-Path $keystore)){
    & (Join-Path $JdkRoot 'bin\keytool.exe') -genkeypair -keystore $keystore -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 -storepass android -keypass android -dname 'CN=Ekonex Debug,O=Ekonex,C=IT' -noprompt
}
& $apksigner sign --ks $keystore --ks-key-alias androiddebugkey --ks-pass pass:android --key-pass pass:android --out $output $aligned
if($LASTEXITCODE){throw 'Firma APK non riuscita.'}; & $apksigner verify --verbose $output
if($LASTEXITCODE){throw 'Verifica firma non riuscita.'}
Write-Output "Environment=$Environment"
Write-Output "ApiBase=$apiBase"
Write-Output $output
