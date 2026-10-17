@echo off
cd /d "%~dp0"
title IMVU Status - login do dono (presente gratis)
echo.
echo Abrindo o assistente de login...
echo.
set "PYEXE="
where python >nul 2>nul
if %errorlevel%==0 set "PYEXE=python"
if defined PYEXE goto pronto
where py >nul 2>nul
if %errorlevel%==0 set "PYEXE=py"
if defined PYEXE goto pronto
echo Python nao encontrado neste PC.
echo Instale em https://www.python.org/downloads/
echo e marque a opcao "Add Python to PATH" na instalacao.
goto fim
:pronto
%PYEXE% -m pip install --quiet --disable-pip-version-check browser_cookie3 >nul 2>nul
%PYEXE% "server\login_local.py"
:fim
echo.
pause
