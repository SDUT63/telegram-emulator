@echo off
rem  Проверка перед запуском: всё ли на месте.
rem  Файл в UTF-8 без BOM: chcp 65001 ниже переключает консоль на UTF-8,
rem  иначе русские буквы в окне превращаются в вопросительные знаки.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY goto nopython

%PY% launcher.py check
goto done

:nopython
echo.
echo   На этом компьютере не найден Python.
echo.
echo   Откройте python.org/downloads, скачайте и установите его.
echo   В первом окне установщика обязательно отметьте галочку
echo   "Add python.exe to PATH" — без неё Windows его не увидит.
echo.
echo   Подробно — в файле ЗАПУСК_БОТА.md рядом с этим файлом.
echo.

:done
echo.
pause
