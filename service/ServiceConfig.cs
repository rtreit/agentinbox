namespace AgentInboxService;

using System.Text.Json;
using System.Text.Json.Serialization;

public class ServiceConfig
{
    /// <summary>
    /// Path to python.exe. Relative paths are resolved from WorkingDirectory.
    /// Use .venv\Scripts\python.exe to pick up the virtual environment.
    /// </summary>
    [JsonPropertyName("pythonPath")]
    public string PythonPath { get; set; } = @".venv\Scripts\python.exe";

    /// <summary>
    /// Python arguments for running the daemon (e.g. "-m agentinbox daemon" for module mode,
    /// or a script path like "src\daemon.py").
    /// </summary>
    [JsonPropertyName("scriptPath")]
    public string ScriptPath { get; set; } = "-m agentinbox daemon";

    /// <summary>
    /// Working directory for the daemon process.
    /// Relative paths are resolved from the parent of the service exe directory (the repo root).
    /// </summary>
    [JsonPropertyName("workingDirectory")]
    public string WorkingDirectory { get; set; } = ".";

    /// <summary>
    /// Extra command-line arguments appended after scriptPath.
    /// </summary>
    [JsonPropertyName("arguments")]
    public string Arguments { get; set; } = "";

    /// <summary>
    /// Whether to restart the daemon if it exits unexpectedly.
    /// </summary>
    [JsonPropertyName("restartOnCrash")]
    public bool RestartOnCrash { get; set; } = true;

    /// <summary>
    /// Base seconds to wait before restarting after a crash (doubles on each consecutive restart).
    /// </summary>
    [JsonPropertyName("restartDelaySeconds")]
    public int RestartDelaySeconds { get; set; } = 5;

    /// <summary>
    /// Maximum number of restarts within the restart window before giving up.
    /// </summary>
    [JsonPropertyName("maxRestarts")]
    public int MaxRestarts { get; set; } = 10;

    /// <summary>
    /// Time window (minutes) for counting restarts. Counter resets after this period.
    /// </summary>
    [JsonPropertyName("maxRestartWindowMinutes")]
    public int MaxRestartWindowMinutes { get; set; } = 30;

    /// <summary>
    /// Directory for service log files. Relative paths resolve from WorkingDirectory.
    /// </summary>
    [JsonPropertyName("logDirectory")]
    public string LogDirectory { get; set; } = "logs";

    /// <summary>
    /// Master switch — if false the service starts but does not launch the daemon.
    /// </summary>
    [JsonPropertyName("enabled")]
    public bool Enabled { get; set; } = true;

    /// <summary>
    /// Extra directories to prepend to PATH when launching the daemon.
    /// The service runs as SYSTEM and won't have the user's PATH.
    /// </summary>
    [JsonPropertyName("extraPath")]
    public string ExtraPath { get; set; } = "";

    /// <summary>
    /// User profile directory (e.g. "C:\Users\randyt") to map into the daemon
    /// environment when the service runs as SYSTEM.  When set, USERPROFILE, HOME,
    /// APPDATA, LOCALAPPDATA etc. are pointed at this directory so that tools like
    /// Copilot CLI can find auth tokens and configuration.
    /// If empty, the profile is inferred from the working directory path (requires
    /// the path to be under C:\Users\).
    /// </summary>
    [JsonPropertyName("userProfile")]
    public string UserProfile { get; set; } = "";

    // --- helpers ---

    private static readonly JsonSerializerOptions s_jsonOpts = new()
    {
        WriteIndented = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
    };

    /// <summary>
    /// Resolve the working directory. Relative paths are resolved from the parent
    /// of the service exe directory (i.e. the repo root when the exe lives in service/).
    /// </summary>
    public string ResolvedWorkingDirectory
    {
        get
        {
            if (Path.IsPathRooted(WorkingDirectory))
                return WorkingDirectory;

            var serviceDir = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
            var parent = Path.GetDirectoryName(serviceDir) ?? serviceDir;
            return Path.GetFullPath(Path.Combine(parent, WorkingDirectory));
        }
    }

    /// <summary>Resolve a potentially-relative path against WorkingDirectory.</summary>
    public string ResolvePath(string path)
    {
        if (Path.IsPathRooted(path))
            return path;
        return Path.GetFullPath(Path.Combine(ResolvedWorkingDirectory, path));
    }

    public string ResolvedPythonPath => ResolvePath(PythonPath);

    /// <summary>
    /// Resolved script path. When scriptPath starts with "-" (module mode, e.g. "-m agentinbox daemon"),
    /// returns the raw value since it is not a file system path.
    /// </summary>
    public string ResolvedScriptPath =>
        ScriptPath.StartsWith("-") ? ScriptPath : ResolvePath(ScriptPath);

    public string ResolvedLogDirectory => ResolvePath(LogDirectory);

    public static ServiceConfig Load(string path)
    {
        if (!File.Exists(path))
            return new ServiceConfig();

        var json = File.ReadAllText(path);
        return JsonSerializer.Deserialize<ServiceConfig>(json, s_jsonOpts) ?? new ServiceConfig();
    }

    public void Save(string path)
    {
        var json = JsonSerializer.Serialize(this, s_jsonOpts);
        File.WriteAllText(path, json);
    }

    /// <summary>Find agentinbox-service.json next to the running executable.</summary>
    public static string DefaultConfigPath()
    {
        var dir = AppContext.BaseDirectory;
        return Path.Combine(dir, "agentinbox-service.json");
    }
}
