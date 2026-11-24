# Stop starting the focus tracker at sign-in. Your data folder is left untouched.
Stop-ScheduledTask -TaskName "FocusTracker" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "FocusTracker" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "FocusTracker removed from startup."
