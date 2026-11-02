# MovieInsight V3.4 - 32 unique film posters saved locally.
# Requires Windows PowerShell 5.1+; no Python / npm / packages / elevation.
# Save only in the posters folder next to this script (does not change system files).
param([switch]$CheckOnly)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12 } catch { }
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
$PosterDir=Join-Path $Root 'posters'
New-Item -ItemType Directory -Path $PosterDir -Force | Out-Null
$Titles=@(
 @('Interstellar',157336),@('Inception',27205),@('The Godfather',238),@('Blade Runner 2049',335984),
 @('Dune (2021)',438631),@('The Dark Knight',155),@('Fight Club',550),@('The Matrix',603),
 @('The Shawshank Redemption',278),@('Titanic',597),@('Gladiator',98),@('Forrest Gump',13),
 @('Star Wars: The Force Awakens',140607),@('Ant-Man',102899),@('Jurassic World',135397),@('The Martian',286217),
 @('Mad Max: Fury Road',76341),@('Mission Impossible: Rogue Nation',177677),@('Minions',211672),
 @('Terminator Genisys',87101),@('Spectre',206647),@('Mockingjay Part 2',131634),
 @('Southpaw',307081),@('Big Hero 6',177572),@('The Revenant',281957),@('Inside Out',150540),
 @('The Hobbit: The Battle of the Five Armies',122917),@('The Hateful Eight',273248),
 @('Mockingjay Part 1',131631),@('Avengers: Age of Ultron',99861),@('Everest',253412),@('Star Wars (1977)',11)
)
Add-Type -AssemblyName System.Drawing
function IsValidPoster([string]$Path){
  if(!(Test-Path -LiteralPath $Path -PathType Leaf)){return $false}
  if((Get-Item -LiteralPath $Path).Length -lt 9000){return $false}
  $im=$null
  try {
    $im=[System.Drawing.Image]::FromFile($Path)
    return ($im.Width -ge 200 -and $im.Height -ge 290)
  } catch {return $false} finally {if($null -ne $im){$im.Dispose()}}
}
$Existing=0
foreach($i in 1..32){
 $file=Join-Path $PosterDir ('{0:D2}.jpg' -f $i)
 if(IsValidPoster $file){$Existing++}
}
Write-Host ('Local valid posters: {0}/32' -f $Existing) -ForegroundColor Cyan
if($CheckOnly){if($Existing -eq 32){exit 0}else{exit 1}}
if($Existing -eq 32){Write-Host 'All 32 posters are ready. Open index.html.' -ForegroundColor Green;exit 0}
Write-Host 'TMDB needs a Read Access Token ONLY for checking the original movie poster paths.'
Write-Host 'Your token is not saved in a file, command line, or HTML.'
$token=$env:TMDB_READ_ACCESS_TOKEN
if([string]::IsNullOrWhiteSpace($token)){
 $secret=Read-Host 'Paste your TMDB Read Access Token' -AsSecureString
 $bstr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
 try {$token=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)}
 finally {[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)}
}
$token=$token.Trim()
if(!$token){Write-Host 'No token, stopping.' -ForegroundColor Yellow;exit 2}
$headers=@{Authorization='Bearer '+$token;Accept='application/json'}
$good=0
$bad=New-Object System.Collections.Generic.List[string]
$seen=New-Object 'System.Collections.Generic.HashSet[string]'
for($i=0;$i -lt $Titles.Count;$i++){
 $row=$Titles[$i]; $title=[string]$row[0]; $movieId=[int]$row[1]
 $dst=Join-Path $PosterDir ('{0:D2}.jpg' -f ($i+1))
 if(IsValidPoster $dst){Write-Host ('[{0:D2}/32] Existing: {1}' -f ($i+1),$title);$good++;continue}
 $tmp=Join-Path $PosterDir ('{0:D2}.partial' -f ($i+1))
 $ok=$false
 for($attempt=1;$attempt -le 3 -and !$ok;$attempt++){
  try {
    $info=Invoke-RestMethod -Method Get -Uri ('https://api.themoviedb.org/3/movie/{0}?language=en-US' -f $movieId) -Headers $headers -TimeoutSec 25 -UseBasicParsing
    if([int]$info.id -ne $movieId){throw 'TMDB movie ID mismatch'}
    $path=[string]$info.poster_path
    if(!$path.StartsWith('/')){throw 'TMDB poster_path missing'}
    if($seen.Contains($path)){throw 'Duplicate poster path detected'}
    $imageUrl='https://image.tmdb.org/t/p/w500'+$path
    Invoke-WebRequest -Uri $imageUrl -OutFile $tmp -TimeoutSec 45 -UseBasicParsing
    if(!(IsValidPoster $tmp)){throw 'Downloaded file is not a valid poster image'}
    # Convert all formats into real JPG bytes for predictable offline browser loading.
    $im=[System.Drawing.Image]::FromFile($tmp)
    $jpgTmp=Join-Path $PosterDir ('{0:D2}.jpg.new' -f ($i+1))
    try{$im.Save($jpgTmp,[System.Drawing.Imaging.ImageFormat]::Jpeg)}finally{$im.Dispose()}
    if(!(IsValidPoster $jpgTmp)){throw 'Converted JPEG failed validation'}
    Move-Item -LiteralPath $jpgTmp -Destination $dst -Force
    $seen.Add($path) | Out-Null
    $ok=$true; $good++
    Write-Host ('[{0:D2}/32] OK: {1}' -f ($i+1),$title) -ForegroundColor Green
  } catch {
    $err=$_.Exception.Message
    if($attempt -eq 3){Write-Host ('[{0:D2}/32] Failed: {1}: {2}' -f ($i+1),$title,$err) -ForegroundColor Red;$bad.Add(('{0:D2} - {1}: {2}' -f ($i+1),$title,$err))}
    else{Start-Sleep -Milliseconds (800*$attempt)}
  } finally {if(Test-Path -LiteralPath $tmp){Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue}; if(Test-Path -LiteralPath (Join-Path $PosterDir ('{0:D2}.jpg.new' -f ($i+1)))){Remove-Item -LiteralPath (Join-Path $PosterDir ('{0:D2}.jpg.new' -f ($i+1))) -Force -ErrorAction SilentlyContinue}}
 }
 Start-Sleep -Milliseconds 250
}
$report=Join-Path $Root 'poster-download-result.txt'
$reportText=@('MovieInsight poster download result',('Downloaded/available: {0}/32' -f $good),'Failure entries:')+$bad.ToArray()
[IO.File]::WriteAllLines($report,$reportText,[Text.Encoding]::UTF8)
Write-Host ('Finished: {0}/32. Folder: {1}' -f $good,$PosterDir) -ForegroundColor Cyan
if($bad.Count -gt 0){Write-Host 'Failures are listed in poster-download-result.txt. Rerun to retry missing posters.' -ForegroundColor Yellow;exit 1}
Write-Host 'All posters saved locally. You can now open index.html OFFLINE.' -ForegroundColor Green
exit 0
