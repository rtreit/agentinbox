namespace AgentInboxService;

using System.Diagnostics;

public class DaemonWorker : BackgroundService
{
    private readonly ILogger<DaemonWorker> _logger;
    private readonly ServiceConfig _config;
    private Process? _daemonProcess;

    // Restart tracking
    private readonly List<DateTime> _restartTimes = new();
    private int _consecutiveRestarts;

    public DaemonWorker(ILogger<DaemonWorker> logger)
    {
        _logger = logger;
        _config = ServiceConfig.Load(ServiceConfig.DefaultConfigPath());
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (!_config.Enabled)
        {
            _logger.LogWarning("Service is disabled in config (enabled=false). Idling.");
            await Task.Delay(Timeout.Infinite, stoppingToken);
            return;
        }

        ValidateConfig();

        _logger.LogInformation(
            "AgentInbox service starting. Python={Python}, Script={Script}, WorkDir={Dir}",
            _config.ResolvedPythonPath, _config.ResolvedScriptPath, _config.ResolvedWorkingDirectory);

        while (!stoppingToken.IsCancellationRequested)
        {
            if (!CanRestart())
            {
                _logger.LogError(
                    "Max restarts ({Max}) exceeded within {Window} minutes. Stopping.",
                    _config.MaxRestarts, _config.MaxRestartWindowMinutes);
                return;
            }

            try
            {
                await RunDaemonAsync(stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Daemon process failed");
            }

            if (stoppingToken.IsCancellationRequested)
                break;

            if (!_config.RestartOnCrash)
            {
                _logger.LogWarning("Daemon exited and restartOnCrash=false. Stopping.");
                return;
            }

            _restartTimes.Add(DateTime.UtcNow);
            _consecutiveRestarts++;

            // Exponential backoff: base * 2^(consecutive-1), capped at 5 minutes
            var delay = Math.Min(
                _config.RestartDelaySeconds * (1 << Math.Min(_consecutiveRestarts - 1, 6)),
                300);

            _logger.LogWarning(
                "Daemon exited. Restart {N}/{Max}, backing off {Delay}s...",
                _consecutiveRestarts, _config.MaxRestarts, delay);

            await Task.Delay(TimeSpan.FromSeconds(delay), stoppingToken);
        }
    }

    private async Task RunDaemonAsync(CancellationToken ct)
    {
        var logDir = _config.ResolvedLogDirectory;
        Directory.CreateDirectory(logDir);

        var stdoutPath = Path.Combine(logDir, "service_stdout.log");
        var stderrPath = Path.Combine(logDir, "service_stderr.log");

        var args = _config.ResolvedScriptPath;
        if (!string.IsNullOrWhiteSpace(_config.Arguments))
            args += " " + _config.Arguments;

        var psi = new ProcessStartInfo
        {
            FileName = _config.ResolvedPythonPath,
            Arguments = args,
            WorkingDirectory = _config.ResolvedWorkingDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };

        // Propagate environment from the .env file so the daemon can find
        // STORAGE_CONNECTION_STRING, GROUPME_BOT_ID, etc.
        LoadDotEnv(psi);

        // Merge extra PATH entries so the daemon can find copilot, etc.
        PrependExtraPath(psi);

        // The service runs as SYSTEM — set user-profile env vars so Copilot auth works.
        SetUserProfileVars(psi);

        // Ensure Python output is unbuffered so service logs flush promptly.
        psi.Environment["PYTHONUNBUFFERED"] = "1";

        _logger.LogInformation("Launching: {Exe} {Args}", psi.FileName, psi.Arguments);

        _daemonProcess = Process.Start(psi)
            ?? throw new InvalidOperationException("Failed to start daemon process");

        _logger.LogInformation("Daemon PID={Pid}", _daemonProcess.Id);

        // Reset consecutive restart counter on successful launch
        _ = Task.Run(async () =>
        {
            await Task.Delay(TimeSpan.FromSeconds(30), ct);
            if (!ct.IsCancellationRequested && _daemonProcess is { HasExited: false })
            {
                _consecutiveRestarts = 0;
                _logger.LogInformation("Daemon stable for 30s, reset backoff counter");
            }
        }, ct);

        // Stream stdout/stderr to log files in background tasks
        var stdoutTask = StreamToFileAsync(_daemonProcess.StandardOutput, stdoutPath, ct);
        var stderrTask = StreamToFileAsync(_daemonProcess.StandardError, stderrPath, ct);

        // Wait for the process to exit or cancellation
        try
        {
            await _daemonProcess.WaitForExitAsync(ct);
        }
        catch (OperationCanceledException)
        {
            // Service is stopping — kill the daemon gracefully
            _logger.LogInformation("Service stopping, terminating daemon PID={Pid}", _daemonProcess.Id);
            KillDaemon();
            throw;
        }

        // Let log streams flush
        await Task.WhenAll(stdoutTask, stderrTask).WaitAsync(TimeSpan.FromSeconds(5));

        _logger.LogWarning(
            "Daemon PID={Pid} exited with code {Code}",
            _daemonProcess.Id, _daemonProcess.ExitCode);
    }

    private void KillDaemon()
    {
        try
        {
            if (_daemonProcess is { HasExited: false })
            {
                _daemonProcess.Kill(entireProcessTree: true);
                _daemonProcess.WaitForExit(5000);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "Error killing daemon process");
        }
    }

    private bool CanRestart()
    {
        var cutoff = DateTime.UtcNow.AddMinutes(-_config.MaxRestartWindowMinutes);
        _restartTimes.RemoveAll(t => t < cutoff);
        return _restartTimes.Count < _config.MaxRestarts;
    }

    private void ValidateConfig()
    {
        if (!File.Exists(_config.ResolvedPythonPath))
            _logger.LogWarning("Python not found at {Path}", _config.ResolvedPythonPath);

        // Only validate script path as a file when it's not module mode (e.g. "-m ...")
        if (!_config.ScriptPath.StartsWith("-") && !File.Exists(_config.ResolvedScriptPath))
            _logger.LogWarning("Script not found at {Path}", _config.ResolvedScriptPath);

        if (!Directory.Exists(_config.ResolvedWorkingDirectory))
            _logger.LogWarning("Working directory not found: {Path}", _config.ResolvedWorkingDirectory);
    }

    /// <summary>
    /// Load key=value pairs from .env in the working directory into the
    /// process start info environment.
    /// </summary>
    private void LoadDotEnv(ProcessStartInfo psi)
    {
        var envFile = Path.Combine(_config.ResolvedWorkingDirectory, ".env");
        if (!File.Exists(envFile))
            return;

        foreach (var line in File.ReadLines(envFile))
        {
            var trimmed = line.Trim();
            if (string.IsNullOrEmpty(trimmed) || trimmed.StartsWith('#'))
                continue;

            var eqIdx = trimmed.IndexOf('=');
            if (eqIdx <= 0)
                continue;

            var key = trimmed[..eqIdx].Trim();
            var value = trimmed[(eqIdx + 1)..].Trim();
            psi.Environment[key] = value;
        }

        _logger.LogInformation("Loaded .env from {Path}", envFile);
    }

    /// <summary>
    /// Prepend extraPath directories to the PATH environment variable so the daemon
    /// can find tools that live in the user profile.
    /// </summary>
    private void PrependExtraPath(ProcessStartInfo psi)
    {
        if (string.IsNullOrWhiteSpace(_config.ExtraPath))
            return;

        var current = psi.Environment.TryGetValue("PATH", out var existing) ? existing : "";
        psi.Environment["PATH"] = _config.ExtraPath + ";" + current;
        _logger.LogInformation("Prepended extra PATH entries");
    }

    /// <summary>
    /// Set USERPROFILE, HOMEPATH, APPDATA, LOCALAPPDATA, HOME so that tools like copilot
    /// can find auth tokens and config when running under the SYSTEM account (Session 0).
    /// Derives the user profile from the workingDirectory (e.g. C:\Users\randy\...).
    /// </summary>
    private void SetUserProfileVars(ProcessStartInfo psi)
    {
        var workDir = _config.ResolvedWorkingDirectory;
        var usersDir = Path.DirectorySeparatorChar + "Users" + Path.DirectorySeparatorChar;
        var idx = workDir.IndexOf(usersDir, StringComparison.OrdinalIgnoreCase);
        if (idx < 0)
            return;

        var afterUsers = workDir[(idx + usersDir.Length)..];
        var sep = afterUsers.IndexOf(Path.DirectorySeparatorChar);
        var username = sep >= 0 ? afterUsers[..sep] : afterUsers;
        var profileDir = workDir[..(idx + usersDir.Length + username.Length)];

        if (!Directory.Exists(profileDir))
            return;

        psi.Environment["USERPROFILE"] = profileDir;
        psi.Environment["HOMEPATH"] = @"\Users\" + username;
        psi.Environment["HOMEDRIVE"] = profileDir[..2]; // e.g. "C:"
        psi.Environment["APPDATA"] = Path.Combine(profileDir, "AppData", "Roaming");
        psi.Environment["LOCALAPPDATA"] = Path.Combine(profileDir, "AppData", "Local");
        psi.Environment["HOME"] = profileDir;

        _logger.LogInformation("Set user profile env vars for {User}", username);
    }

    private static async Task StreamToFileAsync(
        StreamReader reader, string path, CancellationToken ct)
    {
        await using var writer = new StreamWriter(path, append: true) { AutoFlush = true };
        await writer.WriteLineAsync(
            $"--- Service log started at {DateTime.UtcNow:O} ---");

        while (!ct.IsCancellationRequested)
        {
            var line = await reader.ReadLineAsync(ct);
            if (line is null)
                break;
            await writer.WriteLineAsync(line);
        }
    }

    public override async Task StopAsync(CancellationToken cancellationToken)
    {
        _logger.LogInformation("AgentInbox service stopping...");
        KillDaemon();
        await base.StopAsync(cancellationToken);
    }

    public override void Dispose()
    {
        _daemonProcess?.Dispose();
        base.Dispose();
        GC.SuppressFinalize(this);
    }
}
