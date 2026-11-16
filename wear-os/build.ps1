param(
    [string]$SdkRoot = (Join-Path $env:LOCALAPPDATA 'EkonexAndroidSdk'),
    [string]$JdkRoot = $env:JAVA_HOME
)
$ErrorActionPreference = 'Stop'
if (-not $JdkRoot) {
    $JdkRoot = (Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Programs\Microsoft') -Directory -Filter 'jdk-17*' |
        Sort-Object Name -Descending | Select-Object -First 1).FullName
}
if (-not $JdkRoot) { throw 'JDK 17 non trovato.' }
$project=$PSScriptRoot; $build=Join-Path $project 'build'; $classes=Join-Path $build 'classes'; $dex=Join-Path $build 'dex'
$tools=Join-Path $SdkRoot 'build-tools\35.0.0'; $androidJar=Join-Path $SdkRoot 'platforms\android-35\android.jar'
$aapt2=Join-Path $tools 'aapt2.exe'; $d8=Join-Path $tools 'd8.bat'; $zipalign=Join-Path $tools 'zipalign.exe'; $apksigner=Join-Path $tools 'apksigner.bat'
foreach($required in @($androidJar,$aapt2,$d8,$zipalign,$apksigner)){if(-not(Test-Path $required)){throw "Strumento Android mancante: $required"}}
$env:JAVA_HOME=$JdkRoot; $env:PATH=(Join-Path $JdkRoot 'bin')+';'+$env:PATH
New-Item -ItemType Directory -Force -Path $classes,$dex | Out-Null
$unsigned=Join-Path $build 'unsigned.apk'; $aligned=Join-Path $build 'aligned.apk'; $output=Join-Path $build 'ekonex-wear-change081-debug.apk'; $keystore=Join-Path $build 'debug.keystore'
& $aapt2 link -o $unsigned --manifest (Join-Path $project 'AndroidManifest.xml') -I $androidJar --min-sdk-version 30 --target-sdk-version 35
if($LASTEXITCODE){throw 'aapt2 non riuscito.'}
$source=Join-Path $project 'src\it\ekonex\voice\watch\MainActivity.java'
& (Join-Path $JdkRoot 'bin\javac.exe') --release 11 -cp $androidJar -d $classes $source
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
if($LASTEXITCODE){throw 'Verifica firma non riuscita.'}; Write-Output $output
