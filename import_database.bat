@echo off
chcp 65001 >nul
title 豆瓣电影系统 - 导入数据库
setlocal

cd /d "%~dp0"

REM ============ 如果 mysql 不在 PATH 中，请把下面这行改成完整路径 ============
set "MYSQL_EXE=mysql"
REM 例如： set "MYSQL_EXE=F:\Navicat16_MySQL\Database_filing\MySQL\MySQL Server 8.0\bin\mysql.exe"
REM ======================================================================

set "SQLFILE=数据库脚本\flask_douban_comment.sql"

if not exist "%SQLFILE%" (
    echo [错误] 找不到数据库脚本：%SQLFILE%
    echo 请把 flask_douban_comment.sql 放到 数据库脚本 目录下
    pause
    exit /b 1
)

echo ============================================
echo   导入数据库（大文件必须走命令行！）
echo ============================================
echo.
echo 说明：该 SQL 文件约 86MB，超过 MySQL 默认的
echo       max_allowed_packet(64MB)，用客户端粘贴执行会报
echo       "2006 - Server has gone away"。
echo       本脚本走命令行逐条导入，不会触发该限制。
echo.

set /p DBUSER=MySQL 用户名（直接回车=root）:
if "%DBUSER%"=="" set "DBUSER=root"

set /p DBPASS=MySQL 密码:

echo.
echo 正在导入，请勿关闭窗口...
echo.

"%MYSQL_EXE%" -u%DBUSER% -p%DBPASS% --default-character-set=utf8mb4 --max_allowed_packet=1G < "%SQLFILE%"

if errorlevel 1 (
    echo.
    echo [失败] 导入过程出错，请检查：
    echo   1) MySQL 服务是否已启动
    echo   2) 用户名 / 密码是否正确
    echo   3) MYSQL_EXE 路径是否正确
) else (
    echo.
    echo [成功] 数据库导入完成，库名：flask_douban_comment
)

pause
endlocal
