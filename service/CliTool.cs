namespace AgentInboxService;

using System.Diagnostics;
using System.ServiceProcess;

/// <summary>
/// CLI tool for managing the AgentInboxDaemon Windows service.
/// Invoked when the exe is run with a subcommand (install, uninstall, start, stop, status, config).
/// </summary>
public static class CliTool
{
    private const string ServiceName = "AgentInboxDaemon";
    private const string DisplayName = "Agent Inbox Daemon";
    private const string Description = "Auto-starts the Agent Inbox daemon on boot.";

    public static int Run(string[] args)
    {
        if (args.Length == 0)
        {
            PrintUsage();
            return 1;
        }

        return args[0].ToLowerInvariant() switch
        {
            "install" => Install(),
            "uninstall" => Uninstall(),
            "start" => StartService(),
            "stop" => StopService(),
            "status" => ShowStatus(),
            "config" => HandleConfig(args.Skip(1).ToArray()),
            "help" or "--help" or "-h" => PrintUsage(),
            _ => PrintUsage($"Unknown command: {args[0]}"),
        };
    }

    private static int PrintUsage(string? error = null)
    {
        if (error is not null)
            Console.Error.WriteLine($"Error: {error}\n");

        Console.WriteLine("""
            AgentInboxService — Windows Service for the Agent Inbox daemon

            Usage:
              AgentInboxService <command> [options]

            Commands:
              install       Install the Windows service (requires admin)
              uninstall     Remove the Windows service (requires admin)
              start         Start the service
              stop          Stop the service
              status        Show service status and config summary
              config        View or edit configuration

            Config subcommands:
              config show                 Show current configuration
              config set <key> <value>    Set a configuration value
              config path                 Show config file path
              config reset                Reset config to defaults

            Config keys:
              pythonPath, scriptPath, workingDirectory, arguments,
              restartOnCrash, restartDelaySeconds, maxRestarts,
              maxRestartWindowMinutes, logDirectory, enabled, extraPath

            Examples:
              AgentInboxService install
              AgentInboxService config set arguments "--interval 15"
              AgentInboxService config set restartDelaySeconds 10
              AgentInboxService status
            """);

        return error is null ? 0 : 1;
    }

    // ---------------------------------------------------------------
    // Service management
    // ---------------------------------------------------------------

    private static int Install()
    {
        var exePath = Environment.ProcessPath
            ?? throw new InvalidOperationException("Cannot determine executable path");

        // sc.exe create
        var result = RunSc(
            $"create {ServiceName} binPath= \"\\\"{exePath}\\\"\" " +
            $"start= delayed-auto DisplayName= \"{DisplayName}\"");

        if (result != 0) return result;

        // Set description
        RunSc($"description {ServiceName} \"{Description}\"");

        // Configure recovery: restart on first, second, and subsequent failures
        RunSc($"failure {ServiceName} reset= 86400 actions= restart/5000/restart/10000/restart/30000");

        Console.WriteLine();
        Console.WriteLine($"  Service '{ServiceName}' installed successfully.");
        Console.WriteLine($"  Start type: Automatic (Delayed Start)");
        Console.WriteLine($"  Config: {ServiceConfig.DefaultConfigPath()}");
        Console.WriteLine();
        Console.WriteLine("  To start now:  AgentInboxService start");
        Console.WriteLine("  To configure:  AgentInboxService config show");

        return 0;
    }

    private static int Uninstall()
    {
        // Stop first if running
        StopService();
        var result = RunSc($"delete {ServiceName}");
        if (result == 0)
            Console.WriteLine($"Service '{ServiceName}' removed.");
        return result;
    }

    private static int StartService()
    {
        Console.WriteLine($"Starting {ServiceName}...");
        return RunSc($"start {ServiceName}");
    }

    private static int StopService()
    {
        Console.WriteLine($"Stopping {ServiceName}...");
        return RunSc($"stop {ServiceName}");
    }

