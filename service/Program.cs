using AgentInboxService;

// CLI mode: if run with subcommands, act as config tool
if (args.Length > 0 && !args[0].StartsWith("--"))
{
    return CliTool.Run(args);
}

// Service mode
var builder = Host.CreateApplicationBuilder(args);
builder.Services.AddWindowsService(options =>
{
    options.ServiceName = "AgentInboxDaemon";
});
builder.Services.AddHostedService<DaemonWorker>();

var host = builder.Build();
host.Run();
return 0;
