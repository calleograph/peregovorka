# Синтез русской речи (Windows SAPI) для живого сценария общей записи: три реплики двух голосов. Использование: make_speech.ps1 <каталог>
param([Parameter(Mandatory = $true)][string]$Out)
Add-Type -AssemblyName System.Speech
New-Item -ItemType Directory -Force $Out | Out-Null
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$lines = @(
    @("p1", "Microsoft Pavel", "Предлагаю утвердить бюджет проекта на следующий квартал и начать закупки."),
    @("p2", "Microsoft Irina", "Согласна, но нужно уточнить сроки поставки оборудования."),
    @("p3", "Microsoft Pavel", "Решили: бюджет утверждаем, а сроки уточняет Борис до пятницы.")
)
foreach ($l in $lines) {
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $s.SelectVoice($l[1])
    $s.SetOutputToWaveFile((Join-Path $Out ($l[0] + ".wav")), $fmt)
    $s.Speak($l[2])
    $s.Dispose()
}