    private static int ShowStatus()
    {
        Console.WriteLine($"=== {DisplayName} ===\n");

        // Service status via sc query
        try
        {
            var sc = new ServiceController(ServiceName);
            Console.WriteLine($"  Service status:  {sc.Status}");
            Console.WriteLine($"  Start type:      {sc.StartType}");
        }
        catch (InvalidOperationException)
        {
            Console.WriteLine("  Service status:  NOT INSTALLED");
        }

        Console.WriteLine();

        // Config summary
        var configPath = ServiceConfig.DefaultConfigPath();
        Console.WriteLine($"  Config file:     {configPath}");

        if (File.Exists(configPath))
        {
            var cfg = ServiceConfig.Load(configPath);
            Console.WriteLine($"  Enabled:         {cfg.Enabled}");
            Console.WriteLine($"  Python:          {cfg.ResolvedPythonPath}");
            Console.WriteLine($"  Script:          {cfg.ResolvedScriptPath}");
            Console.WriteLine($"  Working dir:     {cfg.ResolvedWorkingDirectory}");
            Console.WriteLine($"  Arguments:       {(string.IsNullOrEmpty(cfg.Arguments) ? "(none)" : cfg.Arguments)}");
            Console.WriteLine($"  Restart:         {cfg.RestartOnCrash} (delay={cfg.RestartDelaySeconds}s, max={cfg.MaxRestarts}/{cfg.MaxRestartWindowMinutes}min)");
            Console.WriteLine($"  Log dir:         {cfg.ResolvedLogDirectory}");
            Console.WriteLine($"  Extra PATH:      {(string.IsNullOrEmpty(cfg.ExtraPath) ? "(none)" : cfg.ExtraPath)}");

            // Check Python exists
            if (!File.Exists(cfg.ResolvedPythonPath))
                Console.WriteLine($"\n  ⚠ Python not found at: {cfg.ResolvedPythonPath}");
            // Only check script path as file when not in module mode
            if (!cfg.ScriptPath.StartsWith("-") && !File.Exists(cfg.ResolvedScriptPath))
                Console.WriteLine($"  ⚠ Script not found at: {cfg.ResolvedScriptPath}");
        }
        else
        {
            Console.WriteLine("  (config file not found — defaults will be used)");
        }

        // Check for daemon process
        Console.WriteLine();
        try
        {
            var daemons = Process.GetProcessesByName("python")
                .Concat(Process.GetProcessesByName("python3"))
                .Where(p =>
                {
                    try { return p.MainModule?.FileName?.Contains("python", StringComparison.OrdinalIgnoreCase) == true; }
                    catch { return false; }
                });

            Console.WriteLine($"  Python procs:    {daemons.Count()} running");
        }
        catch
        {
            Console.WriteLine("  Python procs:    (unable to check)");
        }

        return 0;
    }

    // ---------------------------------------------------------------
    // Config management
    // ---------------------------------------------------------------

    private static int HandleConfig(string[] args)
    {
        if (args.Length == 0)
            return HandleConfig(["show"]);

        return args[0].ToLowerInvariant() switch
        {
            "show" => ConfigShow(),
            "set" when args.Length >= 3 => ConfigSet(args[1], string.Join(' ', args.Skip(2))),
            "set" => PrintUsage("config set requires <key> <value>"),
            "path" => ConfigPath(),
            "reset" => ConfigReset(),
            _ => PrintUsage($"Unknown config command: {args[0]}"),
        };
    }

    private static int ConfigShow()
    {
        var path = ServiceConfig.DefaultConfigPath();
        if (File.Exists(path))
        {
            Console.WriteLine(File.ReadAllText(path));
        }
        else
        {
            Console.WriteLine("(no config file — showing defaults)");
            var cfg = new ServiceConfig();
            cfg.Save(path);
            Console.WriteLine(File.ReadAllText(path));
        }
        return 0;
    }

    private static int ConfigSet(string key, string value)
    {
        var path = ServiceConfig.DefaultConfigPath();
        var cfg = ServiceConfig.Load(path);

        var prop = typeof(ServiceConfig).GetProperties()
            .FirstOrDefault(p => p.Name.Equals(key, StringComparison.OrdinalIgnoreCase));

        if (prop is null)
        {
            Console.Error.WriteLine($"Unknown config key: {key}");
            Console.Error.WriteLine("Valid keys: " + string.Join(", ",
                typeof(ServiceConfig).GetProperties()
                    .Where(p => p.CanWrite)
                    .Select(p => p.Name)));
            return 1;
        }

        try
        {
            object converted = prop.PropertyType switch
            {
                var t when t == typeof(bool) => bool.Parse(value),
                var t when t == typeof(int) => int.Parse(value),
                _ => value,
            };

            prop.SetValue(cfg, converted);
            cfg.Save(path);
            Console.WriteLine($"  {prop.Name} = {value}");
            Console.WriteLine($"  Saved to {path}");
            Console.WriteLine("\n  Restart the service for changes to take effect:");
            Console.WriteLine("    AgentInboxService stop && AgentInboxService start");
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"Invalid value for {key}: {ex.Message}");
            return 1;
        }

        return 0;
    }

    private static int ConfigPath()
    {
        Console.WriteLine(ServiceConfig.DefaultConfigPath());
        return 0;
    }

    private static int ConfigReset()
    {
        var path = ServiceConfig.DefaultConfigPath();
        new ServiceConfig().Save(path);
        Console.WriteLine($"Config reset to defaults at {path}");
        return 0;
    }

    // ---------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------

    private static int RunSc(string arguments)
    {
        var psi = new ProcessStartInfo("sc.exe", arguments)
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };

        var proc = Process.Start(psi)!;
        var stdout = proc.StandardOutput.ReadToEnd();
        var stderr = proc.StandardError.ReadToEnd();
        proc.WaitForExit();

        if (!string.IsNullOrWhiteSpace(stdout))
            Console.WriteLine(stdout.TrimEnd());
        if (!string.IsNullOrWhiteSpace(stderr))
            Console.Error.WriteLine(stderr.TrimEnd());

        return proc.ExitCode;
    }
}
