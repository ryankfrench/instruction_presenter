# Instruction Presenter

## Chrome autoplay

The subject video page clicks **Ready to play video** on load. Chrome still blocks playback with sound unless it is started with `--autoplay-policy=no-user-gesture-required`.

Use a separate profile so the flag applies when Chrome is already running. From PowerShell:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --autoplay-policy=no-user-gesture-required --user-data-dir="$env:TEMP\chrome-autoplay" "http://127.0.0.1:8000/instructions/<session-id>/subject/<player-key>/"
```

Replace the URL with the subject page you want to open. Close that Chrome window when you are done; the temporary profile is under `%TEMP%\chrome-autoplay`.
