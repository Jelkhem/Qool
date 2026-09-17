@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ============================================
echo   Qool - اعداد بيئة الاملاء الصوتي المحلي
echo ============================================

where uv >nul 2>nul
if errorlevel 1 (
  if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  ) else (
    echo [0/4] الأداة uv غير موجودة؛ جاري تثبيتها للمستخدم الحالي فقط ^(بدون صلاحيات مسؤول^)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  )
)
where uv >nul 2>nul
if errorlevel 1 (
  echo [خطأ] تعذّر تثبيت uv. ثبّتها يدويًا من: https://docs.astral.sh/uv/
  pause
  exit /b 1
)

rem Python داخل مجلد التطبيق وليس AppData، حتى يعمل الاختصار من أي مكان
set "UV_PYTHON_INSTALL_DIR=%~dp0python"
echo [1/4] تجهيز Python 3.12 معزول ^(لا يغيّر Python الموجود على الجهاز^)...
uv python install 3.12 || goto :fail
if not exist ".venv\Scripts\python.exe" (
  uv venv .venv --python 3.12 || goto :fail
)

echo [2/4] تثبيت المكتبات ^(قد يستغرق عدة دقائق أول مرة؛ مكتبات CUDA حوالي 1.3 GB^)...
uv pip install --python .venv\Scripts\python.exe -r requirements.txt || goto :fail
rem اجعل الحزمة qool متاحة من أي مجلد تشغيل
.venv\Scripts\python.exe -c "import sysconfig, pathlib; pathlib.Path(sysconfig.get_paths()['purelib'], 'qool_project.pth').write_text(r'%~dp0'.rstrip('\\'), encoding='utf-8')" || goto :fail

echo [3/4] تشغيل الاختبارات الآلية...
set "PYTHONPATH=%~dp0"
.venv\Scripts\python.exe -m pytest -q tests || echo [تحذير] بعض الاختبارات فشلت - راجع الناتج أعلاه.

echo [4/4] إنشاء اختصار في قائمة ابدأ وفي مجلد المشروع...
.venv\Scripts\python.exe -c "import os; from pathlib import Path; from qool.startup import create_shortcut; create_shortcut(Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/Qool.lnk', '--show'); create_shortcut(Path(r'%~dp0')/'Qool.lnk', '--show'); print('OK')" || echo [تحذير] تعذر إنشاء الاختصار؛ استخدم Qool.bat

echo.
echo تم الإعداد. شغّل التطبيق بالنقر المزدوج على Qool.lnk أو من قائمة ابدأ ^(Qool^).
echo عند أول تشغيل سيعرض التطبيق تنزيل النموذج مرة واحدة.
pause
exit /b 0

:fail
echo [خطأ] فشل الإعداد. راجع الرسائل أعلاه.
pause
exit /b 1
