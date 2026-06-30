@echo off
setlocal enabledelayedexpansion
cls

echo ===================================================
echo   GitHub Update Tool - SERVICE_CREDIT_TRACKER
echo ===================================================
echo.

cd /d "C:\Users\SSD\Desktop\SERVICE_CREDIT_TRACKER"

if not exist .git (
    echo [ERROR] This directory is not a Git repository.
    pause
    exit /b
)

set "STAGED_ANY="

:: 1. Force check for the database file first
set "DB_FILE=form6_tracker.sqlite3"
if exist "%DB_FILE%" (
    echo [PRIORITY] Found: %DB_FILE%
    set /p "ANS=Stage %DB_FILE% (Y/N): "
    if /i "!ANS!"=="Y" (
        git add "%DB_FILE%"
        set "STAGED_ANY=1"
        echo [STAGED] %DB_FILE%
    ) else (
        echo [SKIPPED] %DB_FILE%
    )
) else (
    echo [WARNING] %DB_FILE% not found in directory.
)

:: 2. Process all other files from git status
echo.
echo --- DETECTED CHANGES (Other Files) ---
git status --short
echo --------------------------------------
echo.

for /f "tokens=1,*" %%A in ('git status --short') do (
    set "STAT=%%A"
    set "FILE=%%B"
    set "FILE=!FILE:"=!"
    
    :: Skip the database file if it showed up in status to avoid double-prompting
    echo !FILE! | findstr /i /v "form6_tracker.sqlite3" >nul
    if !errorlevel! equ 0 (
        echo File: !FILE! [Status: !STAT!]
        set /p "ANS=Stage this file? (Y/N): "
        if /i "!ANS!"=="Y" (
            git add "!FILE!"
            set "STAGED_ANY=1"
            echo [STAGED] !FILE!
        ) else (
            echo [SKIPPED] !FILE!
        )
    )
)

echo.
if not defined STAGED_ANY (
    echo [INFO] No files were selected. Canceling update.
    pause
    exit /b
)

echo ===================================================
echo.
set /p "COMMIT_MSG=Enter commit message: "
if "%COMMIT_MSG%"=="" set "COMMIT_MSG=Update files"

git commit -m "%COMMIT_MSG%"

for /f "tokens=*" %%B in ('git branch --show-current') do set "BRANCH=%%B"
if "%BRANCH%"=="" set "BRANCH=main"
git push origin %BRANCH%

echo.
echo Process complete!
pause