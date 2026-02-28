Set WshShell = CreateObject("WScript.Shell")

' Получаем папку где лежит этот скрипт
Dim scriptDir
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))

' Запускаем start.bat без окна
WshShell.Run "cmd /c """ & scriptDir & "start.bat""", 0, False
