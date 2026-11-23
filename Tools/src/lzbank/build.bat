@echo off
rem Build Tools\lzbank.exe on Windows, with whichever compiler is on PATH:
rem   Visual Studio (run from a "x64 Native Tools Command Prompt"), MinGW-w64 gcc, or clang.
rem Then check it:  python\bin\python Sources\LZBank\tests\test_lzbank.py
setlocal
cd /d "%~dp0"

where cl >nul 2>nul
if %errorlevel%==0 (
    cl /nologo /O2 /W3 /D_CRT_SECURE_NO_WARNINGS lzbank.c /Fe:..\..\lzbank.exe
    del lzbank.obj 2>nul
    goto done
)
where gcc >nul 2>nul
if %errorlevel%==0 (
    gcc -O2 -std=c99 -Wall -s -static -o ..\..\lzbank.exe lzbank.c
    goto done
)
where clang >nul 2>nul
if %errorlevel%==0 (
    clang -O2 -std=c99 -Wall -D_CRT_SECURE_NO_WARNINGS -o ..\..\lzbank.exe lzbank.c
    goto done
)
echo No C compiler found: install Visual Studio Build Tools, MinGW-w64 or LLVM.
exit /b 1

:done
if exist ..\..\lzbank.exe echo built Tools\lzbank.exe
