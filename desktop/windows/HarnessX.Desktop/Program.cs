namespace HarnessX.Desktop;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--smoke-test")
        {
            return RunSmokeTestAsync().GetAwaiter().GetResult();
        }

        ApplicationConfiguration.Initialize();
        Application.Run(new MainForm());
        return 0;
    }

    private static async Task<int> RunSmokeTestAsync()
    {
        var root = Path.Combine(
            Path.GetTempPath(),
            $"harness-x-desktop-smoke-{Guid.NewGuid():N}");
        try
        {
            Directory.CreateDirectory(root);
            var paths = new DesktopPaths(
                root,
                Path.Combine(root, "AppServer"),
                Path.Combine(root, "WebView2"),
                Path.Combine(root, "app-server-executable.txt"));

            var appServerExecutable = DesktopRuntimeLocator.TryResolve(paths);
            if (appServerExecutable is null)
            {
                return 4;
            }

            if (string.Equals(
                    Path.GetFileName(appServerExecutable),
                    "harness-x-app-server.exe",
                    StringComparison.OrdinalIgnoreCase))
            {
                if (!DesktopRuntimeLocator.TryRemember(paths, appServerExecutable))
                {
                    return 5;
                }

                var configured = Environment.GetEnvironmentVariable("HARNESS_X_APP_SERVER_EXECUTABLE");
                var pathValue = Environment.GetEnvironmentVariable("PATH");
                try
                {
                    Environment.SetEnvironmentVariable("HARNESS_X_APP_SERVER_EXECUTABLE", null);
                    Environment.SetEnvironmentVariable("PATH", string.Empty);
                    var remembered = DesktopRuntimeLocator.TryResolve(paths);
                    if (!string.Equals(remembered, appServerExecutable, StringComparison.OrdinalIgnoreCase))
                    {
                        return 6;
                    }
                }
                finally
                {
                    Environment.SetEnvironmentVariable("HARNESS_X_APP_SERVER_EXECUTABLE", configured);
                    Environment.SetEnvironmentVariable("PATH", pathValue);
                }
            }

            await using var server = await AppServerChild.StartAsync(
                appServerExecutable,
                paths.AppServerRoot);
            if (!DesktopUriPolicy.IsAllowedNavigation(server.BootstrapUri.AbsoluteUri, server.BaseUri))
            {
                return 2;
            }
            if (DesktopUriPolicy.IsAllowedNavigation("https://example.com/", server.BaseUri))
            {
                return 3;
            }
            await server.StopAsync();
            return 0;
        }
        catch (Exception exc)
        {
            Console.Error.WriteLine(exc);
            return 1;
        }
        finally
        {
            try
            {
                Directory.Delete(root, recursive: true);
            }
            catch
            {
            }
        }
    }
}
