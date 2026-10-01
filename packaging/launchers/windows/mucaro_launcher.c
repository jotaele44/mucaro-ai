/*
 * Start Mucaro AI.exe - Windows launcher for the double-click source setup.
 *
 * Starts scripts\one_click_windows.ps1 --ui from the folder that contains this .exe, with
 * CREATE_NO_WINDOW: PowerShell and everything it starts share one console that has no window,
 * so nothing but the browser progress page appears. Returns at once, so every double-click starts
 * a fresh launcher (if Mucaro AI is already running, the progress page goes straight to it).
 * With --smoke-test (automated checks only) it waits and returns the setup's exit code.
 *
 * Built reproducibly by scripts/build_launchers.py (pinned zig) into build/launchers/. Not wired up yet:
 * no .exe is committed, and main's one_click_windows.ps1 has no --ui mode. These sources are the base
 * for the queued Windows double-click launcher.
 */
#define WIN32_LEAN_AND_MEAN
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <wchar.h>

#define BIG 32768

static wchar_t root[BIG];
static wchar_t script[BIG];
static wchar_t powershell[MAX_PATH + 64];
static wchar_t command[BIG * 2];

static void fail(const wchar_t *message) {
    MessageBoxW(NULL, message, L"Mucaro AI", MB_OK | MB_ICONERROR);
}

/* Arguments after the program name in the raw command line (quoted or unquoted program name). */
static const wchar_t *arguments_after_program(const wchar_t *line) {
    if (*line == L'"') {
        line++;
        while (*line && *line != L'"') line++;
        if (*line == L'"') line++;
    } else {
        while (*line && *line != L' ' && *line != L'\t') line++;
    }
    while (*line == L' ' || *line == L'\t') line++;
    return line;
}

int WINAPI WinMain(HINSTANCE instance, HINSTANCE previous, LPSTR ansi_args, int show) {
    (void)instance; (void)previous; (void)ansi_args; (void)show;

    DWORD length = GetModuleFileNameW(NULL, root, BIG);
    if (length == 0 || length >= BIG) {
        fail(L"Mucaro AI could not find its own location.");
        return 2;
    }
    wchar_t *slash = wcsrchr(root, L'\\');
    if (slash == NULL) {
        fail(L"Mucaro AI could not find its own location.");
        return 2;
    }
    *slash = L'\0';

    if (swprintf(script, BIG, L"%ls\\scripts\\one_click_windows.ps1", root) < 0 ||
        GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) {
        fail(L"Keep Start Mucaro AI.exe inside the mucaro-ai folder. It could not find the project files next to it.");
        return 2;
    }

    UINT system_length = GetSystemDirectoryW(powershell, MAX_PATH);
    if (system_length == 0 || system_length >= MAX_PATH ||
        swprintf(powershell + system_length, 64, L"\\WindowsPowerShell\\v1.0\\powershell.exe") < 0) {
        fail(L"Mucaro AI could not find Windows PowerShell.");
        return 2;
    }

    const wchar_t *args = arguments_after_program(GetCommandLineW());
    if (swprintf(command, BIG * 2,
                 L"\"%ls\" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"%ls\" --ui %ls",
                 powershell, script, args) < 0) {
        fail(L"The Mucaro AI command line is too long.");
        return 2;
    }

    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    ZeroMemory(&process, sizeof(process));
    if (!CreateProcessW(powershell, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, root, &startup, &process)) {
        fail(L"Mucaro AI could not start Windows PowerShell.");
        return 1;
    }

    DWORD exit_code = 0;
    if (wcsstr(args, L"--smoke-test") != NULL) {
        WaitForSingleObject(process.hProcess, INFINITE);
        if (!GetExitCodeProcess(process.hProcess, &exit_code)) exit_code = 1;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return (int)exit_code;
}
