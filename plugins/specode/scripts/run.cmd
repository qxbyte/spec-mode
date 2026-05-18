@echo off
rem specode python launcher for native Windows cmd.exe.
rem Probes python3, python, py (in that order) and forwards all arguments.

where python3 >nul 2>&1 && (
  python3 %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1 && (
  python %*
  exit /b %ERRORLEVEL%
)
where py >nul 2>&1 && (
  py -3 %*
  exit /b %ERRORLEVEL%
)
echo specode: cannot find python interpreter ^(tried python3, python, py^) 1>&2
exit /b 127
