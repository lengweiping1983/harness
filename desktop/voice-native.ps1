param([ValidateSet('target','paste')][string]$Action, [string]$Target)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class HarnessVoiceTarget {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int key);
}
'@
$current = [HarnessVoiceTarget]::GetForegroundWindow().ToInt64().ToString()
if ($Action -eq 'target') { [Console]::Write($current); exit 0 }
if ($Target -notmatch '^[1-9][0-9]{0,19}$') { exit 2 }
if ($current -ne $Target) { [Console]::Write('copied'); exit 0 }
foreach ($key in @(16,17,18,91,92)) {
  if (([HarnessVoiceTarget]::GetAsyncKeyState($key) -band 0x8000) -ne 0) { [Console]::Write('copied'); exit 0 }
}
Add-Type -AssemblyName System.Windows.Forms
if ([HarnessVoiceTarget]::GetForegroundWindow().ToInt64().ToString() -ne $Target) { [Console]::Write('copied'); exit 0 }
[System.Windows.Forms.SendKeys]::SendWait('^v')
[Console]::Write('sent')
